from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, DateTime, Float, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from database import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of agent TLS cert DER (hex, no colons). Required for remote HTTPS nodes.
    tls_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    cpu_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    servers: Mapped[list["Server"]] = relationship("Server", back_populates="node")
