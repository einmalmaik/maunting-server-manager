from datetime import datetime, timezone
import uuid as uuid_module

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid_module.uuid4()),
        unique=True,
        nullable=False,
        index=True,
    )
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string representation
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    server: Mapped["Server"] = relationship("Server", back_populates="incidents")


class GuardianIncidentDelivery(Base):
    __tablename__ = "guardian_incident_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    incident_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    incident_id: Mapped[int] = mapped_column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    incident: Mapped["Incident"] = relationship("Incident")
    server: Mapped["Server"] = relationship("Server")
