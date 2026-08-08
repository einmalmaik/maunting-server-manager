"""Persistente, bestaetigungspflichtige AI-Aktionsvorschlaege."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiActionProposal(Base):
    __tablename__ = "ai_action_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executing', 'succeeded', 'failed', 'expired')",
            name="ck_ai_action_proposals_status",
        ),
        Index("ix_ai_action_proposals_user_created", "user_id", "created_at"),
        Index("ix_ai_action_proposals_conversation_created", "conversation_id", "created_at"),
        # Traegt die Stundenbudget-Abfrage des autonomen Modus.
        Index(
            "ix_ai_action_proposals_autonomous_created",
            "user_id",
            "autonomous",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional: `propose_server_create` schlaegt einen Server vor, den es noch
    # nicht gibt. Nach erfolgreicher Ausfuehrung traegt der Vorschlag die neue ID.
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Tool-Payload kann Config-Inhalt enthalten und ist deshalb immer DIS-
    # verschluesselt. Preview enthaelt nur redigierte Metadaten/Diff-Zeilen.
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, nullable=False)
    expected_revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # `autonomous` haelt fest, dass niemand zugestimmt hat. Das ist eine andere
    # Aussage als `requires_confirmation=False` und gehoert deshalb ins Audit.
    autonomous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Zielpunkt 3.6: warum geaendert wird und welche Folgen erwartet werden.
    # Vom Modell geliefert, redigiert und laengenbegrenzt — eine Begruendung,
    # keine Zusicherung.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False, index=True)
    confirmation_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

