"""Idempotente Reservierungen für backendseitig erzwungene KI-Kontingente."""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiUsageEvent(Base):
    """Zählt eine logische KI-Anfrage dank eindeutiger Request-ID genau einmal."""

    __tablename__ = "ai_usage_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')",
            name="ck_ai_usage_events_status",
        ),
        CheckConstraint("accounted_tokens >= 0", name="ck_ai_usage_events_tokens"),
        CheckConstraint("reserved_tokens >= 0", name="ck_ai_usage_events_reserved_tokens"),
        CheckConstraint(
            "accounted_cost_microunits >= 0",
            name="ck_ai_usage_events_cost",
        ),
        CheckConstraint(
            "reserved_cost_microunits >= 0",
            name="ck_ai_usage_events_reserved_cost",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    server_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    provider_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "ai_providers.id",
            name="fk_ai_usage_events_provider_id_ai_providers",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True, nullable=False)
    # Bleibt für Idempotenzprüfungen unverändert, auch wenn die tatsächliche
    # Abrechnung nach dem Provider-Aufruf niedriger oder höher ausfällt.
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_cost_microunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accounted_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accounted_cost_microunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
