from sqlalchemy import Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False)

    can_start: Mapped[bool] = mapped_column(Boolean, default=False)
    can_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    can_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    can_update: Mapped[bool] = mapped_column(Boolean, default=False)
    can_edit_config: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_mods: Mapped[bool] = mapped_column(Boolean, default=False)
    can_backup: Mapped[bool] = mapped_column(Boolean, default=False)
    can_restore: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_console: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_logs: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship("User", back_populates="permissions")
    server: Mapped["Server"] = relationship("Server", back_populates="permissions")
