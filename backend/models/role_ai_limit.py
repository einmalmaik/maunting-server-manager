"""Backend-erzwungene KI-Kontingente pro globaler Rolle.

``None`` bedeutet in den Kontingentspalten bei einem vorhandenen Datensatz
bewusst „unbegrenzt“. Fehlt der Datensatz, trägt die Rolle nichts bei; ist gar
keine Rolle des Benutzers konfiguriert, gilt unbegrenzt. Die Auflösungsregeln
stehen einmalig in ``services/ai_limit_service``.

Das gilt seit dem 05.10.2026 auch für ``max_memory_entries``; vorher hieß NULL
dort „nichts hinterlegt“ und wurde beim Merken zur festen Systemgrenze.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RoleAiLimit(Base):
    """Speichert die konfigurierten KI-Limits genau einer Rolle."""

    __tablename__ = "role_ai_limits"

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Wie viele Minuten Sprachsitzung (Realtime, GPT-Live, Gemini Live) im
    # Monat. Bis zum 07.10.2026 stand hier ein Kostenbetrag; Minuten versteht
    # der Betreiber, ohne den Preis je Audiotoken zu kennen. Die Tokenlimits
    # gelten für Sprachsitzungen zusätzlich. Anfragen pro Minute, gleichzeitige
    # Vorgänge und das monatliche Kostenlimit sind am selben Tag entfallen
    # (Migration 20261008_03).
    monthly_realtime_minutes_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Monatliches Diktier- und Transkriptionszeitlimit in Minuten (provider-neutral).
    monthly_dictation_minutes_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Wie tief Benutzer dieser Rolle die KI nachdenken lassen dürfen — als Rang
    # aus `ai_reasoning.RANGFOLGE`: 0 = gar nicht, 1 = minimal … 6 = max.
    #
    # Ein Rang und kein Wort, damit die Grenze zu den übrigen Feldern dieser
    # Tabelle passt: `ai_limit_service._resolve_field` löst sie mit ``max()``
    # auf, samt der Regeln „None heißt unbegrenzt“ und „mehrere Rollen erhöhen“.
    # Ein Wort bräuchte eine zweite Auflösung neben dieser — und zwei
    # Auflösungen für dasselbe Rechtemodell driften auseinander.
    #
    # Warum ein Rang trotzdem reicht, obwohl jedes Modell andere Stufen kennt:
    # gewählt wird aus den echten Stufen des Modells, der Rang vergleicht nur.
    max_reasoning_effort: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Wieviele Memory-Einträge in einem Bereich stehen dürfen, dessen Vorrat an
    # dieser Rolle hängt. Hier stand „was Benutzer dieser Rolle je Bereich
    # anlegen dürfen“ — dieselbe Aussage, die in der Einstellungsmaske schon als
    # falsch korrigiert werden musste. Wem der Vorrat gehört, entscheidet der
    # Bereich und nicht der Schreibende: bei ``scope='team'`` liest
    # ``resolve_scope_memory_limit`` dieses Feld beim **Gründer** des Teams, und
    # für ``server_shared`` und ``panel`` wird die Spalte überhaupt nicht
    # gelesen. Wer sie anhand des alten Satzes auswertet, beantwortet „warum
    # schreibt mein Basic-Kunde 500 Einträge ins Team?“ mit „kann er nicht,
    # seine Rolle steht auf 5“ und meldet damit eine Zahl, die das Panel nicht
    # durchsetzt — genau der Fehler, vor dem der Absatz weiter unten warnt.
    #
    # Vorher war das eine Modulkonstante im Memory-Service und damit für jeden
    # gleich — ein Tarif konnte keinen größeren Wissensvorrat verkaufen, und der
    # Betreiber konnte ihn auch nicht kürzen.
    #
    # Kein Verbrauch, sondern ein Vorrat — aufgelöst wie die Spalten darüber:
    # der höchste Wert gewinnt, NULL heißt unbegrenzt. Warum der Deckel für
    # eine Zahl bewusst niedrig liegt, steht bei
    # ``ai_limit_service.MAX_MEMORY_ENTRIES_MAX``.
    max_memory_entries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
