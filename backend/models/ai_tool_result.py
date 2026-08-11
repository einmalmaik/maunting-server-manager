"""Persistierte Read-Tool-Ergebnisse einer Unterhaltung.

Ohne diese Tabelle lebte ein Tool-Ergebnis nur waehrend eines einzigen Streams:
`ai_stream_service` haengte es an eine lokale Liste, gespeichert wurde
ausschliesslich der Antworttext. Eine Rueckfrage im selben Chat sah den soeben
gelesenen Log oder die gelesene Config nicht mehr — das Modell musste sie neu
holen oder ohne sie antworten.

Der Inhalt ist bereits redigiert (`redact_sensitive_text`) und wird beim
Wiedereinspeisen ausdruecklich als unvertrauenswuerdig gekennzeichnet.

`run_id` haelt fest, zu welchem Lauf ein Ergebnis gehoert. Die Spalte macht aus
"die letzten sechs Ergebnisse dieser Unterhaltung" ein "die Ergebnisse der
letzten Anfrage": eine Unterhaltung laeuft dauerhaft und wechselt dabei das
Thema, ein Lauf nicht. Ohne sie trug der Rueckfluss den gelesenen Log von
Server A noch in die Frage nach Server B — und den Text eines gelesenen Skills
in jeden folgenden Zug.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiToolResult(Base):
    __tablename__ = "ai_tool_results"
    __table_args__ = (
        Index("ix_ai_tool_results_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    # `SET NULL` und nicht `CASCADE`: ein Ergebnis ist ein Beleg der
    # Unterhaltung und gehoert ihr, nicht dem Lauf. Verschwindet der Lauf, soll
    # der Beleg bleiben und nur seine Zuordnung verlieren — dieselbe Regel wie
    # bei `ai_action_proposals.server_id` und `ai_runs.last_server_id`.
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
