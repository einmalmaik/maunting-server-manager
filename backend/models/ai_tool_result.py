"""Persistierte Read-Tool-Ergebnisse einer Unterhaltung.

Ohne diese Tabelle lebte ein Tool-Ergebnis nur waehrend eines einzigen Streams:
`ai_stream_service` haengte es an eine lokale Liste, gespeichert wurde
ausschliesslich der Antworttext. Eine Rueckfrage im selben Chat sah den soeben
gelesenen Log oder die gelesene Config nicht mehr — das Modell musste sie neu
holen oder ohne sie antworten.

Der Inhalt ist bereits redigiert (`redact_sensitive_text`) und wird beim
Wiedereinspeisen ausdruecklich als unvertrauenswuerdig gekennzeichnet.
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
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
