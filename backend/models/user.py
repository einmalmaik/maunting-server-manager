from datetime import datetime, timezone
import hashlib

from sqlalchemy import Boolean, String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # E-Mail: verschluesselt mit DIS (AES-256-GCM, AAD msm:user:email).
    # email_hash (SHA-256 mit Pepper) fuer SQL-Lookup (WHERE email_hash = ?).
    # email_plain ist die Legacy-Spalte (DB-Name "email"), nach Migration
    # nur noch Platzhalter (der Hash-Wert), keine Klartext-E-Mail mehr.
    email_plain: Mapped[str | None] = mapped_column("email", String(255), unique=True, index=True, nullable=True)
    email_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Globale Rolle (Phase 3 RBAC). NULL fuer Owner-Bootstrap akzeptabel, da is_owner alles bypassed.
    role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    two_factor_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True)

    password_reset_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    role: Mapped["Role | None"] = relationship("Role", back_populates="users")
    server_permissions: Mapped[list["ServerPermission"]] = relationship(
        "ServerPermission",
        foreign_keys="ServerPermission.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    backup_codes: Mapped[list["BackupCode"]] = relationship("BackupCode", back_populates="user", cascade="all, delete-orphan")

    # ── E-Mail Property (transparente DIS-Ver-/Entschluesselung) ──

    @staticmethod
    def _email_hash(email: str) -> str:
        """SHA-256(email + pepper) fuer SQL-Lookup. Pepper = settings.secret_key."""
        from config import settings
        return hashlib.sha256((email + settings.secret_key).encode()).hexdigest()

    @property
    def email(self) -> str | None:
        if self.email_encrypted:
            from services.dis_client import DisClient
            return DisClient.decrypt(self.email_encrypted, aad="msm:user:email")
        if self.email_plain:
            # Echte Pre-Migration Erkennung: falls email_plain ein SHA-256 Hash ist, handelt es sich
            # um eine bereits migrierte Zeile, bei der aber email_encrypted fehlt (Datenkorruption/Fehler).
            if len(self.email_plain) == 64 and all(c in "0123456789abcdefABCDEF" for c in self.email_plain):
                from services.dis_client import DisDecryptionError
                raise DisDecryptionError("Inconsistent database state: email_encrypted is missing but email_plain is hashed.")
            return self.email_plain
        return None

    @email.setter
    def email(self, value: str | None) -> None:
        if value:
            from services.dis_client import DisClient
            self.email_encrypted = DisClient.encrypt(value, aad="msm:user:email")
            self.email_hash = self._email_hash(value)
            # Platzhalter in Legacy-Spalte (NOT NULL in alten Schemas).
            # Nach Migration steht hier der Hash, keine Klartext-E-Mail.
            self.email_plain = self.email_hash
        else:
            self.email_encrypted = None
            self.email_hash = None
            self.email_plain = None
