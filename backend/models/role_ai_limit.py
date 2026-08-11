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
    # Wie tief Benutzer dieser Rolle die KI nachdenken lassen dürfen — als Rang
    # aus `ai_reasoning.RANGFOLGE`: 0 = gar nicht, 1 = minimal … 6 = max.
    #
    # Ein Rang und kein Wort, damit die Grenze zu den übrigen Feldern dieser
    # Tabelle passt: `ai_limit_service._resolve_field` löst sie mit ``max()``
    # auf, samt der Regeln „None heißt unbegrenzt“ und „mehrere Rollen erhöhen“.
    # Ein Wort bräuchte eine zweite Auflösung neben dieser — und zwei
    # Auflösungen für dasselbe Rechtemodell driften auseinander.
    #
    # Warum ein Rang trotzdem reicht, obwohl jedes Modell andere Stufen kennt:
    # gewählt wird aus den echten Stufen des Modells, der Rang vergleicht nur.
    max_reasoning_effort: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
