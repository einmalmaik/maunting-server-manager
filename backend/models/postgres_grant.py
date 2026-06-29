from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PostgresGrant(Base):
    __tablename__ = "postgres_grants"
    __table_args__ = (
        UniqueConstraint("server_id", "database_id", "user_id", name="uq_postgres_grants_server_database_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    database_id: Mapped[int] = mapped_column(ForeignKey("postgres_databases.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("postgres_users.id", ondelete="CASCADE"), nullable=False)
    privilege: Mapped[str] = mapped_column(String(32), default="read_write")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    database = relationship("PostgresDatabase", back_populates="grants")
    user = relationship("PostgresUser", back_populates="grants")
