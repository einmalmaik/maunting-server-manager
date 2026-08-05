"""Backend-erzwungene KI-Kontingente pro globaler Rolle.

``None`` bedeutet bei einem vorhandenen Datensatz bewusst „unbegrenzt“.
Fehlt der Datensatz dagegen vollständig, trägt die Rolle ein sicheres Limit
von 0 bei. Damit schaltet ein neues Recht nicht versehentlich Kosten frei.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RoleAiLimit(Base):
    """Speichert die konfigurierten KI-Limits genau einer Rolle."""

    __tablename__ = "role_ai_limits"

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    concurrent_operations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_cost_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
