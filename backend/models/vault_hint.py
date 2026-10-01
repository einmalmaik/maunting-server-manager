from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultHint(Base):
    """Speichert den optionalen Passwort-Hinweis eines Benutzers für seinen Tresor.

    Wird beim Vergessen des Master-Passworts per E-Mail zugestellt.
    Rate-Limit: Maximal 1 Zustellung alle 10 Minuten.

    Das Konto steht nur als ``konto_index`` darin, wie in
    ``vault_user_settings``. Die AAD des Hinweises bleibt die Kontonummer
    (``msm:vault:hint:{user_id}``); sie steht nicht in der Datenbank.

    `hint` haelt den **verschluesselten** Hinweis und ist deshalb `Text`, nicht
    `String(512)`: die 512 sind die Grenze des Klartexts aus `VaultHintSetRequest`,
    und AES-GCM plus Base64 macht daraus rund 720 Zeichen (siehe Migration
    20260922_01).
    """

    __tablename__ = "vault_hints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    konto_index: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    # Altbestand von vor dem 01.10.2026, wird beim Umstellen auf NULL gesetzt.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hint: Mapped[str] = mapped_column(Text, nullable=False)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
