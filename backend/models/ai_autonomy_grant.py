"""Freigabe des autonomen KI-Modus, pro Server oder panelweit.

Zielpunkt 3.7 verlangt, dass der autonome Modus **ausdruecklich aktiviert**
werden muss und durch Rollen, Rechte und Sicherheitsgrenzen beschraenkt bleibt.
Die Berechtigung `ai.autonomous.use` allein reicht dafuer nicht: sie sagt, dass
ein Benutzer den Modus verwenden *darf*, nicht wo und wie viel.

Ein Grant ist deshalb die zweite, bewusste Handlung. `server_id = NULL` bedeutet
panelweit; ein Grant auf einen konkreten Server gewinnt, wenn beide existieren.

`max_actions_per_hour` ist die harte Obergrenze. Sie begrenzt nicht die
Berechtigung — die bleibt bei jeder einzelnen Aktion unveraendert geprueft —
sondern die Menge: ein in eine Schleife geratenes Modell soll nicht in einer
Minute vierzig Backups anstossen.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


DEFAULT_MAX_ACTIONS_PER_HOUR = 10
MAX_ACTIONS_PER_HOUR_LIMIT = 1_000


class AiAutonomyGrant(Base):
    __tablename__ = "ai_autonomy_grants"
    __table_args__ = (
        CheckConstraint(
            "max_actions_per_hour >= 0 AND max_actions_per_hour <= 1000",
            name="ck_ai_autonomy_grants_budget",
        ),
        UniqueConstraint("user_id", "server_id", name="uq_ai_autonomy_grants_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL = panelweit.
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_actions_per_hour: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_MAX_ACTIONS_PER_HOUR, nullable=False
    )
    granted_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
