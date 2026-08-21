from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class RefreshToken(Base):
    """Serverseitig gespeicherte Refresh-Tokens fuer Token-Rotation und Revocation."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    family: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Von welcher Art Client diese Sitzung stammt: "desktop" fuer ein
    # gekoppeltes Geraet (Smart System), `None` fuer den Browser. Steht hier und
    # nicht nur im Access-Token, weil die Rotation in `/api/auth/refresh` das
    # Token neu baut und die Herkunft sonst beim ersten Erneuern verloren ginge.
    # Sie entscheidet, ob die KI die Werkzeuge des Rechners ueberhaupt
    # angeboten bekommt (`ai_tool_registry.herkunft_schnitt`).
    geraet: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")
