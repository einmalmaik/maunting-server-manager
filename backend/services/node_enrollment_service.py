"""Short-lived, owner-approved enrollment for remote MSM agents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy.orm import Session

from models import Node, NodeEnrollment
from services.node_service import encrypt_node_token, validate_remote_node_host

ENROLLMENT_TTL_MINUTES = 15
CLAIMED_RETENTION_MINUTES = 5


def _claim_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _display_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def is_expired(enrollment: NodeEnrollment) -> bool:
    """Compare UTC timestamps from both PostgreSQL and SQLite-backed tests."""
    expires_at = enrollment.expires_at
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires_at <= now


def cleanup_expired(db: Session) -> None:
    now = datetime.now(timezone.utc)
    db.query(NodeEnrollment).filter(NodeEnrollment.expires_at <= now).delete(
        synchronize_session=False
    )
    db.commit()


def begin_enrollment(
    db: Session,
    *,
    name: str,
    source_ip: str,
    port: int,
    tls_fingerprint: str,
    agent_token: str,
) -> tuple[NodeEnrollment, str]:
    cleanup_expired(db)
    host = validate_remote_node_host(
        f"https://{source_ip}:{port}", tls_fingerprint, is_local=False
    )
    claim_secret = secrets.token_urlsafe(48)
    enrollment = NodeEnrollment(
        claim_hash=_claim_hash(claim_secret),
        display_code=_display_code(),
        name=name.strip(),
        host=host,
        tls_fingerprint=tls_fingerprint,
        auth_token_enc=encrypt_node_token(agent_token),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ENROLLMENT_TTL_MINUTES),
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment, claim_secret


def find_by_claim(db: Session, claim_secret: str) -> NodeEnrollment | None:
    if len(claim_secret) < 32:
        return None
    return (
        db.query(NodeEnrollment)
        .filter(NodeEnrollment.claim_hash == _claim_hash(claim_secret))
        .first()
    )


def approve(db: Session, enrollment: NodeEnrollment) -> Node:
    if is_expired(enrollment) or enrollment.status != "pending":
        raise ValueError("Enrollment ist abgelaufen oder nicht mehr offen")
    node = Node(
        name=enrollment.name,
        host=enrollment.host,
        auth_token_enc=enrollment.auth_token_enc,
        tls_fingerprint=enrollment.tls_fingerprint,
        is_local=False,
        status="unknown",
    )
    db.add(node)
    db.flush()
    enrollment.node_id = node.id
    # Noch nicht committen: Der Router prueft zuerst TLS-Pin, Docker und den
    # Agent-Token. Bei einem Fehler rollt die Session auf "pending" zurueck.
    enrollment.status = "verifying"
    db.flush()
    return node


def mark_claimed(db: Session, enrollment: NodeEnrollment) -> None:
    enrollment.status = "claimed"
    enrollment.claimed_at = datetime.now(timezone.utc)
    enrollment.auth_token_enc = "claimed"
    enrollment.expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=CLAIMED_RETENTION_MINUTES
    )
    db.commit()
