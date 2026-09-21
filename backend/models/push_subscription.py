"""Die Zustelladresse eines Browsers fuer WebPush.

Bis 09/2026 endete der Weg einer Benachrichtigung im Nichts: `sw.js` hatte
einen fertigen `push`-Listener, aber es gab keine Tabelle, in der ein Browser
haette sagen koennen, wohin zugestellt werden soll. Wer den Messenger schloss,
erfuhr von einer neuen Nachricht nichts.

Eine Zeile hier ist genau das, was der Browser beim Abonnieren herausgibt: die
vom Hersteller vergebene Endpunkt-Adresse und zwei oeffentliche Werte, gegen
die der Server verschluesselt. Beide sind fuer den Server nutzlos, ausser um
genau diesem Browser etwas zu schicken — den privaten Gegenpart haelt die
Push-Verwaltung des Browsers, nicht MSM.

**Der Endpunkt ist eindeutig, nicht das Konto.** Eine Adresse gehoert zu einer
Browserinstallation, und in einem geteilten Browser meldet sich morgen ein
anderes Konto an. Ohne diese Schranke lieferte der Server die
Benachrichtigungen des Vormieters weiter an denselben Browser; deshalb
uebernimmt eine erneute Anmeldung die Zeile, statt eine zweite anzulegen
(`webpush_service.eintragen`).

Ein Konto hat mehrere Zeilen, eine je Geraet. Das ist gewollt und derselbe
Gedanke wie bei `user_e2ee_devices`: zugestellt wird an Geraete, nicht an
Konten.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PushSubscription(Base):
    """Ein Browser, der Benachrichtigungen empfangen will."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        # Eine Endpunkt-Adresse gehoert zu genau einer Browserinstallation und
        # damit zu genau einer Zeile. Siehe Modulkopf: ohne das bekaeme ein
        # geteilter Browser die Benachrichtigungen des vorherigen Kontos.
        UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
        # Die eine Abfrage im Betrieb: "wohin fuer dieses Konto?"
        Index("ix_push_subscriptions_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Die Adresse des Push-Dienstes des Browserherstellers. Text und nicht
    # String(n), weil ihre Laenge nirgends zugesichert ist: FCM-Adressen liegen
    # bei rund 180 Zeichen, Mozilla vergibt kuerzere, und eine Grenze, die
    # heute passt, schneidet morgen still eine Adresse ab.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # Der oeffentliche P-256-Punkt des Browsers (unkomprimiert, 65 Bytes
    # base64url) und sein Authentifizierungsgeheimnis (16 Bytes base64url).
    # Beides kommt aus `PushSubscription.getKey()` im Browser und ist das, wogegen
    # RFC 8291 die Nutzlast verschluesselt.
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
