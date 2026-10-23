"""Ein dauerhafter Ausweis für den Hintergrundkanal eines Geräts.

Warum es diese Tabelle gibt: Der Zugangstoken lebt 15 Minuten. Ein
Vordergrunddienst auf dem Handy, der sich damit verbindet, verstummt nach einer
Viertelstunde — genau in dem Fall, für den es ihn gibt. Er braucht einen
eigenen Ausweis, der länger hält.

Und warum er **eigener** ist: Der Dienst darf nicht denselben Refresh-Token
erneuern wie die App. Zwei Rotationen auf derselben Familie sperren sie, und
das Gerät wäre entkoppelt (Punkt 62). Dieser Ausweis hat deshalb keine
Rotation: er gilt, bis er widerrufen wird.

## Was in der Zeile steht — und was nicht

Der Ausweis selbst steht **nicht** hier. Gespeichert ist nur sein SHA-256; ein
Blick in die Datenbank verrät keinen gültigen Ausweis. Er wird einmal
ausgegeben (bei der Anlage) und danach nie wieder gezeigt.

`familie` bindet ihn an die Sitzung, die ihn angelegt hat. Damit räumt ein
Abmelden genau die Kanäle dieses Geräts ab und nicht die der anderen. Die
Verbindung, die der Dienst aufbaut, trägt `kanal:<id>` als Familie — so trifft
`SyncEventService.trenne` sie beim Widerruf und schickt ihr das Schlusssignal.

## Was er darf

Nur zuhören: den Ereignisstrom lesen, Präsenz melden, Mailboxen abonnieren.
Kein Schreibzugriff, kein Tresor, keine Kontodaten. Wer einen Ausweis erbeutet,
erfährt, dass etwas passiert — nicht, was.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationChannel(Base):
    """Ein Gerät, das auch bei geschlossener App zuhören darf."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        # Der Hash ist die Kennung, unter der gesucht wird — und er darf nur
        # einmal vorkommen.
        UniqueConstraint("token_hash", name="uq_notification_channels_token"),
        Index("ix_notification_channels_user", "user_id"),
        Index("ix_notification_channels_familie", "familie"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Die Refresh-Familie der Sitzung, die den Kanal angelegt hat. NULL nur
    #: bei Altbestand; dann räumt das Abmelden alle Kanäle des Kontos ab.
    familie: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Was der Mensch sieht („Telefon von Anna"), nie ein Token.
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: SHA-256 des Ausweises als Hex, immer 64 Zeichen.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Gesetzt heisst: gilt nicht mehr. Die Zeile bleibt für die Spur stehen.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
