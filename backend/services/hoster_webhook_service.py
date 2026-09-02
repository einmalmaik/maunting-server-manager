"""Signierte, wiederholbare Webhooks an den angebundenen Shop.

Unterschiede zum vorhandenen Server-Webhook (`outbound_webhook_service`):

- **Signiert statt Shared Secret im Header.** Dort reist das Klartext-Secret in
  jedem Request mit und jeder Proxy auf dem Weg sieht es. Hier wird nur eine
  HMAC-SHA256-Signatur ueber `timestamp.body` uebertragen; das Secret verlaesst
  MSM nie.
- **Dauerhaft statt nur im Speicher.** Dort liegt der Retry in einem
  `asyncio`-Task; ein Panel-Neustart waehrend des Backoffs verliert die
  Zustellung endgueltig. Hier steht der naechste Versuch mit `next_attempt_at`
  in der Datenbank und wird von einem Scheduler-Lauf aufgenommen.

Das Signaturformat ist absichtlich identisch zu dem, was
`singra_webhook_handler` eingehend prueft — ein Empfaenger kann denselben Code
verwenden.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from models import HosterIntegration, HosterService, HosterWebhookDelivery
from services.hoster_integration_service import resolve_webhook_secret


logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Wachsender Abstand, damit ein kurz gestoerter Shop nicht zusaetzlich
# belastet wird. Der letzte Versuch liegt gut eine Stunde nach dem ersten.
RETRY_BACKOFF_SECONDS = (30, 120, 600, 3_600)
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_PAYLOAD_BYTES = 16 * 1024
DELIVERY_RETENTION_DAYS = 30

SIGNATURE_HEADER = "X-MSM-Signature"
TIMESTAMP_HEADER = "X-MSM-Timestamp"
EVENT_HEADER = "X-MSM-Event"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sign_payload(secret: str, timestamp: str, body: str) -> str:
    """HMAC-SHA256 ueber `timestamp.body`, ausgegeben als `sha256=<hex>`.

    Der Zeitstempel ist Teil der signierten Daten. Ohne ihn koennte ein
    abgefangener Request beliebig oft erneut zugestellt werden.
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def service_payload(service: HosterService) -> dict:
    """Zustandsmeldung an den Shop — bewusst ohne interne Infrastrukturdetails.

    Node-Namen, Hostadressen, Ports und Installationspfade gehoeren dem
    Betreiber, nicht dem Shop. Der Shop braucht nur den Vertragszustand.
    """
    return {
        "external_service_id": service.external_service_id,
        "desired_state": service.desired_state,
        "status": service.status,
        "status_code": service.status_code,
        "server_id": service.server_id,
        "correlation_id": service.correlation_id,
        "terminate_after": (
            service.terminate_after.isoformat() if service.terminate_after else None
        ),
        "updated_at": service.updated_at.isoformat() if service.updated_at else None,
    }


def enqueue_service_event(
    db: Session, *, integration: HosterIntegration, service: HosterService
) -> HosterWebhookDelivery | None:
    """Stellt eine Zustandsaenderung zur Zustellung ein.

    Ohne konfiguriertes Ziel oder Secret wird bewusst nichts eingestellt: eine
    Zeile, die nie zugestellt werden kann, waere nur irrefuehrender Ballast.
    """
    if not integration.webhook_url or not integration.webhook_secret_encrypted:
        return None
    body = json.dumps(
        {"event": f"service.{service.status}", **service_payload(service)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        logger.warning("Hoster-Webhook-Payload zu gross (service_id=%s)", service.id)
        return None
    delivery = HosterWebhookDelivery(
        integration_id=integration.id,
        service_id=service.id,
        event_type=f"service.{service.status}"[:64],
        payload=body,
        payload_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        status="pending",
        attempt=0,
        next_attempt_at=_now(),
        correlation_id=service.correlation_id,
    )
    db.add(delivery)
    db.commit()
    return delivery


def enqueue_custom_event(
    db: Session,
    *,
    integration: HosterIntegration,
    event_type: str,
    payload: dict,
    correlation_id: str | None = None,
) -> HosterWebhookDelivery | None:
    """Stellt ein beliebiges Event (z. B. Simulation) zur Webhook-Zustellung ein."""
    if not integration.webhook_url:
        return None
    body = json.dumps(
        {"event": event_type, **payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        logger.warning("Hoster-Webhook-Payload zu gross (integration_id=%s)", integration.id)
        return None
    delivery = HosterWebhookDelivery(
        integration_id=integration.id,
        service_id=None,
        event_type=event_type[:64],
        payload=body,
        payload_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        status="pending",
        attempt=0,
        next_attempt_at=_now(),
        correlation_id=correlation_id or str(uuid4()),
    )
    db.add(delivery)
    db.commit()
    return delivery


def _schedule_retry(delivery: HosterWebhookDelivery) -> None:
    """Setzt den naechsten Versuch oder schliesst endgueltig ab."""
    if delivery.attempt >= MAX_ATTEMPTS:
        delivery.status = "failed"
        delivery.next_attempt_at = None
        return
    index = min(delivery.attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
    delivery.next_attempt_at = _now() + timedelta(seconds=RETRY_BACKOFF_SECONDS[index])


def deliver_pending(db: Session, *, limit: int = 25) -> int:
    """Stellt faellige Webhooks zu. Wird zyklisch vom Scheduler aufgerufen.

    Rueckgabe ist die Zahl der versuchten Zustellungen. Fehler beenden den Lauf
    nicht: eine kaputte Integration darf die anderen nicht blockieren.
    """
    due = (
        db.query(HosterWebhookDelivery)
        .filter(
            HosterWebhookDelivery.status == "pending",
            HosterWebhookDelivery.next_attempt_at.isnot(None),
            HosterWebhookDelivery.next_attempt_at <= _now(),
        )
        .order_by(HosterWebhookDelivery.id)
        .limit(max(1, min(limit, 100)))
        .all()
    )
    attempted = 0
    for delivery in due:
        integration = (
            db.query(HosterIntegration)
            .filter(HosterIntegration.id == delivery.integration_id)
            .first()
        )
        if integration is None or not integration.enabled or not integration.webhook_url:
            delivery.status = "failed"
            delivery.error = "integration_unavailable"
            delivery.next_attempt_at = None
            db.commit()
            continue
        try:
            secret = resolve_webhook_secret(integration)
        except Exception:
            # Ein Entschluesselungsfehler (z. B. nach Key-Rotation) darf nicht
            # stillschweigend als "zugestellt" gelten.
            logger.warning(
                "Webhook-Secret nicht entschluesselbar (integration=%s)", integration.slug
            )
            secret = None
        if not secret:
            delivery.status = "failed"
            delivery.error = "secret_unavailable"
            delivery.next_attempt_at = None
            db.commit()
            continue

        attempted += 1
        delivery.attempt += 1
        delivery.sent_at = _now()
        timestamp = str(int(delivery.sent_at.timestamp()))
        try:
            response = httpx.post(
                integration.webhook_url,
                content=delivery.payload.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    TIMESTAMP_HEADER: timestamp,
                    SIGNATURE_HEADER: sign_payload(secret, timestamp, delivery.payload),
                    EVENT_HEADER: delivery.event_type,
                    "User-Agent": "MSM-Hoster-Webhook/1.0",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            # Nur der Fehlertyp wird festgehalten: Fehlertexte von httpx
            # enthalten die vollstaendige Ziel-URL.
            delivery.error = type(exc).__name__[:200]
            delivery.response_code = None
            _schedule_retry(delivery)
            db.commit()
            continue

        delivery.response_code = response.status_code
        if 200 <= response.status_code < 300:
            delivery.status = "ok"
            delivery.error = None
            delivery.next_attempt_at = None
        elif 400 <= response.status_code < 500:
            # Client-Fehler wiederholen sich nicht von allein.
            delivery.status = "failed"
            delivery.error = f"HTTP {response.status_code}"
            delivery.next_attempt_at = None
        else:
            delivery.error = f"HTTP {response.status_code}"
            _schedule_retry(delivery)
        db.commit()
    return attempted


def retry_delivery(db: Session, delivery: HosterWebhookDelivery) -> None:
    """Stellt eine endgueltig fehlgeschlagene Zustellung erneut in die Warteschlange."""
    if delivery.status != "failed":
        return
    delivery.status = "pending"
    delivery.attempt = 0
    delivery.error = None
    delivery.next_attempt_at = _now()
    db.commit()


def enforce_retention(db: Session) -> int:
    """Entfernt alte, abgeschlossene Zustellungen."""
    cutoff = _now() - timedelta(days=DELIVERY_RETENTION_DAYS)
    removed = (
        db.query(HosterWebhookDelivery)
        .filter(
            HosterWebhookDelivery.status.in_(("ok", "failed")),
            HosterWebhookDelivery.created_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(removed or 0)
