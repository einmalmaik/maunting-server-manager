"""Persistente, benutzergebundene AI-Gespraeche."""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Die Arten von Unterhaltungen, die es geben darf.
#:
#: ``primary`` ist der Dauerchat: der eine Gespraechspartner, in den der Mensch
#: tippt und in dem ein faellig gewordener stehender Auftrag berichtet.
#: ``guardian`` ist der Hintergrund: dort und nur dort schreiben die Laeufe, die
#: eine Guardian-Stoerung ausgeloest hat.
#: ``worker`` ist ein Auftrag: jede vom Gehirn deklarierte Arbeit bekommt ihr
#: eigenes Fenster — davon gibt es je Benutzer beliebig viele gleichzeitig,
#: eines je Auftrag (docs/agentic-framework.md, v3).
#:
#: Die Trennung ist keine Ordnungsfrage, sondern der Grund, warum ueberhaupt
#: repariert werden kann. Solange beides in derselben Zeile stand, galt: eine
#: Heilung startete nicht, solange der Mensch etwas laufen hatte, und eine
#: getippte Nachricht loeste eine laufende Heilung ab (``vorgaenger_abloesen``
#: greift je Unterhaltung). Wer nachts einen Server repariert bekommen wollte,
#: durfte tagsueber nicht mit der KI reden.
#:
#: Diese Aufzaehlung ist die Wahrheitsquelle: der CheckConstraint unten wird
#: daraus **erzeugt**, `ai_chat_service` nimmt sie entgegen. Wer eine weitere Art
#: braucht, traegt sie hier ein — und bekommt die Schranke geschenkt.
ARTEN = ("primary", "guardian", "worker")

#: Die Arten, von denen es je Benutzer hoechstens **eine** Unterhaltung gibt.
#: Der eindeutige Index unten wird aus dieser Aufzaehlung erzeugt; ``worker``
#: steht bewusst nicht darin — mehrere gleichzeitige Auftraege sind der Zweck.
EINZELFENSTER = ("primary", "guardian")


class AiConversation(Base):
    """Eine dauerhafte Unterhaltung eines Benutzers mit dem Assistenten.

    Fuer die Arten in ``EINZELFENSTER`` gilt: genau eine Zeile je Benutzer und
    Art — erzwungen ueber den eindeutigen (partiellen) Index
    ``uq_ai_conversations_user_kind``. Es sind bewusst nicht beliebig viele:
    der Assistent soll wie ein Gespraechspartner funktionieren und nicht wie
    eine Ablage, in der man erst den richtigen Ordner suchen muss. Was hier
    aufgeteilt wird, ist deshalb kein Ordner, sondern ein *Anlass* — der Mensch
    fragt, eine Stoerung weckt, oder das Gehirn deklariert einen Auftrag.
    ``worker``-Zeilen sind von der Eindeutigkeit ausgenommen: ein Fenster je
    Auftrag, mehrere gleichzeitig.

    Der Serverbezug haengt nicht hier, sondern am einzelnen Werkzeugaufruf: ein
    Assistent, dem man erst sagen muss, welchen Server er ansehen darf, bevor man
    ihn ueberhaupt fragen kann, ist keiner. ``server_id`` bleibt nur als Spalte
    bestehen, damit die Migration nichts loeschen muss; sie ist immer ``NULL``.
    Auch die Guardian-Unterhaltung bekommt sie **nicht** gesetzt: sie gehoert dem
    Benutzer und sammelt die Reparaturen aller seiner Anlagen; um welchen Server
    es in einem Lauf ging, steht an ``ai_runs.last_server_id`` und im Laufrahmen.
    """

    __tablename__ = "ai_conversations"
    __table_args__ = (
        # Erzeugt statt abgeschrieben — dasselbe Muster wie `ZUSTAENDE` in
        # `ai_run.py`. Die Migration traegt ihre eigene Kopie: eine angewandte
        # Migration ist Geschichte und wird nicht nachtraeglich umgeschrieben.
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{art}'" for art in ARTEN) + ")",
            name="ck_ai_conversations_kind",
        ),
        Index("ix_ai_conversations_user_updated", "user_id", "updated_at"),
        # Partiell: Eindeutigkeit gilt nur fuer die EINZELFENSTER-Arten.
        # Worker-Fenster gibt es je Benutzer beliebig oft — die Kappe dafuer
        # liegt im Werkzeug-Handler und beim Betreiber-Deckel, nicht im Schema.
        Index(
            "uq_ai_conversations_user_kind",
            "user_id",
            "kind",
            unique=True,
            sqlite_where=text(
                "kind IN (" + ", ".join(f"'{art}'" for art in EINZELFENSTER) + ")"
            ),
            postgresql_where=text(
                "kind IN (" + ", ".join(f"'{art}'" for art in EINZELFENSTER) + ")"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Welcher Anlass in diese Zeile schreibt. Siehe `ARTEN`.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="primary", server_default="primary"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    # Nur eine spaetere, explizit minimierte Zusammenfassung; nie Provider-Interna.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Bis hierhin ist die Historie in `summary` zusammengefasst. Nachrichten
    # davor fliessen nicht mehr einzeln in eine Anfrage (Kontextkompression).
    summarized_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AiMessage(Base):
    """Eine persistierte Benutzer- oder Assistenten-Nachricht."""

    __tablename__ = "ai_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_ai_messages_role"),
        CheckConstraint(
            "status IN ('complete', 'streaming', 'failed')",
            name="ck_ai_messages_status",
        ),
        Index("ix_ai_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Denkschritte des Modells, falls es welche geliefert hat. Bewusst eine
    # eigene Spalte: sie sind eine Nebenausgabe, die der Benutzer aufklappen
    # kann, und duerfen nicht in eine Folgeanfrage zurueckfliessen. In `content`
    # waeren sie von der eigentlichen Antwort nicht mehr unterscheidbar.
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Die Rueckfrage, die diese Nachricht gestellt hat — bereits geprueft und
    # redigiert durch `question_payload()`, als {"question": ..., "options": [...]}.
    #
    # Sie gehoert an die Nachricht und nicht in ein fluechtiges Ereignis. Vorher
    # lebte sie nur im SSE-Strom: der Chat zeigte eine leere Blase mit "Keine
    # Antwort erhalten", nach einem Neuladen war die Frage weg, und — das
    # Schwerste — **das Modell sah seine eigene Frage in der Historie nicht**.
    # Auf die Antwort "Server.properties" folgte deshalb dieselbe Frage erneut.
    question_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Die Gliederung dieser Antwort: Text, Werkzeuge und Denkschritte in der
    # Reihenfolge, in der sie entstanden sind, als [{"art": "text", "inhalt":
    # ...}, {"art": "tool", "werkzeug": {...}}, {"art": "denken", "inhalt":
    # ...}, ...].
    #
    # `reasoning` daneben ist die Ableitung aus den Denkabschnitten, so wie
    # `content` die Ableitung aus den Textabschnitten ist — kein zweiter
    # Speicher. Der Denktext steht hier bereits geschwärzt (`_finalize_stream`).
    #
    # Aus demselben Grund hier wie `question_json` daneben, und aus demselben
    # gemeldeten Anlass: was nur im Ereignisstrom lebte, war nach einem
    # Neuladen weg. Der Betreiber hat genau das benannt — "man sieht die
    # Tool-Uses nur waehrenddessen".
    #
    # `content` bleibt daneben stehen und ist **kein** zweiter Speicher
    # derselben Sache: es ist der reine Text, der zum Anbieter zurueckgeht und
    # in die Zusammenfassung einfliesst. Die Abschnitte sind das, was der
    # Browser zeichnet. Wer nur eines von beiden haette, muesste das andere
    # raten — aus dem Text die Reihenfolge der Werkzeuge, oder aus den
    # Abschnitten einen Text, der Werkzeugchips enthaelt.
    #
    # `None` heisst "aus der Zeit vor dieser Spalte", nicht "keine Abschnitte".
    # Der Verlauf zeigt solche Nachrichten weiterhin als reinen Text.
    sections_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    provider_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
