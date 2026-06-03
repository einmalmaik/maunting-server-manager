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

    # IdP-seitige eindeutige User-ID (z. B. Google "sub", GitHub "id")
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Bei Login aktualisierte Profildaten (read-only cache, kein PII-Pflichtfeld)
    email_at_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username_at_link: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
