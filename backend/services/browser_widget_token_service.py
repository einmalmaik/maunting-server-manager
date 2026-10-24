"""Wetter-Widget-Token: ausstellen, prüfen, widerrufen.

Der Klartext verlässt den Server genau einmal (beim Ausstellen). Gespeichert
wird nur der SHA-256-Hash. Gültig ist das Token ausschließlich für die
Widget-Wetterroute; die normale Sitzung und alle anderen Routen bleiben außen
vor.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.browser_widget_token import BrowserWidgetToken

SCOPE = "browser.wetter"
TTL_DAYS = 30
MAX_AKTIVE = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ausstellen(db: Session, user_id: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    ablauf = now + timedelta(days=TTL_DAYS)
    aktive = (
        db.query(BrowserWidgetToken)
        .filter(
            BrowserWidgetToken.user_id == user_id,
            BrowserWidgetToken.scope == SCOPE,
            BrowserWidgetToken.revoked_at.is_(None),
            BrowserWidgetToken.expires_at > now,
        )
        .order_by(BrowserWidgetToken.created_at.asc())
        .all()
    )
    for alt in aktive[: max(0, len(aktive) - MAX_AKTIVE + 1)]:
        alt.revoked_at = now
    klar = f"pw_{secrets.token_urlsafe(32)}"
    db.add(
        BrowserWidgetToken(
            user_id=user_id,
            token_hash=_hash(klar),
            scope=SCOPE,
            expires_at=ablauf,
        )
    )
    db.commit()
    return klar, ablauf


def pruefen(db: Session, klar: str) -> int | None:
    """Nutzer-ID zum Token oder `None`. Nur Tokens mit `pw_`-Präfix."""
    if not klar.startswith("pw_"):
        return None
    now = datetime.now(timezone.utc)
    zeile = (
        db.query(BrowserWidgetToken)
        .filter(
            BrowserWidgetToken.token_hash == _hash(klar),
            BrowserWidgetToken.scope == SCOPE,
            BrowserWidgetToken.revoked_at.is_(None),
            BrowserWidgetToken.expires_at > now,
        )
        .first()
    )
    if zeile is None:
        return None
    zeile.last_used_at = now
    db.commit()
    return zeile.user_id


def widerrufen(db: Session, user_id: int) -> int:
    now = datetime.now(timezone.utc)
    anzahl = (
        db.query(BrowserWidgetToken)
        .filter(
            BrowserWidgetToken.user_id == user_id,
            BrowserWidgetToken.scope == SCOPE,
            BrowserWidgetToken.revoked_at.is_(None),
        )
        .update({BrowserWidgetToken.revoked_at: now}, synchronize_session=False)
    )
    db.commit()
    return int(anzahl or 0)
