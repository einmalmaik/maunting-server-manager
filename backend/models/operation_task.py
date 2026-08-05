"""Persistenter Status sicherheitsrelevanter Backend-Aufgaben."""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OperationTask(Base):
    """Eine idempotente, vom Backend autorisierte fachliche Aufgabe.

    Absichtlich wird kein Request-Payload gespeichert: Task-Metadaten reichen
    für Status, Wiederholung und Audit aus und können keine Credentials leaken.
    """

    __tablename__ = "operation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_operation_tasks_status",
        ),
        CheckConstraint(
            "origin IN ('direct', 'ai', 'external', 'system')",
            name="ck_operation_tasks_origin",
        ),
        CheckConstraint("attempt >= 1", name="ck_operation_tasks_attempt"),
        UniqueConstraint(
            "actor_user_id",
            "task_type",
            "idempotency_key_hash",
            name="uq_operation_tasks_actor_type_idempotency",
        ),
        Index("ix_operation_tasks_actor_created", "actor_user_id", "created_at"),
        Index("ix_operation_tasks_server_created", "server_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="accepted")
    server_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    retry_of_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("operation_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Nur lokalisierbarer, stabiler Fehler-Key; nie rohe Exception-Texte.
    error_message: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
