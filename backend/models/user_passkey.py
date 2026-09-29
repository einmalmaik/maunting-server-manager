from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.dis_text import DisText


class UserPasskey(Base):
    """Ein Passkey als zweiter Faktor — der oeffentliche Schluessel, den der Server prueft.

    Bis 09/2026 gab es diese Tabelle nicht. Der „Passkey" war eine Abfrage im
    Browser, und der Server glaubte dem Feld `passkey_verified: true`, das der
    Browser danach schickte. Wer das Passwort kannte, schickte das Feld selbst.
    Jetzt unterschreibt das Geraet eine Einmal-Challenge des Servers, und
    geprueft wird gegen den Schluessel hier.

    `rp_id` ist der Host, unter dem der Passkey angelegt wurde. Ein Passkey gilt
    nur dort: im Browser unter der Panel-Adresse angelegt, taugt er in der
    Desktop-App (`tauri.localhost`) nicht.
    """

    __tablename__ = "user_passkeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Base64url der Credential-ID. Die Spezifikation erlaubt bis 1023 Byte.
    credential_id: Mapped[str] = mapped_column(String(1400), unique=True, nullable=False)
    # SubjectPublicKeyInfo (DER), Base64url. Oeffentlich — kein Geheimnis.
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    # COSE-Algorithmus: -7 ES256, -8 EdDSA, -257 RS256.
    algorithm: Mapped[int] = mapped_column(Integer, nullable=False)
    rp_id: Mapped[str] = mapped_column(String(253), nullable=False)
    # Vorzeichenloser 32-Bit-Zaehler — passt nicht in ein PostgreSQL-INTEGER.
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Vom Benutzer vergeben („Handy“, „PC Windows Hello“), damit er mehrere
    # Passkeys auseinanderhaelt. Nutzerinhalt, deshalb verschluesselt.
    name: Mapped[str | None] = mapped_column(DisText(aad="msm:auth:user_passkeys.name"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="passkeys")
