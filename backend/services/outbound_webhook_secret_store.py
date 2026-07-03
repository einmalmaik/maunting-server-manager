"""DB-gestuetzter Secret-Store fuer Webhook-Secrets (DIS-verschluesselt).

Frueher In-Memory (Prozess-Lifetime), jetzt persistent in der
``webhook_subscriptions`` Tabelle mit DIS AES-256-GCM verschluesselt.
Das loest das Problem, dass Secrets nach einem Restart verloren gingen.

Sicherheits-Invarianten:
- Klartext-Secrets werden NIE im Plain gespeichert — nur DIS-verschluesselt.
- ``get`` ist die einzige Methode, die Klartext zurueckgibt (nur beim Versand).
- Secrets werden nie geloggt.
"""
from __future__ import annotations

from database import SessionLocal
from models import WebhookSubscription
from services.auth_service import AuthService


def put(subscription_id: int, secret: str) -> None:
    if not secret:
        return
    enc = AuthService.encrypt_secret(secret, aad=f"msm:webhook:{subscription_id}:secret")
    db = SessionLocal()
    try:
        sub = db.get(WebhookSubscription, subscription_id)
        if sub:
            sub.secret_encrypted = enc
            db.commit()
    finally:
        db.close()


def get(subscription_id: int) -> str | None:
    db = SessionLocal()
    try:
        sub = db.get(WebhookSubscription, subscription_id)
        if not sub or not sub.secret_encrypted:
            return None
        try:
            return AuthService.decrypt_secret(
                sub.secret_encrypted,
                aad=f"msm:webhook:{subscription_id}:secret",
            )
        except Exception:
            return None
    finally:
        db.close()


def delete(subscription_id: int) -> None:
    db = SessionLocal()
    try:
        sub = db.get(WebhookSubscription, subscription_id)
        if sub:
            sub.secret_encrypted = None
            db.commit()
    finally:
        db.close()


def reset_for_tests() -> None:
    """Loescht alle verschluesselten Secrets ( fuer Test-Isolation)."""
    db = SessionLocal()
    try:
        for sub in db.query(WebhookSubscription).all():
            sub.secret_encrypted = None
        db.commit()
    finally:
        db.close()
