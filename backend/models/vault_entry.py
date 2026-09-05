from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class VaultEntry(Base):
    """Zero-Knowledge Vault Entry in PostgreSQL.

    CRITICAL SECURITY & PRIVACY INVARIANTS:
    - TRUE ANONYMIZATION: No foreign keys to users, teams, servers or any personal entities.
    - NO METADATA: No cleartext service name, no URLs, no usernames, no tags.
    - BLIND BUCKET: Entries are isolated in a blinded `bucket_id` (64-char hex)
      derived client-side from the user's master key material. The backend is completely
      blind to what is stored and who owns it.
    - CIPHERTEXT: Authenticated AES-GCM envelope (`sv-vault-v1:...`).
    - REVISION & TOMBSTONES: Monotonic revision for sync conflict resolution,
      `is_deleted` flag for syncing deletions.
    """

    __tablename__ = "vault_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_gen_uuid)
    bucket_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ciphertext: Mapped[Text] = mapped_column(Text, default="", nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_vault_entries_bucket_revision", "bucket_id", "revision"),
    )
