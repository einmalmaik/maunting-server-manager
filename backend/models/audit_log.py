from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Text statt Integer: nicht jedes Ziel hat eine Zahl als Kennung. Memory,
    # Skills, Anhaenge und Aktionsvorschlaege sind UUIDs, und diese Aufrufe
    # gibt es seit Phase C. Auf SQLite fiel das nicht auf — es speichert einen
    # String klaglos in einer INTEGER-Spalte —, auf PostgreSQL scheiterte
    # dagegen jeder `remember`-Aufruf am Audit-Eintrag.
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Herkunft ist keine Autorisierungsquelle, sondern reine Nachvollziehbarkeit.
    # Die eigentliche Berechtigungsprüfung findet weiterhin vor der Aktion statt.
    origin: Mapped[str] = mapped_column(String(16), default="direct", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
