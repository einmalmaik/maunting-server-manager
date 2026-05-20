from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    game_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    install_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    linux_user: Mapped[str] = mapped_column(String(64), nullable=False)

    # Status
    status: Mapped[str] = mapped_column(String(32), default="stopped")  # stopped, running, installing, updating, error
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-Restart
    auto_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    restart_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restart_time_utc: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HH:MM

    # Ressourcen
    cpu_limit_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    permissions: Mapped[list["Permission"]] = relationship("Permission", back_populates="server", cascade="all, delete-orphan")
    backups: Mapped[list["Backup"]] = relationship("Backup", back_populates="server", cascade="all, delete-orphan")
    mods: Mapped[list["Mod"]] = relationship("Mod", back_populates="server", cascade="all, delete-orphan")
