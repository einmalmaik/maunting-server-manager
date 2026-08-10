"""Ein Lauf: der Zug der KI als eigenstaendiges, dauerhaftes Ding.

Bis hierher war ein Zug der KI ein **Python-Generator an einem HTTP-Request**.
Er existierte nur, solange der Browser die Verbindung hielt. Alles, was im
Betrieb schieflief, hatte genau diese eine Ursache:

* Der Benutzer wechselt die Seite oder schliesst den Browser — der Zug stirbt
  mitten in der Arbeit, und die halbfertige Antwort wird als `failed`
  abgerechnet (frueher ``ai_stream_service`` im ``GeneratorExit``-Zweig).
* Die KI schlaegt eine Aktion vor und wartet. Der Mensch bestaetigt Minuten
  spaeter ueber einen **eigenen** HTTP-Endpunkt — von dem der Generator nichts
  erfaehrt, weil es ihn laengst nicht mehr gibt. Der Benutzer musste eine neue
  Nachricht schreiben, damit es weitergeht.
* Eine zusammengesetzte Bitte ("richte den Server ein, stell das ein, starte
  ihn und sag Bescheid") kann nicht zu Ende laufen, weil jede Unterbrechung
  das Ende ist.

Ein Lauf dreht das um: **die Arbeit ist das Dauerhafte, der Datenstrom ist nur
das Fenster darauf.** Der Lauf laeuft im Hintergrund weiter, ob jemand zusieht
oder nicht; ein Client kann sich jederzeit wieder anhaengen; und eine
Bestaetigung weckt ihn genau dort, wo er stehengeblieben ist.

Das Vorbild steht im selben Haus: die Server-Konsole haelt ihre Zeilen ebenfalls
unabhaengig von der WebSocket-Verbindung und liefert beim Wiederverbinden nach,
was fehlt (``console_stream_service``).
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


# Ein Lauf, der auf einen Menschen wartet, haelt keine Ressourcen — er ist eine
# Zeile. Diese beiden Zustaende sind deshalb bewusst nicht befristet: wer eine
# Bestaetigung eine Stunde liegen laesst, findet sie danach noch vor.
WARTEND = ("waiting_confirmation", "waiting_user")
# Endzustaende. Ab hier weckt nichts den Lauf mehr auf.
BEENDET = ("completed", "failed", "cancelled")
ZUSTAENDE = ("running", *WARTEND, *BEENDET)


class AiRun(Base):
    """Ein Lauf der KI, von der Bitte des Benutzers bis zur fertigen Arbeit.

    Ein Lauf umfasst **mehrere** Assistenten-Nachrichten, wenn er unterbrochen
    wurde: jede Fortsetzung nach einer Bestaetigung schreibt eine eigene
    Nachricht. Das ist Absicht — "ich stelle um, bitte bestaetigen" und "erledigt,
    der Server laeuft" sind zwei Aussagen und keine zerhackte.
    """

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'waiting_confirmation', 'waiting_user', "
            "'completed', 'failed', 'cancelled')",
            name="ck_ai_runs_status",
        ),
        # Fuer "hat dieser Benutzer gerade etwas laufen?" — die Frage, die die
        # Glocke und der Wiederanschluss stellen.
        Index("ix_ai_runs_user_status", "user_id", "status"),
        Index("ix_ai_runs_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")

    # Die Nachricht, die dieses Segment gerade schreibt. Nach einer Unterbrechung
    # zeigt sie auf die **neue** Nachricht der Fortsetzung; die vorherige bleibt
    # als abgeschlossene Aussage im Verlauf stehen.
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Das Arbeitsgedaechtnis der Schleife: die vollstaendige Nachrichtenliste an
    # den Anbieter plus die Zaehler des Budgets. Genau das, was frueher nur in
    # der lokalen Variablen `provider_messages` stand und mit dem Generator
    # verschwand — und der Grund, warum eine Fortsetzung bisher unmoeglich war.
    #
    # Es wird bewusst *nicht* aus der Unterhaltung neu abgeleitet: eine
    # Fortsetzung muss dieselben Werkzeugergebnisse sehen wie der abgebrochene
    # Zug, sonst faengt die KI von vorn an zu lesen.
    state_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Denkschritte fuer diesen Lauf angefordert? Gehoert zum Lauf und nicht zur
    # Nachricht, weil die Fortsetzung dieselbe Einstellung braucht.
    reasoning: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Warum der Lauf endete: 'done' | 'question' | 'awaiting_confirmation' |
    # 'budget' | ein Fehlercode. Der Text ist fuer Menschen und Protokoll, nicht
    # fuer Fallunterscheidungen — dafuer ist `status` da.
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
