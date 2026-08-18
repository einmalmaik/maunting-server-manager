"""Eine Meldung: was ein Worker dem Menschen zu sagen hat — als Zeile.

docs/agentic-framework.md (Abschnitt 4): Ergebnisse und Rueckfragen der Worker
graetschen nie ins Gespraech. Sie warten als Zeilen, bis Ruhe ist, und werden
dann **gebuendelt** vom Gehirn geliefert. Die Zeile ist die Zustellgarantie:
sie ueberlebt Neustarts, und die Marke ``zugestellt_at`` wird **vor** dem
Versand committet — kein Doppelversand, kein Verlust beim Absturz.

Der Text steht hier bereits **geschwaerzt**. `ai_meldestelle.melden()` ist der
eine Punkt, durch den jede Meldung geht; eine Zeile, die woanders entstuende,
truege moeglicherweise Klartext aus einem Worker-Lauf, der Logdateien fremder
Server gelesen hat.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Was eine Meldung sein kann. ``ergebnis`` ist der Abschlussbericht eines
#: Workers (auch ein gescheiterter — der sagt, was er geschafft hat und woran
#: er scheiterte). ``frage`` ist eine Rueckfrage: der Worker parkt, das Gehirn
#: stellt sie menschlich, und die Antwort weckt ueber ``worker_id`` genau ihn.
#:
#: Wahrheitsquelle wie ueberall: der CheckConstraint unten wird daraus erzeugt.
MELDUNGSARTEN = ("ergebnis", "frage")

#: Wohin gemeldet wird. Woertlich die Kanaele der stehenden Auftraege
#: (`models/ai_task.KANAELE`) — aber als eigene Kopie, damit eine dortige
#: Erweiterung nicht stillschweigend hier gilt. Chat ist nie abwaehlbar:
#: ``email`` heisst *zusaetzlich*, nie ausschliesslich.
KANAELE = ("chat", "email", "both")


class AiMeldung(Base):
    """Eine wartende oder zugestellte Wortmeldung an einen Benutzer.

    ``zugestellt_at`` ist die Doppelversand-Marke fuer den **Chat-Kanal**:
    ``NULL`` heisst „wartet auf Ruhe". Die Mail des ``email``-Kanals geht
    dagegen sofort beim Melden in den Ausgangskorb — sie unterbricht kein
    Gespraech, und der Korb traegt ab dort die Zustellung.
    """

    __tablename__ = "ai_meldungen"
    __table_args__ = (
        CheckConstraint(
            "art IN (" + ", ".join(f"'{art}'" for art in MELDUNGSARTEN) + ")",
            name="ck_ai_meldungen_art",
        ),
        CheckConstraint(
            "kanal IN (" + ", ".join(f"'{kanal}'" for kanal in KANAELE) + ")",
            name="ck_ai_meldungen_kanal",
        ),
        # Die Frage des Takts: "hat dieser Benutzer offene Meldungen?"
        Index("ix_ai_meldungen_user_zugestellt", "user_id", "zugestellt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Das Fenster des Workers, dem die Meldung entstammt — der Anker des
    # Frage-Routings. `SET NULL` statt `CASCADE`: die Meldung ist ein Beleg an
    # den Menschen und ueberlebt das Aufraeumen des Fensters; eine Frage ohne
    # Fenster ist dann schlicht nicht mehr beantwortbar.
    worker_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("ai_conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    art: Mapped[str] = mapped_column(String(16), nullable=False, default="ergebnis")
    kanal: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    # Bereits geschwaerzt (siehe Modul-Docstring). Der Kurztext, den das Gehirn
    # liefert — die Meldung ist das Ergebnis, nie der Prozess.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Bei ``art='frage'``: die geprueften Frage-Daten ({"question", "options"}),
    # dasselbe Format wie `AiMessage.question_json`.
    question_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    zugestellt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
