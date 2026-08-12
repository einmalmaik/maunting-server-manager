"""Idempotente KI-Kontingentreservierung vor einem Provider-Aufruf.

Die User-Zeile wird in PostgreSQL gesperrt, damit parallele Anfragen desselben
Benutzers nicht beide dieselbe freie Quote sehen. Die eindeutige Request-ID
verhindert zusätzlich Doppelzählung bei Retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiMessage, AiUsageEvent, User
from services.ai_limit_service import (
    MONTHLY_COST_LIMIT_CENTS_MAX,
    TOKEN_LIMIT_MAX,
    resolve_effective_limits,
)

if TYPE_CHECKING:  # pragma: no cover
    # Nur fuer die Annotation. Zur Laufzeit importiert die Abrechnung den
    # Anbieteradapter nicht: sie bucht Zahlen, sie spricht kein Protokoll — und
    # die umgekehrte Richtung gibt es bereits.
    from services.openai_compatible_adapter import StreamUsage


ACTIVE_STATUSES = ("reserved", "completed")
# Die Waehrung der Abrechnung ist **USD**: OpenRouter meldet die tatsaechlichen
# Kosten in USD, und eine Umrechnung vor der Buchung waere eine zweite
# Fehlerquelle in genau der Zahl, die stimmen soll. In die Anzeigewaehrung geht
# es erst in der Oberflaeche (`services/ai_kosten.py`).
MICROUNITS_PER_CENT = 10_000
# Die Obergrenze jeder einzelnen Kostenangabe. Stand vorher an drei Stellen als
# Produkt ausgeschrieben — einmal in der Reservierung, einmal beim Abschluss,
# einmal beim Klemmen im Stream.
MAX_COST_MICROUNITS = MONTHLY_COST_LIMIT_CENTS_MAX * MICROUNITS_PER_CENT


class AiQuotaExceeded(ValueError):
    """Eine konkrete, nicht-sensible KI-Grenze ist ausgeschöpft."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"KI-Kontingent ausgeschöpft: {reason}")


class AiUsageConflict(ValueError):
    """Eine Request-ID wurde mit widersprüchlichen Daten wiederverwendet."""


def _matches_reservation(
    event: AiUsageEvent,
    *,
    user_id: int,
    server_id: int | None,
    provider_id: int | None,
    model: str | None,
    estimated_tokens: int,
    estimated_cost_microunits: int,
) -> bool:
    """Vergleicht den unveränderlichen Idempotenzvertrag einer Reservierung."""
    return (
        event.user_id == user_id
        and event.server_id == server_id
        and event.provider_id == provider_id
        and event.model == model
        and event.reserved_tokens == estimated_tokens
        and event.reserved_cost_microunits == estimated_cost_microunits
    )


def _canonical_request_id(request_id: str | UUID) -> str:
    """Akzeptiert ausschließlich kanonische UUIDs als Idempotency-Key."""
    try:
        return str(UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Ungültige AI-Request-ID") from exc


def _period_starts(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Berechnet UTC-Tages-, ISO-Wochen- und Monatsanfang."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    return day, week, month


def _sum_since(db: Session, user_id: int, since: datetime, column) -> int:
    value = (
        db.query(func.coalesce(func.sum(column), 0))
        .filter(
            AiUsageEvent.user_id == user_id,
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= since,
        )
        .scalar()
    )
    return int(value or 0)


def _ensure_within(limit: int | None, current: int, requested: int, reason: str) -> None:
    """Prüft eine Grenze; ``None`` ist explizit unbegrenzt."""
    if limit is not None and current + requested > limit:
        raise AiQuotaExceeded(reason)


def reserve_ai_usage(
    db: Session,
    user: User,
    *,
    request_id: str | UUID,
    estimated_tokens: int,
    estimated_cost_microunits: int = 0,
    server_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    now: datetime | None = None,
) -> AiUsageEvent:
    """Reserviert eine Anfrage atomar oder liefert dieselbe Reservierung erneut."""
    request_key = _canonical_request_id(request_id)
    if (
        estimated_tokens < 0
        or estimated_tokens > TOKEN_LIMIT_MAX
        or estimated_cost_microunits < 0
        or estimated_cost_microunits > MAX_COST_MICROUNITS
    ):
        raise ValueError("Geschätzter AI-Verbrauch liegt außerhalb des erlaubten Bereichs")

    # Bestehende Reservierung zuerst pruefen: Dies verhindert nur eine zweite
    # Kontingentbuchung. Provider-Replays blockiert die Chat-Schicht anhand der
    # persistenten Assistant-Nachricht mit derselben Request-ID.
    existing = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_key).first()
    if existing is not None:
        if not _matches_reservation(
            existing,
            user_id=user.id,
            server_id=server_id,
            provider_id=provider_id,
            model=model,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimated_cost_microunits,
        ):
            raise AiUsageConflict("AI-Request-ID wurde mit anderen Daten wiederverwendet")
        return existing

    # PostgreSQL serialisiert dadurch alle Quotenentscheidungen pro User.
    db.query(User.id).filter(User.id == user.id).with_for_update().one()
    current_time = now or datetime.now(timezone.utc)
    day_start, week_start, month_start = _period_starts(current_time)
    limits = resolve_effective_limits(db, user)

    minute_count = (
        db.query(func.count(AiUsageEvent.id))
        .filter(
            AiUsageEvent.user_id == user.id,
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= current_time - timedelta(minutes=1),
        )
        .scalar()
        or 0
    )
    _ensure_within(limits.requests_per_minute, int(minute_count), 1, "requests_per_minute")

    concurrent = (
        db.query(func.count(AiUsageEvent.id))
        .filter(
            AiUsageEvent.user_id == user.id,
            AiUsageEvent.status == "reserved",
        )
        .scalar()
        or 0
    )
    _ensure_within(limits.concurrent_operations, int(concurrent), 1, "concurrent_operations")
    _ensure_within(
        limits.daily_token_limit,
        _sum_since(db, user.id, day_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "daily_token_limit",
    )
    _ensure_within(
        limits.weekly_token_limit,
        _sum_since(db, user.id, week_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "weekly_token_limit",
    )
    _ensure_within(
        limits.monthly_token_limit,
        _sum_since(db, user.id, month_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "monthly_token_limit",
    )
    _ensure_within(
        None if limits.monthly_cost_limit_cents is None else limits.monthly_cost_limit_cents * MICROUNITS_PER_CENT,
        _sum_since(db, user.id, month_start, AiUsageEvent.accounted_cost_microunits),
        estimated_cost_microunits,
        "monthly_cost_limit_cents",
    )

    event = AiUsageEvent(
        request_id=request_key,
        user_id=user.id,
        server_id=server_id,
        provider_id=provider_id,
        model=model,
        status="reserved",
        reserved_tokens=estimated_tokens,
        reserved_cost_microunits=estimated_cost_microunits,
        accounted_tokens=estimated_tokens,
        accounted_cost_microunits=estimated_cost_microunits,
        created_at=current_time,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError as exc:
        concurrent = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_key).first()
        if concurrent is not None and _matches_reservation(
            concurrent,
            user_id=user.id,
            server_id=server_id,
            provider_id=provider_id,
            model=model,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimated_cost_microunits,
        ):
            return concurrent
        raise AiUsageConflict("AI-Request-ID wurde gleichzeitig widersprüchlich reserviert") from exc
    return event


def abrechnung(
    usage: "StreamUsage",
    *,
    reserved_tokens: int,
    estimated_actual_tokens: int,
    failed: bool = False,
    token_price_micro_usd_per_million: int | None = None,
) -> tuple[int, int, str]:
    """Was eine Anfrage gekostet hat — Tokens, Kosten, und woher die Zahl stammt.

    Die Reihenfolge ist die ganze Aussage dieser Funktion:

    1. **Was der Anbieter meldet.** OpenRouter schickt in der letzten Zeile
       jedes Streams ``cost`` — den Betrag, der dem Konto tatsächlich belastet
       wurde, in USD. Ihn zu buchen ist genauer als jede Nachrechnung, weil es
       dieselbe Zahl ist, die im Dashboard des Anbieters steht. Genau daran
       soll sich die Anzeige nachprüfen lassen.
    2. **Sonst der gepflegte Preis** (`estimate_cost_microunits`, hier
       nachgebildet, weil dort ein ``AiProvider`` erwartet wird und hier nur
       noch die Zahl vorliegt). Eine Näherung mit *einem* Preis auf *alle*
       Tokens — und als solche markiert.
    3. **Sonst null.** MSM erfindet keinen Preis.

    Im Stream stand hier früher ein ``max(reserviert, gerechnet)``. Der Gedanke
    war, dass eine Überschreitung nicht nachträglich verschwinden soll — die
    Wirkung war eine andere: die Reservierung ist eine grobe Schätzung
    (`estimate_reserved_tokens`, Zeichen durch vier plus 2.048 Ausgabetokens),
    und lag sie zu hoch, blieb sie **für immer** stehen, auch wenn hinterher
    die gemessene Zahl vorlag. Eine Messung sticht eine Schätzung; das ist der
    ganze Zweck einer Messung.

    Die Tokenzahl folgt derselben Ordnung, hat aber einen zusätzlichen Fall:
    bricht eine Anfrage ab, ohne dass der Anbieter etwas gemeldet hat, gilt
    konservativ die Reserve. Nach einem Abbruch ist unbekannt, wieviel der
    Anbieter bereits geliefert hat, und verschenkte Tokens sind das schlechtere
    Risiko als ein paar zu viel gebuchte.

    Steht hier und nicht im Stream, weil die Verdichtung dieselbe Frage
    beantworten muss. Sie tat es lange nicht: sie buchte fest den reservierten
    Betrag und damit **nie** echte Kosten.
    """
    if usage.total_tokens is not None:
        tokens = usage.total_tokens
    elif failed:
        tokens = reserved_tokens
    else:
        tokens = estimated_actual_tokens
    tokens = min(TOKEN_LIMIT_MAX, max(0, tokens))

    if usage.vom_anbieter and usage.cost_micro_usd is not None:
        return tokens, min(MAX_COST_MICROUNITS, max(0, usage.cost_micro_usd)), "provider"
    if token_price_micro_usd_per_million:
        kosten = (tokens * int(token_price_micro_usd_per_million)) // 1_000_000
        return tokens, min(MAX_COST_MICROUNITS, kosten), "estimate"
    return tokens, 0, "none"


def complete_ai_usage(
    db: Session,
    event: AiUsageEvent,
    *,
    actual_tokens: int,
    actual_cost_microunits: int,
    aufschluesselung: "StreamUsage | None" = None,
    cost_source: str | None = None,
    now: datetime | None = None,
) -> AiUsageEvent:
    """Schließt eine Reservierung idempotent mit dem tatsächlichen Verbrauch ab.

    ``aufschluesselung`` und ``cost_source`` sind der **Nachweis** neben der
    Buchung: was der Anbieter im Einzelnen gemeldet hat, und ob überhaupt er es
    war. Sie gehören ausdrücklich **nicht** zum Idempotenzvertrag — der bleibt
    auf ``accounted_tokens`` und ``accounted_cost_microunits``, den beiden
    Zahlen, an denen die Kontingente hängen. Eine Wiederholung mit derselben
    Buchung, aber leerem Nachweis, ist kein Konflikt; sie ergänzt nur nichts.
    """
    if (
        actual_tokens < 0
        or actual_tokens > TOKEN_LIMIT_MAX
        or actual_cost_microunits < 0
        or actual_cost_microunits > MAX_COST_MICROUNITS
    ):
        raise ValueError("Tatsächlicher AI-Verbrauch liegt außerhalb des erlaubten Bereichs")
    if event.status == "completed":
        if (
            event.accounted_tokens != actual_tokens
            or event.accounted_cost_microunits != actual_cost_microunits
        ):
            raise AiUsageConflict("Abgeschlossene AI-Anfrage hat abweichende Verbrauchsdaten")
        return event
    if event.status != "reserved":
        raise AiUsageConflict("Nur reservierte AI-Anfragen können abgeschlossen werden")
    event.status = "completed"
    event.accounted_tokens = actual_tokens
    event.accounted_cost_microunits = actual_cost_microunits
    if aufschluesselung is not None:
        event.prompt_tokens = aufschluesselung.prompt_tokens
        event.completion_tokens = aufschluesselung.completion_tokens
        event.cached_tokens = aufschluesselung.cached_tokens
        event.reasoning_tokens = aufschluesselung.reasoning_tokens
        # Mindestens eine: die Zeile existiert, weil eine Anfrage stattgefunden
        # hat. Eine 0 hier hiesse "der Anbieter wurde nie gefragt", und das
        # stimmt an dieser Stelle nie.
        event.provider_requests = max(1, aufschluesselung.anfragen)
    if cost_source is not None:
        event.cost_source = cost_source
    event.completed_at = now or datetime.now(timezone.utc)
    db.flush()
    return event


def fail_ai_usage(db: Session, event: AiUsageEvent) -> AiUsageEvent:
    """Gibt eine fehlgeschlagene Reservierung frei, ohne Verbrauch zu erfinden."""
    if event.status == "failed":
        return event
    if event.status != "reserved":
        raise AiUsageConflict("Abgeschlossene AI-Anfragen können nicht fehlschlagen")
    event.status = "failed"
    event.accounted_tokens = 0
    event.accounted_cost_microunits = 0
    event.completed_at = datetime.now(timezone.utc)
    db.flush()
    return event


# ── Auswertung ────────────────────────────────────────────────────────
#
# Getrennt von der Reservierung, weil es eine andere Frage beantwortet: dort
# „darf diese Anfrage noch“, hier „wer hat wieviel verbraucht“. Gemeinsam ist
# beiden die Grundlage — dieselben Zeitraeume aus `_period_starts` und dieselben
# `ACTIVE_STATUSES`. Das ist keine Kosmetik: eine Ansicht, die andere Zahlen
# zeigt als die Sperre durchsetzt, ist schlimmer als gar keine. Wer sich fragt,
# warum jemand mit „noch 20 % frei“ abgewiesen wird, findet die Antwort dann
# nirgends.
#
# `failed` faellt damit heraus, und zwar richtig: `fail_ai_usage` setzt den
# verbuchten Verbrauch auf 0 zurueck: eine Anfrage, die nie beim Anbieter
# ankam, hat nichts gekostet.


@dataclass(frozen=True)
class AiUsageSummary:
    """Verbrauch eines Benutzers in den Zeitraeumen, die auch die Grenzen kennen.

    ``last_request_at`` ist die letzte Anfrage **innerhalb des ausgewerteten
    Fensters**, nicht die letzte ueberhaupt. Das Fenster ist genau der Zeitraum,
    den die Ansicht zeigt — ein Datum von ausserhalb waere dort nicht einzuordnen.
    """

    user_id: int
    username: str
    tokens_today: int
    tokens_week: int
    tokens_month: int
    cost_month_microunits: int
    requests_month: int
    last_request_at: datetime | None


def _since(column, threshold: datetime):
    """Summiert eine Spalte nur fuer Zeilen ab ``threshold``.

    Alle drei Zeitraeume in **einer** Abfrage statt in dreien je Benutzer. Bei
    einem Hoster mit einigen hundert Kunden waere die naheliegende Schleife
    dreihundert Abfragen fuer eine Tabelle.
    """
    return func.coalesce(func.sum(case((AiUsageEvent.created_at >= threshold, column), else_=0)), 0)


def _usage_rows(
    db: Session, *, user_id: int | None, now: datetime | None = None
) -> list[AiUsageSummary]:
    current_time = now or datetime.now(timezone.utc)
    day_start, week_start, month_start = _period_starts(current_time)
    # Die ISO-Woche kann vor dem Monatsanfang beginnen — am Ersten eines Monats
    # regelmaessig. Wer hier nur ab Monatsanfang laedt, zeigt in den ersten
    # Tagen eine zu niedrige Wochenzahl.
    earliest = min(week_start, month_start)

    query = (
        db.query(
            AiUsageEvent.user_id.label("user_id"),
            User.username.label("username"),
            _since(AiUsageEvent.accounted_tokens, day_start).label("tokens_today"),
            _since(AiUsageEvent.accounted_tokens, week_start).label("tokens_week"),
            _since(AiUsageEvent.accounted_tokens, month_start).label("tokens_month"),
            _since(AiUsageEvent.accounted_cost_microunits, month_start).label("cost_month"),
            _since(1, month_start).label("requests_month"),
            func.max(AiUsageEvent.created_at).label("last_request_at"),
        )
        .join(User, User.id == AiUsageEvent.user_id)
        .filter(
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= earliest,
        )
        .group_by(AiUsageEvent.user_id, User.username)
    )
    if user_id is not None:
        query = query.filter(AiUsageEvent.user_id == user_id)

    return [
        AiUsageSummary(
            user_id=int(row.user_id),
            username=row.username,
            tokens_today=int(row.tokens_today or 0),
            tokens_week=int(row.tokens_week or 0),
            tokens_month=int(row.tokens_month or 0),
            cost_month_microunits=int(row.cost_month or 0),
            requests_month=int(row.requests_month or 0),
            last_request_at=row.last_request_at,
        )
        for row in query.all()
    ]


@dataclass(frozen=True)
class AiUsageEventRow:
    """Eine einzelne Anfrage mit allem, was der Anbieter dazu gemeldet hat."""

    id: int
    created_at: datetime
    user_id: int
    username: str
    model: str | None
    tokens: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    provider_requests: int | None
    cost_micro_usd: int
    cost_source: str | None


#: Wieviele Einzelzeilen eine Seite hoechstens traegt. Die Aufstellung ist ein
#: Nachweis zum Durchsehen, keine Datenausleitung.
MAX_EVENT_LIMIT = 200
STANDARD_EVENT_LIMIT = 50


def usage_events(
    db: Session,
    *,
    user_id: int | None,
    limit: int = STANDARD_EVENT_LIMIT,
    offset: int = 0,
    now: datetime | None = None,
) -> tuple[list[AiUsageEventRow], bool]:
    """Die Einzelanfragen hinter den Summen, neueste zuerst.

    Bewusst aus **denselben** Bausteinen wie `_usage_rows`: derselbe Monat aus
    `_period_starts`, dieselben `ACTIVE_STATUSES`. Eine Aufstellung, die andere
    Zeilen zeigt als die Summe daneben zaehlt, waere schlimmer als keine — wer
    eine Abweichung sucht, faende sie dann in der Ansicht statt in der Sache.

    Der Monat ist die Grenze, weil er der laengste Zeitraum ist, den die Summen
    ueberhaupt kennen. Eine Zeile von davor haette in dieser Ansicht keinen
    Bezug mehr, gegen den man sie halten koennte.

    Zurueck kommt ein Paar: die Zeilen und ob dahinter noch mehr liegt. Ermittelt
    wird das, indem eine Zeile mehr geholt wird als gefragt — billiger als ein
    zweites ``count(*)`` ueber dieselbe Tabelle.
    """
    current_time = now or datetime.now(timezone.utc)
    _, _, month_start = _period_starts(current_time)
    gefragt = max(1, min(MAX_EVENT_LIMIT, int(limit)))

    query = (
        db.query(AiUsageEvent, User.username)
        .join(User, User.id == AiUsageEvent.user_id)
        .filter(
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= month_start,
        )
    )
    if user_id is not None:
        query = query.filter(AiUsageEvent.user_id == user_id)

    rohdaten = (
        query
        # Nach ``id`` als zweitem Kriterium: zwei Anfragen koennen im selben
        # Sekundenbruchteil angelegt werden, und eine Sortierung ohne
        # eindeutigen Schluessel laesst eine Zeile beim Blaettern doppelt oder
        # gar nicht erscheinen.
        .order_by(AiUsageEvent.created_at.desc(), AiUsageEvent.id.desc())
        .offset(max(0, int(offset)))
        .limit(gefragt + 1)
        .all()
    )
    mehr = len(rohdaten) > gefragt
    return [
        AiUsageEventRow(
            id=event.id,
            created_at=event.created_at,
            user_id=event.user_id,
            username=username,
            model=event.model,
            tokens=int(event.accounted_tokens or 0),
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            cached_tokens=event.cached_tokens,
            reasoning_tokens=event.reasoning_tokens,
            provider_requests=event.provider_requests,
            cost_micro_usd=int(event.accounted_cost_microunits or 0),
            cost_source=event.cost_source,
        )
        for event, username in rohdaten[:gefragt]
    ], mehr


def usage_overview(db: Session, *, now: datetime | None = None) -> list[AiUsageSummary]:
    """Der Verbrauch aller Benutzer, die im Zeitraum ueberhaupt etwas verbraucht haben.

    Wer nichts verbraucht hat, fehlt — bewusst. Die Frage hinter dieser Ansicht
    ist „wohin fliessen die Kosten“, und eine Liste, in der zweihundert Kunden
    mit lauter Nullen stehen, beantwortet sie schlechter als eine mit den zwoelf,
    die tatsaechlich etwas verbrauchen.

    Sortiert nach Monatsverbrauch: die teuerste Zeile steht oben, weil sie die
    ist, wegen der jemand diese Seite aufruft.
    """
    rows = _usage_rows(db, user_id=None, now=now)
    return sorted(rows, key=lambda row: (-row.tokens_month, row.username.lower()))


def usage_for_user(db: Session, user: User, *, now: datetime | None = None) -> AiUsageSummary:
    """Der eigene Verbrauch. Ohne Ereignisse eine Null-Zeile statt ``None``.

    Ein Benutzer, der die KI noch nie benutzt hat, soll „0 von 50.000“ sehen und
    nicht eine leere Ansicht, die nach einem Fehler aussieht.
    """
    rows = _usage_rows(db, user_id=user.id, now=now)
    if rows:
        return rows[0]
    return AiUsageSummary(
        user_id=user.id, username=user.username, tokens_today=0, tokens_week=0,
        tokens_month=0, cost_month_microunits=0, requests_month=0, last_request_at=None,
    )


def verwaiste_reservierungen_abgleichen(db) -> int:
    """Schliesst beim Panel-Start Reservierungen ohne zugehoerige Nachricht.

    Die Verdichtung ist der einzige Pfad, der Kontingent reserviert und sofort
    committet, **ohne** dazu eine `AiMessage` anzulegen — sie fasst ja nur
    vorhandene Nachrichten zusammen, sie schreibt keine. Der bestehende
    Wiederanlauf `reconcile_interrupted_ai_streams` haengt aber genau an einer
    Nachricht im Zustand `streaming` und findet diese Zeile deshalb nie.

    Stirbt der Prozess zwischen Reservierung und Abschluss — ein Kill loest
    `asyncio.CancelledError` aus, und das ist eine BaseException, die weder der
    Anbieterfehler-Zweig hier noch das `except Exception` des Aufrufers faengt —
    bleibt das Ereignis fuer immer auf `reserved`. Das ist nicht bloss
    Buchhaltung: der Nebenlaeufigkeitszaehler in `reserve_ai_usage` zaehlt
    reservierte Ereignisse **ohne Zeitfenster**. Bei `concurrent_operations = 2`
    genuegen zwei solcher Abbrueche, und der Benutzer bekommt dauerhaft
    AiQuotaExceeded, obwohl nichts laeuft — von selbst loest sich das nie.

    Abgerechnet wird konservativ mit dem reservierten Wert, aus demselben Grund
    wie beim Stream-Wiederanlauf: nach einem Abbruch ist unbekannt, wie viele
    Tokens der Anbieter bereits geliefert hat, und verschenkte Tokens sind das
    schlechtere Risiko als ein paar zu viel gebuchte.

    Die Funktion steht hier und nicht bei der Verdichtung, obwohl nur diese die
    verwaisten Zeilen erzeugt: sie fegt ueber *alle* offenen Reservierungen und
    kennt weder Unterhaltung noch Faltung. Wer spaeter sucht, warum eine
    Reservierung beim Start geschlossen wurde, sieht dort nach, wo sie angelegt
    wurde.
    """
    offen = db.query(AiUsageEvent).filter(AiUsageEvent.status == "reserved").all()
    jetzt = datetime.now(timezone.utc)
    geschlossen = 0
    for event in offen:
        hat_nachricht = (
            db.query(AiMessage.id)
            .filter(AiMessage.request_id == event.request_id)
            .first()
        )
        if hat_nachricht is not None:
            # Gibt es eine Nachricht, ist `reconcile_interrupted_ai_streams`
            # zustaendig. Der kennt zusaetzlich deren Zustand und darf eine
            # gerade erst begonnene Anfrage nicht faelschlich abschliessen.
            continue
        complete_ai_usage(
            db,
            event,
            actual_tokens=event.reserved_tokens,
            actual_cost_microunits=event.reserved_cost_microunits,
            # Hier wird die Reserve gebucht, nicht eine Messung — der Prozess
            # ist ja gestorben, bevor der Anbieter irgendetwas melden konnte.
            # Die Zeile muss das sagen, sonst steht sie in der Aufstellung
            # neben gemessenen und sieht aus wie eine von ihnen.
            cost_source="estimate",
            now=jetzt,
        )
        geschlossen += 1
    if geschlossen:
        db.commit()
    return geschlossen
