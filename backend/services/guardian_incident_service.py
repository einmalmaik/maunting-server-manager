"""Idempotent Incident Ingestion and consolidation."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from models import ChangeEvent, Incident, Server, GuardianIncidentDelivery
from services.node_client import NodeClient


logger = logging.getLogger(__name__)

_INCIDENT_STATUSES = frozenset(
    {"open", "recovering", "verifying", "resolved", "quarantined"}
)


def _safe_text(value: Any, *, fallback: str, limit: int) -> str:
    text = " ".join(str(value or fallback).split())
    return text[:limit]


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validated_incident(item: dict[str, Any], server_id: int) -> tuple[str, dict[str, Any]]:
    raw_uuid = str(item.get("uuid") or "")
    try:
        incident_uuid = str(uuid.UUID(raw_uuid))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Agent incident UUID is invalid") from exc
    if incident_uuid != raw_uuid.lower() or int(item.get("server_id") or 0) != server_id:
        raise ValueError("Agent incident identity is invalid")
    payload = item.get("payload")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Agent incident payload is invalid")
    incident_type = _safe_text(item.get("type"), fallback="unknown", limit=64)
    status = str(item.get("status") or "open")
    if status not in _INCIDENT_STATUSES:
        raise ValueError("Agent incident status is invalid")
    fingerprint = _safe_text(item.get("fingerprint"), fallback=incident_uuid, limit=128)
    
    # Extract attempts from payload
    attempts = payload.get("attempts") or []
    if not isinstance(attempts, list):
        attempts = []

    normalized = {
        "type": incident_type,
        "status": status,
        "fingerprint": fingerprint,
        "created_at": _parse_datetime(item.get("created_at")),
        "payload": payload,
        "attempts": attempts,
    }
    return incident_uuid, normalized


def _merge_attempts(existing_json: str | None, new_attempts: list[dict]) -> list[dict]:
    try:
        existing = json.loads(existing_json) if existing_json else []
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    # Merge based on started_at/attempt_number or timestamp to deduplicate
    seen = set()
    merged = []
    for att in existing + new_attempts:
        if not isinstance(att, dict):
            continue
        # Use attempt_number or started_at/timestamp as a key
        key = att.get("attempt_number") or att.get("started_at") or att.get("timestamp")
        if key is not None:
            if key not in seen:
                seen.add(key)
                merged.append(att)
        else:
            merged.append(att)
    return merged


def _notify_guardian_incident(server_id: int, incident_type: str, status: str, description: str) -> None:
    import asyncio
    import threading

    def _bg_notify():
        from database import SessionLocal
        from models import Server, ServerPermission, User
        from services.email_service import EmailService
        from services.outbound_webhook_service import (
            dispatch_event,
            build_guardian_incident_payload,
            EVENT_GUARDIAN_INCIDENT,
        )

        db = SessionLocal()
        try:
            server = db.get(Server, server_id)
            if not server:
                return

            payload = build_guardian_incident_payload(server, incident_type, status, description)

            loop = asyncio.new_event_loop()
            try:
                # 1. Outbound Webhook dispatch
                loop.run_until_complete(
                    dispatch_event(db, server=server, event_type=EVENT_GUARDIAN_INCIDENT, payload=payload)
                )

                # 2. Email Notifications to Server Users with email_notifications enabled
                if EmailService.is_configured():
                    perms = db.query(ServerPermission).filter(ServerPermission.server_id == server_id).all()
                    user_ids = {p.user_id for p in perms}
                    users = db.query(User).filter(User.id.in_(user_ids), User.email_notifications.is_(True)).all() if user_ids else []

                    for u in users:
                        if u.email:
                            loop.run_until_complete(
                                EmailService.send_guardian_incident_notification(
                                    to=u.email,
                                    username=u.username,
                                    server_name=server.name,
                                    incident_type=incident_type,
                                    status=status,
                                    details=description,
                                )
                            )

                # Wait for any background tasks (such as HTTP deliveries created by dispatch_event)
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()
        except Exception as exc:
            logger.warning("Guardian notification bg error for server %s: %s", server_id, exc)
        finally:
            db.close()

    t = threading.Thread(target=_bg_notify, daemon=True)
    t.start()




def ingest_incidents_and_ack(
    db: Session,
    server: Server,
    node_client: NodeClient,
    container_name: str,
    incidents: list[dict[str, Any]],
) -> list[str]:
    """Commit all valid incidents before acknowledging any UUID to the Agent."""
    acknowledged: list[str] = []
    notifications_to_send: list[tuple[str, str, str]] = []

    for raw in incidents:
        try:
            incident_uuid, item = _validated_incident(raw, server.id)
        except (TypeError, ValueError) as exc:
            logger.warning("Guardian rejected malformed incident for server_id=%s: %s", server.id, exc)
            continue

        payload = item["payload"]
        message = _safe_text(
            payload.get("message"),
            fallback="Guardian incident",
            limit=2000,
        )

        # 1. Check if this exact UUID was already delivered (Idempotency)
        delivery = db.query(GuardianIncidentDelivery).filter(GuardianIncidentDelivery.incident_uuid == incident_uuid).first()
        if delivery is not None:
            # We already processed this incident UUID. 
            # If the status changed, update it.
            existing_inc = db.query(Incident).filter(Incident.uuid == incident_uuid).first()
            if existing_inc and existing_inc.status != item["status"]:
                old_st = existing_inc.status
                existing_inc.status = item["status"]
                if existing_inc.status == "resolved":
                    existing_inc.resolved_at = datetime.now(timezone.utc)
                if old_st != item["status"]:
                    notifications_to_send.append((item["type"], item["status"], message))
            
            acknowledged.append(incident_uuid)
            continue

        target_incident = None

        # 2. Check for exact UUID match in Incidents
        existing = db.query(Incident).filter(Incident.uuid == incident_uuid).first()
        if existing is not None:
            merged_att = _merge_attempts(existing.attempts, item["attempts"])
            existing.attempts = json.dumps(merged_att, sort_keys=True, separators=(",", ":"))
            existing.description = message
            old_st = existing.status
            existing.status = item["status"]
            if item["status"] == "resolved":
                existing.resolved_at = datetime.now(timezone.utc)
            elif existing.resolved_at is not None and item["status"] != "resolved":
                existing.resolved_at = None
            if old_st != item["status"]:
                notifications_to_send.append((item["type"], item["status"], message))
            target_incident = existing
        else:
            # 3. Check for active (unresolved) incident with the same fingerprint (Grouping)
            group_parent = (
                db.query(Incident)
                .filter(
                    Incident.server_id == server.id,
                    Incident.fingerprint == item["fingerprint"],
                    Incident.status != "resolved",
                )
                .first()
            )
            if group_parent is not None:
                group_parent.occurrences += 1
                merged_att = _merge_attempts(group_parent.attempts, item["attempts"])
                old_st = group_parent.status
                group_parent.status = item["status"]
                group_parent.attempts = json.dumps(merged_att, sort_keys=True, separators=(",", ":"))
                group_parent.description = message
                if group_parent.status == "resolved":
                    group_parent.resolved_at = datetime.now(timezone.utc)
                if old_st != item["status"]:
                    notifications_to_send.append((item["type"], item["status"], message))
                target_incident = group_parent
            else:
                # 4. Create a brand new incident
                merged_att = item["attempts"]
                status = item["status"]

                new_inc = Incident(
                    uuid=incident_uuid,
                    server_id=server.id,
                    title=f"Autopilot: {item['type']}",
                    description=message,
                    type=item["type"],
                    status=status,
                    fingerprint=item["fingerprint"],
                    created_at=item["created_at"],
                    attempts=json.dumps(merged_att, sort_keys=True, separators=(",", ":")),
                    occurrences=1,
                )
                if status == "resolved":
                    new_inc.resolved_at = datetime.now(timezone.utc)
                db.add(new_inc)
                db.flush()
                notifications_to_send.append((item["type"], status, message))
                target_incident = new_inc

        # Record delivery to prevent future duplicate processing
        if target_incident:
            db.add(GuardianIncidentDelivery(
                incident_uuid=incident_uuid,
                incident_id=target_incident.id,
                server_id=server.id,
                received_at=datetime.now(timezone.utc)
            ))

        acknowledged.append(incident_uuid)

    if not acknowledged:
        return []
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Dispatch notifications after commit
    for inc_type, inc_status, inc_desc in notifications_to_send:
        _notify_guardian_incident(server.id, inc_type, inc_status, inc_desc)


    # ACK payload to node
    try:
        node_client.acknowledge_incidents(container_name, acknowledged)
        now = datetime.now(timezone.utc)
        db.query(GuardianIncidentDelivery).filter(
            GuardianIncidentDelivery.incident_uuid.in_(acknowledged)
        ).update({"acknowledged_at": now}, synchronize_session=False)
        db.commit()
    except Exception as exc:
        logger.error("Failed to ACK incidents for %s: %s", container_name, exc)
        raise

    return acknowledged

