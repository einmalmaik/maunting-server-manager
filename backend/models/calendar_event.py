from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_uid() -> str:
    return str(uuid.uuid4())


class CalendarEvent(Base):
    """Nativer Kalendereintrag in MSM.

    Wird für lokale / native Kalender verwendet, wenn kein externer CalDAV-Server
    angebunden ist oder der integrierte Kalender genutzt wird.
    """

    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    calendar_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_calendars.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Eindeutige UID für iCal-Export und KI-Referenzierung
    event_uid: Mapped[str] = mapped_column(
        String(64), default=_gen_uid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Die Wiederholungsregel — verschluesselt wie Titel, Beschreibung und Ort.
    #
    # `NOT NULL` und auf **jeder** Zeile gefuellt, auch bei Einzelterminen
    # (dort steht der verschluesselte Satz "keine Wiederholung"). Eine
    # `nullable`-Spalte haette durch blosses "gefuellt vs. leer" verraten,
    # welche Termine Serien sind — ohne dass jemand etwas entschluesseln muss.
    # Genau das war der Betreiberentscheid vom 22.09.2026: wer in die Datenbank
    # sieht, soll Serien von Einzelterminen nicht unterscheiden koennen.
    #
    # Der Preis steht in `CalendarService.get_events`: ohne lesbares Merkmal
    # kann der Server eine Serie von 1995 nicht in eine Abfrage fuer 2026
    # hineinfiltern. Ausgebreitet wird deshalb im Client (und fuer
    # Erinnerungen serverseitig, soweit der Server die Regel lesen kann).
    #
    # Leerer String heisst "Altbestand, noch nicht nachgezogen" — der Lesepfad
    # fuellt ihn nach, wie er es mit unverschluesselten Titeln auch tut. Das
    # verraet nichts ueber Wiederholungen, nur dass die Zeile aelter ist als
    # die Migration.
    recurrence: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)

    # Semantische Kategorie: personal, team, server, node
    event_type: Mapped[str] = mapped_column(
        String(32), default="personal", nullable=False, index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # Relationships
    calendar = relationship("UserCalendar", backref="events", lazy="joined")
    user = relationship("User", lazy="joined")
    team = relationship("Team", lazy="joined")
    server = relationship("Server", lazy="joined")
