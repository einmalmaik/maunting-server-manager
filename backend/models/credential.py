"""Benutzereigene Zugangsdaten und ihre Bindung an einen Server.

Panelweite Zugangsdaten bleiben unveraendert in `panel_settings` — das ist der
Self-Hosted-Standardfall und darf sich nicht aendern. Diese Tabellen ergaenzen
darunter zwei feinere Ebenen (Benutzer, Server), damit ein Hoster nicht jeden
Kundenserver mit dem zentralen Betreiberzugang laufen lassen muss.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


# Nur diese beiden Arten sind scoped. Der Steam-Web-API-Key bleibt bewusst
# panelweit: er dient Workshop-Metadatenabfragen, nicht dem Zugriff auf Daten
# eines einzelnen Kunden.
KIND_GITHUB_TOKEN = "github_token"
KIND_STEAM_ACCOUNT = "steam_account"
CREDENTIAL_KINDS = (KIND_GITHUB_TOKEN, KIND_STEAM_ACCOUNT)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserCredential(Base):
    """Ein vom Benutzer hinterlegtes Geheimnis.

    Mehrere Eintraege je Art sind erlaubt und ueber `label` unterscheidbar —
    jemand kann zwei Steam-Konten fuer verschiedene Spiele verwenden.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('github_token', 'steam_account')",
            name="ck_user_credentials_kind",
        ),
        UniqueConstraint("user_id", "kind", "label", name="uq_user_credentials_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nur bei Steam belegt.
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Nicht umkehrbarer Wiedererkennungshinweis, nie das Geheimnis selbst.
    secret_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class ServerCredentialBinding(Base):
    """Welches Credential ein Server fuer eine Art verwendet.

    Der Server verweist auf das Credential, statt dessen Wert zu kopieren. Damit
    wirkt eine Rotation sofort und der Klartext existiert weiterhin nur an einer
    einzigen, verschluesselten Stelle.
    """

    __tablename__ = "server_credential_bindings"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('github_token', 'steam_account')",
            name="ck_server_credential_bindings_kind",
        ),
    )

    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    credential_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_credentials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    credential: Mapped["UserCredential"] = relationship("UserCredential", lazy="joined")
