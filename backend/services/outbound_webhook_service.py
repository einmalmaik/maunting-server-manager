"""Outbound-Webhook-Versand (MSM -> Drittsystem wie Discord-Bot).

Sicherheitsmodell
-----------------
- Jede Subscription hat ihr eigenes Secret. Wir senden den Klartext-Wert
  ausschliesslich beim Anlegen / Rotieren einmal an den Aufrufer zurueck
  und legen NUR `hashlib.sha256(secret)` in der DB ab.
- Bei der Zustellung wird das Klartext-Secret als `X-Webhook-Secret`-Header
  mitgesendet (Empfaenger-Verifizierung). Es wird NIE geloggt.
- Versand erfolgt immer aus einem dedizierten Worker-Thread oder Background-
  Task; wir blockieren HTTP-Requests NICHT mit langen Webhook-Versuchen.
- Fehlertexte (Exception-Messages) koennen URLs enthalten — wir kuerzen
  sie und loggen nur den Statuscode, niemals die volle URL.

KISS-Prinzipien
---------------
- Eine Datei, ein klarer Vertrag (dispatch_event).
- Keine externe Lib; httpx ist bereits im Projekt (requirements.txt).
- HTTP 2xx = success. 4xx = failed, kein retry (Client-Fehler).
  5xx/Network = failed, retry mit Backoff.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models.server import Server
from models.webhook_delivery import WebhookDelivery
from models.webhook_subscription import WebhookSubscription

logger = logging.getLogger(__name__)


# Limits & Defaults
MAX_TARGET_URL_LENGTH = 2048
MAX_PAYLOAD_BYTES = 16 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 3
DELIVERY_RETENTION_DAYS = 7
DELIVERY_RETENTION_PER_SUB = 100


# Event-Typen, die verschickt werden koennen. Wird in Routers + UI verwendet.
EVENT_STATUS_CHANGE = "status_change"
EVENT_PLAYER_UPDATE = "player_update"
EVENT_ERROR = "error"
EVENT_KNOWN_TYPES = frozenset({EVENT_STATUS_CHANGE, EVENT_PLAYER_UPDATE, EVENT_ERROR})


# ── Public API: Test-Secret generieren + hashen ─────────────────────────────


def generate_secret() -> str:
    """URL-sicherer Token fuer den Empfaenger (Discord-Bot etc.)."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    """SHA-256 Hex (64 Zeichen). Constant-time compare im Empfaenger."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_hint(secret: str) -> str:
    """Letzte 4 Zeichen als UX-Erinnerung."""
    if len(secret) < 4:
        return "****"
    return f"...{secret[-4:]}"


def payload_hash(payload_text: str) -> str:
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


# ── Subscription-Filter (event_filter: "status_change,player_update" | "") ──


def _filter_matches(event_filter: str | None, event_type: str) -> bool:
    """True wenn der Event-Typ vom Subscription-Filter abgedeckt ist.

    Leer/None = "alle senden" (KISS-Default: User abonniert pauschal).
    WICHTIG: Hier wird NIE per ``event_type in filter`` geprueft — wir
    wollen Wortgleichheit auf dem vollen Event-Namen, sonst feuert
    "status_change" auch in einem Filter "change" (false positive).
    """
    if not event_filter:
        return True
    wanted = {e.strip() for e in event_filter.split(",") if e.strip()}
    if not wanted:
        return True
    return event_type in wanted


# ── Core dispatch ───────────────────────────────────────────────────────────


async def dispatch_event(
    db: Session,
    *,
    server: Server,
    event_type: str,
    payload: dict[str, Any],
) -> list[int]:
    """Sendet `payload` an alle aktiven Subscriptions dieses Servers.

    Wird auch synchron aus dem Lifecycle-/Scheduler-Pfad aufgerufen.
    Da httpx asynchron ist, delegieren wir den eigentlichen HTTP-Call in
    einen separaten Background-Task (Fire-and-forget). Synchroner Pfad
    bekommt NUR die Liste der versendeten Delivery-IDs zurueck
    (Logging/Debug). Bei Auftreten eines Fehlers wird der Delivery-Record
    im `failed` Status persistiert.

    WICHTIG: Niemals Plaintext-Secret oder volle URL loggen.
    """
    # Subscription-Lookup ist billig; pro Server idR <10 Records.
    subs = (
        db.query(WebhookSubscription)
        .filter(
            WebhookSubscription.server_id == server.id,
            WebhookSubscription.enabled.is_(True),
        )
        .all()
    )
    if not subs:
        return []

    payload_text = _serialize_payload(payload, event_type)
    if len(payload_text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        logger.warning(
            "outbound-webhook: payload too large for server_id=%s (event=%s), skipping",
            server.id,
            event_type,
        )
        return []

    delivery_ids: list[int] = []
    for sub in subs:
        if not _filter_matches(sub.event_filter, event_type):
            continue

        delivery = WebhookDelivery(
            subscription_id=sub.id,
            server_id=server.id,
            event_type=event_type[:64],
            payload=payload_text,
            payload_hash=payload_hash(payload_text),
            status="pending",
            attempt=1,
            sent_at=datetime.now(timezone.utc),
        )
        db.add(delivery)
        db.flush()
        delivery_ids.append(delivery.id)
    db.commit()

    # Background-Tasks starten
    for sub in subs:
        if not _filter_matches(sub.event_filter, event_type):
            continue
        # Den jeweiligen Delivery-Record finden, der zu dieser Subscription gehoert
        dlv = next((d for d in db.identity_map.values()
                    if isinstance(d, WebhookDelivery)
                    and d.subscription_id == sub.id
                    and d.status == "pending"), None)
        if dlv is None:
            continue
        asyncio.create_task(
            _send_with_retry(sub.id, delivery_id=dlv.id),
            name=f"webhook-delivery-{dlv.id}",
        )

    return delivery_ids


async def _send_with_retry(subscription_id: int, delivery_id: int) -> None:
    """Sendet eine einzelne Zustellung mit Retry+Backoff.

    Persistiert Ergebnis direkt in den `webhook_deliveries`-Record und
    aktualisiert den `last_delivery_*`-Snapshot der Subscription.

    Args:
        subscription_id: Welche Subscription gerade sendet
        delivery_id: Welcher konkreter Delivery-Record persistiert wurde

    Side Effects:
        Schreibt `status`, `response_code`, `error`, `attempt` an den
        Delivery-Record sowie `last_delivery_*` an die Subscription.
    """
    backoff_choices = [1, 4, 16]  # Sekunden; 3 Versuche insgesamt
    last_error: str | None = None
    last_code: int | None = None

    # Frische DB-Session, weil wir aus einem Background-Task laufen
    from database import SessionLocal
    db = SessionLocal()
    try:
        sub = db.get(WebhookSubscription, subscription_id)
        delivery = db.get(WebhookDelivery, delivery_id)
        if sub is None or delivery is None:
            return

        secret = _load_plaintext_secret(sub, db)
        if not secret:
            delivery.status = "failed"
            delivery.error = "Secret nicht abrufbar"
            db.commit()
            return

        for attempt_index, sleep_seconds in enumerate([0] + backoff_choices):
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            delivery.attempt = attempt_index + 1
            delivery.sent_at = datetime.now(timezone.utc)
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                    follow_redirects=False,  # kein stilles Folgen
                ) as client:
                    resp = await client.post(
                        sub.target_url,
                        content=delivery.payload.encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Secret": secret,
                            "X-MSM-Event": delivery.event_type,
                            "X-MSM-Delivery": str(delivery_id),
                            "User-Agent": "MSM-Webhook/1.0",
                        },
                    )
                last_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.status = "ok"
                    delivery.response_code = resp.status_code
                    delivery.error = None
                    sub.last_delivery_status = "ok"
                    sub.last_response_code = resp.status_code
                    sub.last_delivery_at = datetime.now(timezone.utc)
                    db.commit()
                    return
                # 4xx ist Client-Fehler — kein Retry
                if 400 <= resp.status_code < 500:
                    delivery.status = "failed"
                    delivery.response_code = resp.status_code
                    delivery.error = f"HTTP {resp.status_code}"
                    sub.last_delivery_status = "failed"
                    sub.last_response_code = resp.status_code
                    sub.last_delivery_at = datetime.now(timezone.utc)
                    db.commit()
                    return
                # 5xx → retry
                last_error = f"HTTP {resp.status_code}"
                db.commit()
            except (httpx.RequestError, httpx.HTTPError) as exc:
                # Kein URL/Body im Log — nur Fehlertyp
                last_error = type(exc).__name__
                db.commit()
            except Exception as exc:  # pragma: no cover — defensive
                last_error = type(exc).__name__
                logger.exception("outbound-webhook: unhandled exception (delivery=%s)", delivery_id)

        # Alle Retries ausgeschoepft
        delivery.status = "failed"
        delivery.response_code = last_code
        delivery.error = (last_error or "Unknown failure")[:1000]
        sub.last_delivery_status = "failed"
        sub.last_response_code = last_code
        sub.last_delivery_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _load_plaintext_secret(sub: WebhookSubscription, db: Session) -> str | None:
    """Laedt das Klartext-Secret fuer den Versand.

    Wir speichern absichtlich KEINEN Klartext in der DB. Stattdessen wird
    das Secret aus einem kleinen In-Memory-Store geliefert, den der
    Router befuellt, sobald der User das Secret generiert oder rotiert.

    Hintergrund: Ein Server-Manager darf KEINEN symmetrischen Reversible-
    Storage bauen (Security.md §4 verbietet eigene Krypto). Wir uebergeben
    das Secret also nur einmal vom Anlege-Handler an den Dispatcher.
    """
    from services.outbound_webhook_secret_store import get as secret_store_get
    return secret_store_get(sub.id)


def secret_store_put(subscription_id: int, plaintext: str) -> None:
    """Legt das Klartext-Secret im In-Memory-Store ab (nur Prozess-Lifetime).

    Aufgerufen vom Router nach erfolgreichem enable/rotate/secret-update
    sowie beim Server-Start aus dem Bootstrap-Loader (sofern vorhanden).
    """
    from services.outbound_webhook_secret_store import put as secret_store_put_fn
    secret_store_put_fn(subscription_id, plaintext)


# ── Payload-Builder ─────────────────────────────────────────────────────────


def build_status_payload(server: Server) -> dict[str, Any]:
    """Generisches Server-Status-Payload."""
    return {
        "event_type": EVENT_STATUS_CHANGE,
        "server_id": server.id,
        "server_name": server.name,
        "game_type": server.game_type,
        "status": server.status,
        "status_message": server.status_message,
        "started_at": server.last_started_at.isoformat() if server.last_started_at else None,
        "uptime_seconds": server.uptime_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_player_payload(server: Server, players: int, max_players: int | None) -> dict[str, Any]:
    """Spieler-/Slot-Payload."""
    return {
        "event_type": EVENT_PLAYER_UPDATE,
        "server_id": server.id,
        "server_name": server.name,
        "status": server.status,
        "player_count": int(players),
        "max_players": int(max_players) if max_players is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_error_payload(server: Server, error: str) -> dict[str, Any]:
    return {
        "event_type": EVENT_ERROR,
        "server_id": server.id,
        "server_name": server.name,
        "status": server.status,
        "error": (error or "")[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _serialize_payload(payload: dict[str, Any], event_type: str) -> str:
    """Stellt sicher dass event_type im Body enthalten ist."""
    full = {"event_type": event_type, **payload}
    return json.dumps(full, ensure_ascii=False, separators=(",", ":"))


# ── Retention (UI-Feed) ─────────────────────────────────────────────────────


def enforce_retention(db: Session, server_id: int) -> int:
    """Loescht alte Deliveries dieses Servers nach Anzahl + Alter.

    Wird im selben Request aufgerufen, damit kein Cron noetig ist.
    Liefert die Anzahl geloeschter Rows.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DELIVERY_RETENTION_DAYS)
    deleted_age = db.execute(
        delete(WebhookDelivery).where(
            WebhookDelivery.server_id == server_id,
            WebhookDelivery.sent_at < cutoff,
        )
    ).rowcount or 0

    keep_rows = db.execute(
        select(WebhookDelivery.id)
        .where(WebhookDelivery.server_id == server_id)
        .order_by(WebhookDelivery.sent_at.desc())
        .limit(DELIVERY_RETENTION_PER_SUB)
    ).fetchall()
    keep_ids = {row[0] for row in keep_rows}

    all_ids = [
        row[0]
        for row in db.execute(
            select(WebhookDelivery.id).where(WebhookDelivery.server_id == server_id)
        ).fetchall()
    ]
    drop_ids = [i for i in all_ids if i not in keep_ids]
    if drop_ids:
        db.execute(
            delete(WebhookDelivery).where(WebhookDelivery.id.in_(drop_ids))
        )
    return deleted_age


# ── Test-Resetter (fuer Tests) ──────────────────────────────────────────────


def reset_secret_store_for_tests() -> None:
    """Loescht den In-Memory Secret-Store (nur fuer Tests)."""
    from services.outbound_webhook_secret_store import reset_for_tests
    reset_for_tests()
