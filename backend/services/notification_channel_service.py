"""Der dauerhafte Ausweis eines Geräts für den Hintergrundkanal.

Anlegen verlangt einen frischen Nachweis (`passkey_service.frischer_nachweis_fehlt`)
— ein neuer Dauerzugang entsteht nie aus einem bloß vorhandenen Token. Ausgeben
wird der Ausweis genau einmal; gespeichert ist nur sein Hash.

Widerrufen nimmt drei Dinge mit, wie jeder Entzug (Punkt 20): die Zeile, die
offene Verbindung (`SyncEventService.trenne`) und — beim Abmelden — die
Zustelladressen derselben Sitzung.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models.notification_channel import NotificationChannel
from models.user import User
from services.sync_event_service import SyncEventService

logger = logging.getLogger("msm.notification_channels")

#: Wie viele Geräte ein Konto für den Hintergrundkanal anmelden darf.
#:
#: Jeder Ausweis ist ein dauerhafter Zugang zum Ereignisstrom. Fünf decken
#: Telefon, Tablet, zwei Rechner und ein Ersatzgerät ab; wer mehr braucht,
#: widerruft eines. Der älteste fällt beim Überschreiten heraus, damit ein
#: vergessenes Gerät nicht dauerhaft mitliest.
MAX_KANAELE = 5

#: So lang ist der Ausweis. 32 Byte Zufall, hex — nicht ratbar, und die Länge
#: steht im Schema, damit weder kürzere noch längere Werte ankommen.
AUSWEIS_LAENGE = 64


def _hash(ausweis: str) -> str:
    return hashlib.sha256(ausweis.strip().lower().encode("utf-8")).hexdigest()


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Kanal:
    """Was der Ereignisstrom von einem Ausweis braucht — und nicht mehr."""

    id: int
    user_id: int


def anlegen(
    db: Session, user: User, *, familie: str | None, label: str | None
) -> tuple[NotificationChannel, str]:
    """Legt einen Kanal an und gibt ``(Zeile, Ausweis)`` zurück.

    Der Ausweis verlässt diese Funktion genau einmal. Er steht nirgends sonst:
    nicht im Log, nicht in der Zeile, nicht in der Antwort eines späteren
    Aufrufs.
    """
    offen = (
        db.query(NotificationChannel)
        .filter(
            NotificationChannel.user_id == user.id,
            NotificationChannel.revoked_at.is_(None),
        )
        .order_by(NotificationChannel.created_at.asc())
        .all()
    )
    # Über dem Deckel fällt der älteste offene Kanal. Nicht der, der gerade
    # dazukommt — sonst legte ein Konto in einer Schleife Ausweise an, ohne
    # dass je einer gültig bliebe.
    while len(offen) >= MAX_KANAELE:
        alt = offen.pop(0)
        alt.revoked_at = _jetzt()
        SyncEventService.trenne(alt.user_id, f"kanal:{alt.id}")
        logger.info("Kanal %d verdraengt (Deckel %d)", alt.id, MAX_KANAELE)

    ausweis = secrets.token_hex(AUSWEIS_LAENGE // 2)
    kanal = NotificationChannel(
        user_id=user.id,
        familie=(familie or None),
        label=(label or "").strip()[:64] or None,
        token_hash=_hash(ausweis),
    )
    db.add(kanal)
    db.commit()
    db.refresh(kanal)
    logger.info(
        "Kanal %d angelegt (Konto %d, Familie %s)", kanal.id, user.id, "ja" if familie else "nein"
    )
    return kanal, ausweis


def ist_gueltig(db: Session, ausweis: str | None) -> bool:
    """Gilt dieser Ausweis — ohne Konto, ohne `last_seen_at`, ohne Nebenwirkung.

    Für `verify_csrf`: dort wird nur entschieden, ob der Header ein
    ausdrücklicher Ausweis ist (kein Ambient-Cookie, also kein CSRF-Weg).
    Ein **erfundener** Wert darf das nicht erreichen, sonst umginge ein
    Cookie-Request mit beliebigem 64-stelligen Header die Prüfung.
    """
    wert = (ausweis or "").strip()
    if len(wert) != AUSWEIS_LAENGE or "." in wert:
        return False
    return (
        db.query(NotificationChannel.id)
        .filter(
            NotificationChannel.token_hash == _hash(wert),
            NotificationChannel.revoked_at.is_(None),
        )
        .first()
        is not None
    )


def aus_bearer(db: Session, authorization: str | None) -> tuple[Kanal, User] | None:
    """Löst ``Authorization: Bearer <ausweis>`` zu Konto und Kanal auf.

    Gibt ``None`` zurück, wenn der Kopf fehlt, der Ausweis unbekannt oder
    widerrufen ist, oder das Konto nicht mehr aktiv ist. Ein widerrufener
    Ausweis verhält sich wie ein unbekannter; es gibt keinen Hinweis darauf,
    dass er einmal gültig war.
    """
    kopf = (authorization or "").strip()
    if not kopf.lower().startswith("bearer "):
        return None
    ausweis = kopf[7:].strip()
    if len(ausweis) != AUSWEIS_LAENGE:
        return None
    kanal = (
        db.query(NotificationChannel)
        .filter(
            NotificationChannel.token_hash == _hash(ausweis),
            NotificationChannel.revoked_at.is_(None),
        )
        .first()
    )
    if kanal is None:
        return None
    konto = db.query(User).filter_by(id=kanal.user_id).first()
    if konto is None or not konto.is_active:
        return None
    kanal.last_seen_at = _jetzt()
    db.commit()
    return Kanal(id=kanal.id, user_id=kanal.user_id), konto


def widerrufen(db: Session, kanal: NotificationChannel) -> None:
    """Nimmt die Zeile und die offene Verbindung mit (Punkt 20)."""
    kanal.revoked_at = _jetzt()
    db.commit()
    SyncEventService.trenne(kanal.user_id, f"kanal:{kanal.id}")
    logger.info("Kanal %d widerrufen", kanal.id)


def widerrufen_familie(db: Session, user_id: int, familie: str | None) -> int:
    """Beim Abmelden: die Kanäle **dieser** Sitzung, nicht die der anderen.

    Ohne Familie (Altbestand oder Cookie ohne Familienangabe) fallen alle
    Kanäle des Kontos — ein Ausweis, der keinem Gerät zuzuordnen ist, soll ein
    Abmelden nicht überleben.
    """
    abfrage = db.query(NotificationChannel).filter(
        NotificationChannel.user_id == user_id,
        NotificationChannel.revoked_at.is_(None),
    )
    if familie:
        abfrage = abfrage.filter(NotificationChannel.familie == familie)
    kanaele = abfrage.all()
    jetzt = _jetzt()
    for kanal in kanaele:
        kanal.revoked_at = jetzt
        SyncEventService.trenne(kanal.user_id, f"kanal:{kanal.id}")
    if kanaele:
        db.commit()
        logger.info("Abmelden: %d Kanal/Kanaele widerrufen", len(kanaele))
    return len(kanaele)


def liste(db: Session, user_id: int) -> list[NotificationChannel]:
    """Die offenen Kanäle eines Kontos, ältester zuerst."""
    return (
        db.query(NotificationChannel)
        .filter(
            NotificationChannel.user_id == user_id,
            NotificationChannel.revoked_at.is_(None),
        )
        .order_by(NotificationChannel.created_at.asc())
        .all()
    )
