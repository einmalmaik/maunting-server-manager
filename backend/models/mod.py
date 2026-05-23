from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Mod(Base):
    __tablename__ = "mods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False)
    workshop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_order: Mapped[int | None] = mapped_column(Integer, default=0)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    dependencies_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of workshop IDs

    server: Mapped["Server"] = relationship("Server", back_populates="mods")
