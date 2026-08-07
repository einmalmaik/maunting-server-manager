"""Kurzlebiger Einmal-Login aus dem Shop direkt in das MSM-Panel.

Der Kunde klickt im Kundenbereich seines Hosters auf "Server verwalten" und
landet angemeldet in seinem Panel — ohne zweites MSM-Passwort.

Sicherheitsgrenzen
------------------
- Der Token lebt fuenf Minuten und gilt genau einmal. Der Verbrauch ist ein
  bedingtes UPDATE und damit auch bei parallelen Klicks eindeutig.
- Gespeichert wird nur der SHA-256-Hash. Der Klartext existiert einzig im Link
  des Kunden und erscheint weder im Audit noch in Logs.
- Das Ziel ist auf eine feste Liste panelinterner Pfade begrenzt. Damit ist der
  Handoff kein offener Redirect.
- Der Token authentifiziert, er autorisiert nicht: welche Server der Kunde
  danach sieht, entscheidet unveraendert die serverbezogene Rechtepruefung.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import HosterHandoff, HosterIntegration, HosterService, User
from models.hoster import hash_token
from services import audit_service


HANDOFF_TTL = timedelta(minutes=5)
MAX_ACTIVE_HANDOFFS_PER_USER = 5
_DEFAULT_TARGET = "/servers"
# Nur diese Ziele sind erlaubt. `/servers/<id>` deckt den Direktsprung auf den
# gekauften Server ab; alles andere waere ein frei steuerbarer Redirect.
_TARGET_RE = re.compile(r"^/(servers|dashboard)(/[0-9]{1,12})?$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def normalize_target_path(value: str | None) -> str:
    """Prueft das Sprungziel gegen eine feste Allowlist interner Pfade."""
    path = (value or "").strip() or _DEFAULT_TARGET
    if len(path) > 128 or not _TARGET_RE.fullmatch(path):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_handoff_target", "message": "errors.invalid_handoff_target"},
        )
    return path


def create_handoff(
    db: Session,
    *,
    integration: HosterIntegration,
    service: HosterService,
    target_path: str | None,
) -> tuple[HosterHandoff, str]:
    """Erzeugt einen Einmal-Token fuer den Kunden dieses Vertrags.

    Rueckgabe ist `(Datensatz, Klartext-Token)`. Der Klartext wird ausschliesslich
    an den aufrufenden Shop zurueckgegeben und nirgends gespeichert.
    """
    user = (
        db.query(User)
        .filter(User.id == service.identity.user_id, User.is_active.is_(True))
        .first()
    )
    if user is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "handoff_user_inactive", "message": "errors.handoff_user_inactive"},
        )
    path = normalize_target_path(target_path)

    # Alte, noch offene Token desselben Benutzers verfallen. Sonst koennte ein
    # Shop unbegrenzt viele gueltige Einmal-Links auf Vorrat erzeugen.
    _expire_surplus(db, user_id=user.id)

    token = secrets.token_urlsafe(32)
    handoff = HosterHandoff(
        id=str(uuid4()),
        integration_id=integration.id,
        service_id=service.id,
        user_id=user.id,
        token_hash=hash_token(token),
        target_path=path,
        expires_at=_now() + HANDOFF_TTL,
    )
    db.add(handoff)
    audit_service.record_privileged_action(
        db,
        user_id=integration.service_user_id,
        action="hoster.handoff.created",
        target_type="hoster_service",
        target_id=None,
        # Bewusst ohne Token und ohne Kundenkennung.
        details={"integration": integration.slug, "service_id": service.id, "target": path},
        origin="external",
        correlation_id=service.correlation_id,
    )
    db.commit()
    db.refresh(handoff)
    return handoff, token


def _expire_surplus(db: Session, *, user_id: int) -> None:
    """Begrenzt die Zahl gleichzeitig gueltiger Token je Benutzer."""
    active = (
        db.query(HosterHandoff)
        .filter(
            HosterHandoff.user_id == user_id,
            HosterHandoff.consumed_at.is_(None),
            HosterHandoff.expires_at > _now(),
        )
        .order_by(HosterHandoff.created_at.desc())
        .all()
    )
    for surplus in active[MAX_ACTIVE_HANDOFFS_PER_USER - 1 :]:
        surplus.expires_at = _now()


def redeem(db: Session, token: str) -> tuple[User, str]:
    """Loest einen Handoff-Token genau einmal ein.

    Rueckgabe ist `(Benutzer, Zielpfad)`. Jeder Fehlerfall antwortet einheitlich,
    damit ein Angreifer nicht unterscheiden kann, ob ein Token unbekannt,
    abgelaufen oder bereits verbraucht ist.
    """
    value = (token or "").strip()
    if not value or len(value) > 256:
        raise _invalid()
    token_hash = hash_token(value)
    now = _now()

    # Atomarer Verbrauch: nur genau ein paralleler Klick gewinnt. Ein bedingtes
    # UPDATE ist hier verlaesslicher als Lesen-und-dann-Schreiben und
    # funktioniert unabhaengig von Zeilensperren der Datenbank.
    consumed = (
        db.query(HosterHandoff)
        .filter(
            HosterHandoff.token_hash == token_hash,
            HosterHandoff.consumed_at.is_(None),
            HosterHandoff.expires_at > now,
        )
        .update({"consumed_at": now}, synchronize_session=False)
    )
    db.commit()
    if consumed != 1:
        raise _invalid()

    handoff = (
        db.query(HosterHandoff).filter(HosterHandoff.token_hash == token_hash).first()
    )
    if handoff is None:
        raise _invalid()
    user = (
        db.query(User)
        .filter(User.id == handoff.user_id, User.is_active.is_(True))
        .first()
    )
    if user is None:
        raise _invalid()

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="hoster.handoff.redeemed",
        target_type="hoster_service",
        target_id=None,
        details={"handoff_id": handoff.id, "target": handoff.target_path},
        origin="external",
        commit=True,
    )
    return user, handoff.target_path


def _invalid() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "handoff_invalid", "message": "errors.handoff_invalid"},
    )


def cleanup_expired(db: Session) -> int:
    """Entfernt abgelaufene und verbrauchte Token nach einem Tag."""
    cutoff = _now() - timedelta(days=1)
    removed = (
        db.query(HosterHandoff)
        .filter(HosterHandoff.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(removed or 0)
