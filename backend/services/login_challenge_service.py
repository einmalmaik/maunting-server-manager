"""LoginChallenge-Verwaltung fuer In-flight-Logins (aktuell OAuth+2FA).

Sicherheits-Invarianten:
- Opaque Tokens werden NIE im Klartext gespeichert (nur SHA-256-Hash).
- Single-use: consume setzt ``consumed_at`` und ist nicht wiederholbar.
- TTL: Default 5 Minuten. Aelter Challenges werden bei Lookup uebergangen.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import LoginChallenge
from models.login_challenge import hash_challenge_token


DEFAULT_TTL_SECONDS = 300  # 5 Minuten


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_challenge(
    db: Session,
    purpose: str,
    user_id: int | None = None,
    payload: dict | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Erzeugt einen neuen Challenge-Eintrag und gibt das opaque Plain-Token zurueck.

    Der Client erhaelt das Token, der Server speichert nur den Hash.
    """
    plain = secrets.token_urlsafe(32)
    expires = _now() + timedelta(seconds=ttl_seconds)
    row = LoginChallenge(
        token_hash=hash_challenge_token(plain),
        purpose=purpose,
        user_id=user_id,
        payload_json=json.dumps(payload) if payload else None,
        expires_at=expires,
    )
    db.add(row)
    db.commit()
    return plain


def lookup_valid(db: Session, plain: str, purpose: str) -> LoginChallenge | None:
    """Sucht eine noch nicht abgelaufene, nicht konsumierte Challenge.

    Returns None bei: leerer Input, falscher Purpose, abgelaufen oder bereits konsumiert.
    """
    if not plain:
        return None
    row = (
        db.query(LoginChallenge)
        .filter(
            LoginChallenge.token_hash == hash_challenge_token(plain),
            LoginChallenge.purpose == purpose,
            LoginChallenge.consumed_at.is_(None),
            LoginChallenge.expires_at > _now(),
        )
        .first()
    )
    return row


def consume(db: Session, row: LoginChallenge) -> None:
    """Markiert eine Challenge als konsumiert (single-use)."""
    row.consumed_at = _now()
    db.commit()


def versuch_belegen(db: Session, row: LoginChallenge, hoechstens: int) -> int | None:
    """Belegt atomar einen von ``hoechstens`` Versuchen; ``None``, wenn keiner frei ist.

    Belegt wird vor der Pruefung des Codes, nicht danach. Bis 27.09.2026 las jede
    Anfrage den Zaehler, pruefte und schrieb ihn zurueck: parallele Fehlversuche
    ueberschrieben sich, und die Challenge blieb nach drei Fehlversuchen gueltig.

    Das UPDATE greift nur, wenn ``payload_json`` noch so aussieht wie gelesen.
    Schlaegt es fehl, hat eine andere Anfrage gerade einen Versuch belegt; nach
    ``hoechstens`` solchen Runden ist sicher keiner mehr frei.
    """
    for _ in range(hoechstens):
        treffer = (
            db.query(LoginChallenge.payload_json)
            .filter(
                LoginChallenge.id == row.id,
                LoginChallenge.consumed_at.is_(None),
                LoginChallenge.expires_at > _now(),
            )
            .first()
        )
        if treffer is None:
            return None
        alt = treffer[0]
        daten = json.loads(alt) if alt else {}
        nummer = int(daten.get("versuche", 0)) + 1
        if nummer > hoechstens:
            return None
        belegt = (
            db.query(LoginChallenge)
            .filter(
                LoginChallenge.id == row.id,
                LoginChallenge.consumed_at.is_(None),
                LoginChallenge.payload_json.is_not_distinct_from(alt),
            )
            .update(
                {LoginChallenge.payload_json: json.dumps({**daten, "versuche": nummer})},
                synchronize_session=False,
            )
        )
        db.commit()
        if belegt == 1:
            return nummer
    return None


def cleanup_expired(db: Session) -> int:
    """Loescht abgelaufene Challenges. Idempotent. Wird vom Lifespan aufgerufen."""
    cutoff = _now()
    deleted = (
        db.query(LoginChallenge)
        .filter(LoginChallenge.expires_at < cutoff)
        .delete()
    )
    db.commit()
    return deleted


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "create_challenge",
    "lookup_valid",
    "consume",
    "versuch_belegen",
    "cleanup_expired",
]
