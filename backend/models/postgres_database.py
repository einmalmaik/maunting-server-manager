from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PostgresDatabase(Base):
    __tablename__ = "postgres_databases"
    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uq_postgres_databases_server_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    owner_role: Mapped[str] = mapped_column(String(63), nullable=False)
    owner_password_encrypted: Mapped[str] = mapped_column(String(4096), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    server = relationship("Server", back_populates="postgres_databases")
    grants = relationship("PostgresGrant", back_populates="database", cascade="all, delete-orphan")
