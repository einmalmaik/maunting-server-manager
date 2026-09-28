from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PostgresUser(Base):
    __tablename__ = "postgres_users"
    __table_args__ = (
        UniqueConstraint("server_id", "username", name="uq_postgres_users_server_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    password_mask: Mapped[str] = mapped_column(String(64), nullable=False)
    # DIS, AAD ``msm:pg:user:<server_id>:<username>``. NULL bei Nutzern von vor
    # 09/2026 — deren Passwort wurde nie gespeichert; ein Rotieren fuellt es.
    password_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server = relationship("Server", back_populates="postgres_users")
    grants = relationship("PostgresGrant", back_populates="user", cascade="all, delete-orphan")
