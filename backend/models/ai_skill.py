"""Versionierte AI-Skills aus einer festen MSM-Aktionsmenge."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiSkill(Base):
    __tablename__ = "ai_skills"
    __table_args__ = (
        UniqueConstraint("skill_key", "version", name="uq_ai_skills_key_version"),
        Index("ix_ai_skills_key_created", "skill_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
