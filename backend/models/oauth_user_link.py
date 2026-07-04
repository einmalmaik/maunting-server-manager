from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OAuthUserLink(Base):
    """Verknuepft eine IdP-Identitaet (provider + subject) mit einem MSM-User.

    Ein User kann mehrere Links haben (z. B. Google UND GitHub), aber pro
    Provider nur einen Link (subject-Id ist beim IdP eindeutig pro App).
    """

    __tablename__ = "oauth_user_links"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject", name="uq_oauth_user_links_provider_subject"),
        UniqueConstraint("provider_id", "user_id", name="uq_oauth_user_links_provider_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("oauth_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # IdP-seitige eindeutige User-ID (z. B. Google "sub", GitHub "id") - SHA-256 hashed
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    @staticmethod
    def _hash_subject(subject: str) -> str:
        from config import settings
        import hashlib
        return hashlib.sha256((subject + settings.secret_key).encode()).hexdigest()

    # Bei Login aktualisierte Profildaten (read-only cache, kein PII-Pflichtfeld)
    email_at_link_plain: Mapped[str | None] = mapped_column("email_at_link", String(255), nullable=True)
    email_at_link_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    username_at_link_plain: Mapped[str | None] = mapped_column("username_at_link", String(128), nullable=True)
    username_at_link_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    @property
    def email_at_link(self) -> str | None:
        if self.email_at_link_encrypted:
            from services.dis_client import DisClient
            try:
                return DisClient.decrypt(self.email_at_link_encrypted, aad="msm:oauth:link:email")
            except Exception:
                pass
        if self.email_at_link_plain:
            return self.email_at_link_plain
        return None

    @email_at_link.setter
    def email_at_link(self, value: str | None) -> None:
        if value:
            from services.dis_client import DisClient
            self.email_at_link_encrypted = DisClient.encrypt(value, aad="msm:oauth:link:email")
            self.email_at_link_plain = None
        else:
            self.email_at_link_encrypted = None
            self.email_at_link_plain = None

    @property
    def username_at_link(self) -> str | None:
        if self.username_at_link_encrypted:
            from services.dis_client import DisClient
            try:
                return DisClient.decrypt(self.username_at_link_encrypted, aad="msm:oauth:link:username")
            except Exception:
                pass
        if self.username_at_link_plain:
            return self.username_at_link_plain
        return None

    @username_at_link.setter
    def username_at_link(self, value: str | None) -> None:
        if value:
            from services.dis_client import DisClient
            self.username_at_link_encrypted = DisClient.encrypt(value, aad="msm:oauth:link:username")
            self.username_at_link_plain = None
        else:
            self.username_at_link_encrypted = None
            self.username_at_link_plain = None

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
