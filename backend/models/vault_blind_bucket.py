from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBlindBucket(Base):
    """Zero-Metadata, blind authorization store for password vault buckets.

    CRITICAL PRIVACY & SECURITY INVARIANTS:
    - NO USER LINKAGE: Contains NO user_id, no IP addresses, no foreign keys.
    - BLIND VERIFICATION: Stores only auth_verifier (SHA-256 of client-derived bucketAuthToken).
    - PREIMAGE RESISTANT: Server compromise or database leaks do not reveal the bucketAuthToken,
      nor can anyone map bucket_id back to user accounts or cleartext vault data.
    """

    __tablename__ = "vault_blind_buckets"

    bucket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    auth_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
