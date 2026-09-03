from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultUserSetting(Base):
    """User-specific Zero-Knowledge Vault configuration and authorization.

    SECURITY INVARIANTS:
    - Links a user account to their authorized blind `bucket_id`.
    - Prevents IDOR / unauthorized multi-tenant access to another user's vault bucket.
    - Persists the user's KDF salt for multi-device synchronization and offline recovery.
    """

    __tablename__ = "vault_user_settings"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    bucket_id: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )
    kdf_salt: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        onupdate=_now,
        nullable=False,
    )
