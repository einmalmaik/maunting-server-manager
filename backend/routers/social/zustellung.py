"""Die gerätebezogene Zustellung — annehmen, abholen, quittieren.

Drei Routen, alle unter ``/api/social``:

* ``POST /e2ee/deliveries`` — ein Stapel Zustellungen annehmen.
* ``GET  /e2ee/deliveries`` — die offenen Auftraege der eigenen Inbox holen.
* ``POST /e2ee/deliveries/ack`` — die verarbeiteten Auftraege quittieren.

Der Beleg des Geraets kommt als Kopfzeile (``X-Geraete-Beleg``), nicht in der
Adresse: was in der Adresse steht, steht im Zugriffsprotokoll des Webservers
und in jedem Zwischenspeicher davor.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, session_familie, verify_csrf
from models import User
from schemas.social import (
    E2eeZustellCreate,
    E2eeZustellQuittung,
)
from services.social import zustellung
from services.social_service import SocialService
from routers.social.basis import _check_social_enabled, mailbox_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Der Geraetebeleg: die Unterschrift des Geraets ueber die Anfrage.
GeraeteBeleg = Annotated[str | None, Header(alias="X-Geraete-Beleg")]


def _beleg_fehler(fehler: Exception) -> HTTPException:
    """Die Fehler der Zustellung als HTTP — ohne den Grund preiszugeben."""
    if isinstance(fehler, zustellung.KonfliktFehler):
        return HTTPException(status_code=409, detail=fehler.detail)
    if isinstance(fehler, zustellung.BeweisFehler):
        return HTTPException(status_code=403, detail="Der Geraetebeleg passt nicht.")
    if isinstance(fehler, zustellung.UnbekanntesZielFehler):
        return HTTPException(status_code=404, detail="Unbekanntes Zielgeraet.")
    if isinstance(fehler, zustellung.ZielNichtErlaubtFehler):
        return HTTPException(status_code=403, detail="Dieses Zielgeraet ist nicht zulaessig.")
    return HTTPException(status_code=400, detail=fehler.detail)


@router.post(
    "/e2ee/deliveries",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def zustellungen_annehmen(
    req: E2eeZustellCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    """Nimmt einen Stapel Zustellungen an.

    **Erst die Berechtigung, dann die Idempotenz.** Wer in diese Mailbox nicht
    schreiben darf, erfährt nichts darueber, ob eine Zustellkennung schon
    bekannt ist. Die Pruefung ist dieselbe wie beim Relais
    (`assert_mailbox_zugang`): sie entscheidet, wer in welches Gespraech
    schreiben darf, und sie kennt die Mailbox, in der der Absender schreibt.
    """
    SocialService.assert_mailbox_zugang(db, current_user.id, req.mailbox_id, nachweis)
    try:
        return zustellung.annehmen(
            db,
            sender_id=current_user.id,
            mailbox_id=req.mailbox_id,
            geraet_id=req.geraet_id,
            payloads=[p.model_dump() for p in req.payloads],
            eintraege=[e.model_dump() for e in req.eintraege],
            signatur=req.signatur,
        )
    except zustellung.ZustellFehler as fehler:
        raise _beleg_fehler(fehler) from fehler


@router.get(
    "/e2ee/deliveries",
    dependencies=[Depends(_check_social_enabled)],
)
def zustellungen_holen(
    inbox_hash: str = Query(..., min_length=64, max_length=64),
    seit_seq: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
    beleg: GeraeteBeleg = None,
) -> dict:
    """Holt die offenen Auftraege der eigenen Zustelladresse.

    Der Abruf loescht nichts. Das Loeschen ist die Quittung — ein Abruf, der
    zugleich aufraeumt, wuerde eine verlorene Antwort mit einem verlorenen
    Inhalt bezahlen.
    """
    if not beleg:
        raise HTTPException(status_code=403, detail="Der Geraetebeleg fehlt.")
    try:
        return zustellung.lesen(
            db,
            user_id=current_user.id,
            familie=familie,
            inbox_hash=inbox_hash,
            seit_seq=seit_seq,
            limit=limit,
            signatur=beleg,
        )
    except zustellung.ZustellFehler as fehler:
        raise _beleg_fehler(fehler) from fehler


@router.post(
    "/e2ee/deliveries/ack",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def zustellungen_quittieren(
    req: E2eeZustellQuittung,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Quittiert die verarbeiteten Auftraege — und loescht genau sie.

    Die Quittung kommt **nach** dem lokalen Commit: erst wenn die Nachricht
    entschluesselt, verarbeitet und gespeichert ist, gilt sie als zugestellt.
    """
    try:
        return zustellung.quittieren(
            db,
            user_id=current_user.id,
            familie=familie,
            inbox_hash=req.inbox_hash,
            geraet_id=req.geraet_id,
            delivery_ids=list(req.delivery_ids),
            signatur=req.signatur,
        )
    except zustellung.ZustellFehler as fehler:
        raise _beleg_fehler(fehler) from fehler
