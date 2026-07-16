from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class NodeEnrollment(Base):
    """Short-lived owner-approved remote-node enrollment."""

    __tablename__ = "node_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_code: Mapped[str] = mapped_column(String(9), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    tls_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
