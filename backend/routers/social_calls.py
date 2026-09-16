"""Anrufe: Einladungen, LiveKit-Zugangstoken und die Betreiber-Umschaltung.

Eigene Datei, weil `social.py` mit Freundschaften, Umschlaegen, Gruppen und
Stories bereits gut gefuellt ist und Anrufe ein abgeschlossenes Thema sind.

Was hier **nicht** steht, ist Absicht: keine Anrufhistorie, keine Dauer, kein
Gespraechspartner in der Datenbank. Wer mit wem spricht, weiss der Prozess, so
lange der Raum lebt, und danach niemand mehr.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import ChatGroupMember, User
from schemas.calls import (
    CallKeyRelayRequest,
    CallParticipantCountResponse,
    CallTokenRequest,
    CallTokenResponse,
    DirectCallResponse,
    LivekitConfigUpdate,
    LivekitStatusResponse,
    LivekitTestRequest,
    LivekitTestResponse,
)
from schemas.social import GroupCallRoomJoinRequest, GroupCallRoomResponse
from services import audit_service, livekit_service
from services.call_room_service import (
    EINLADUNG_TTL_SEKUNDEN,
    GRUPPE_MAX_TEILNEHMER,
    CallRoomService,
    GroupCallRoomRegistry,
)
from services.livekit_service import LivekitZielAbgelehnt
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social/calls", tags=["calls"])
admin_router = APIRouter(prefix="/api/admin/messenger/livekit", tags=["calls-admin"])


def _check_social_enabled(db: Session = Depends(get_db)):
    SocialService.assert_social_enabled(db)


def _konfiguration_oder_fehler(db: Session) -> livekit_service.LivekitKonfiguration:
    konf = livekit_service.konfiguration(db)
    if not konf.konfiguriert:
        # Ehrlich scheitern statt ein Token auszugeben, das nirgends gilt: der
        # Client wuerde sonst ins Leere verbinden und der Anrufer haette nur ein
        # stummes Fenster.
        raise HTTPException(
            status_code=503,
            detail="Der Anrufserver ist nicht eingerichtet. Bitte an die Panel-Verwaltung wenden.",
        )
    return konf


# ── Einladungen zu Zweiergespraechen ────────────────────────────────────────


@router.post(
    "/invite/{target_user_id}",
    response_model=DirectCallResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def einladung_erstellen(
    target_user_id: int,
    mode: Literal["audio", "video"] = Query("audio"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Oeffnet einen Anrufraum und laesst beim Freund klingeln."""
    ziel = db.query(User).filter_by(id=target_user_id, is_active=True).first()
    if not ziel:
        raise HTTPException(status_code=404, detail="Zielnutzer nicht gefunden")
    if ziel.id == user.id:
        raise HTTPException(status_code=400, detail="Man ruft sich nicht selbst an.")
    if not SocialService.is_confirmed_friend(db, user.id, target_user_id):
        raise HTTPException(
            status_code=403,
            detail="Anrufe sind nur zwischen bestätigten Freunden möglich.",
        )
    _konfiguration_oder_fehler(db)

    raum = CallRoomService.issue(user.id, target_user_id)
    SyncEventService.publish(
        {
            "type": "direct_call_invitation",
            "signaling_token": raum,
            "caller_id": user.id,
            "caller_username": user.username,
            "caller_avatar_url": user.avatar_url,
            "mode": mode,
            "recipient_id": target_user_id,
            "expires_in": int(EINLADUNG_TTL_SEKUNDEN),
        },
        user_id=target_user_id,
    )
    return {
        "signaling_token": raum,
        "recipient_id": target_user_id,
        "expires_in": int(EINLADUNG_TTL_SEKUNDEN),
    }


@router.post(
    "/{signaling_token}/invite/{target_user_id}",
    response_model=DirectCallResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def teilnehmer_nachholen(
    signaling_token: str,
    target_user_id: int,
    mode: Literal["audio", "video"] = Query("audio"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Holt einen Freund in ein laufendes Zweiergespraech.

    Zwei Schranken: der Einladende muss selbst im Raum berechtigt sein, und der
    Eingeladene muss ein bestaetigter Freund **des Einladenden** sein. Damit
    kann niemand ueber einen Raum hinweg Fremde an Dritte heranfuehren.
    """
    ziel = db.query(User).filter_by(id=target_user_id, is_active=True).first()
    if not ziel:
        raise HTTPException(status_code=404, detail="Zielnutzer nicht gefunden")
    if not SocialService.is_confirmed_friend(db, user.id, target_user_id):
        raise HTTPException(
            status_code=403,
            detail="Nur bestätigte Freunde lassen sich in ein Gespräch holen.",
        )
    if not CallRoomService.add_participant(signaling_token, user.id, target_user_id):
        raise HTTPException(
            status_code=404, detail="Anruf nicht gefunden oder bereits beendet."
        )

    SyncEventService.publish(
        {
            "type": "direct_call_invitation",
            "signaling_token": signaling_token,
            "caller_id": user.id,
            "caller_username": user.username,
            "caller_avatar_url": user.avatar_url,
            "mode": mode,
            "recipient_id": target_user_id,
            "expires_in": int(EINLADUNG_TTL_SEKUNDEN),
        },
        user_id=target_user_id,
    )
    return {
        "signaling_token": signaling_token,
        "recipient_id": target_user_id,
        "expires_in": int(EINLADUNG_TTL_SEKUNDEN),
    }


@router.post(
    "/{signaling_token}/reject",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def einladung_ablehnen(
    signaling_token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    anrufer_id = CallRoomService.reject(signaling_token, user.id)
    if anrufer_id is None:
        raise HTTPException(
            status_code=404, detail="Anruf nicht gefunden oder bereits abgelaufen."
        )
    SyncEventService.publish(
        {
            "type": "direct_call_rejected",
            "signaling_token": signaling_token,
            "recipient_id": user.id,
        },
        user_id=anrufer_id,
    )
    return {"ok": True}


@router.post(
    "/{signaling_token}/cancel",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def einladung_abbrechen(
    signaling_token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Der Anrufer legt auf, bevor abgenommen wurde.

    Entwertet die Einladung, damit ein spaetes Annehmen keinen Raum mehr
    oeffnet, und laesst das Klingeln beim Gegenueber serverseitig verstummen.
    """
    empfaenger_id = CallRoomService.cancel(signaling_token, user.id)
    if empfaenger_id is None:
        raise HTTPException(
            status_code=404, detail="Anruf nicht gefunden oder bereits abgelaufen."
        )
    SyncEventService.publish(
        {
            "type": "direct_call_cancelled",
            "signaling_token": signaling_token,
            "caller_id": user.id,
            "recipient_id": empfaenger_id,
        },
        user_id=empfaenger_id,
    )
    return {"ok": True}


# ── Zugangstoken ────────────────────────────────────────────────────────────


@router.post(
    "/token",
    response_model=CallTokenResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def zugangstoken(
    req: CallTokenRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Gibt ein kurzlebiges LiveKit-Token fuer einen bereits geoeffneten Raum aus.

    Der Raum entsteht nie hier: entweder hat ihn eine Einladung geoeffnet oder
    `POST /api/social/groups/{id}/calls`. Diese Trennung ist die eigentliche
    Schranke — ein Token bekommt nur, wer den Raumnamen kennt **und** an ihm
    berechtigt ist.
    """
    konf = _konfiguration_oder_fehler(db)

    if req.art == "direkt":
        if CallRoomService.is_consumed(req.raum):
            raise HTTPException(status_code=410, detail="Der Anruf ist bereits beendet.")
        if not CallRoomService.authorize(req.raum, user.id):
            raise HTTPException(
                status_code=403, detail="Für diesen Anruf liegt keine Einladung vor."
            )
    else:
        if req.group_id is None:
            raise HTTPException(status_code=400, detail="Gruppenkennung fehlt.")
        SocialService.assert_group_call_permission(
            db, req.group_id, user.id, "join_group_calls"
        )
        raum = GroupCallRoomRegistry.get(req.raum)
        if raum is None or raum[0] != req.group_id:
            raise HTTPException(
                status_code=404, detail="Gruppenanruf nicht gefunden oder abgelaufen."
            )

    identity = f"u{user.id}"
    token = livekit_service.zugangstoken(
        req.raum,
        identity,
        user.username,
        api_key=konf.api_key,
        api_secret=konf.api_secret,
    )
    return {
        "url": konf.client_url,
        "token": token,
        "raum": req.raum,
        "identity": identity,
        "ttl": livekit_service.TOKEN_TTL_SEKUNDEN,
    }


@router.post(
    "/{raum}/key",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def raumschluessel_weiterreichen(
    raum: str,
    req: CallKeyRelayRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Reicht den verpackten Raumschlüssel an einen Teilnehmer weiter.

    Das Panel sieht nur Chiffrat. Der Schlüssel entsteht im Browser des
    Anrufenden, wird gegen den veröffentlichten Schlüssel des Empfängers
    verpackt und hier nur zugestellt — deshalb kann der Medienserver die
    Gespräche nicht mithören, obwohl alle Ströme durch ihn laufen.

    Beide Seiten müssen im selben Raum berechtigt sein. Sonst wäre das hier ein
    Weg, jedem Konto beliebige Daten zuzustellen.
    """
    gruppe = GroupCallRoomRegistry.get(raum)
    if gruppe is not None:
        group_id = gruppe[0]
        SocialService.assert_group_call_permission(db, group_id, user.id, "join_group_calls")
        if not SocialService.has_group_permission(
            db, group_id, req.target_user_id, "join_group_calls"
        ):
            raise HTTPException(
                status_code=403, detail="Der Empfänger darf diesem Gruppenanruf nicht beitreten."
            )
    else:
        berechtigte = CallRoomService.berechtigte(raum)
        if user.id not in berechtigte:
            raise HTTPException(status_code=404, detail="Anruf nicht gefunden.")
        if req.target_user_id not in berechtigte:
            raise HTTPException(
                status_code=403, detail="Der Empfänger gehört nicht zu diesem Anruf."
            )

    SyncEventService.publish(
        {
            "type": "call_key",
            "raum": raum,
            "from_user_id": user.id,
            "ciphertext": req.ciphertext,
        },
        user_id=req.target_user_id,
    )
    return {"ok": True}


@router.get(
    "/{raum}/teilnehmer",
    response_model=CallParticipantCountResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def teilnehmerzahl(
    raum: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Wie viele gerade im Raum sind. Nur fuer Berechtigte."""
    gruppe = GroupCallRoomRegistry.get(raum)
    if gruppe is not None:
        SocialService.assert_group_call_permission(db, gruppe[0], user.id, "join_group_calls")
    elif user.id not in CallRoomService.berechtigte(raum):
        raise HTTPException(status_code=404, detail="Anruf nicht gefunden.")
    return {"raum": raum, "teilnehmer": livekit_service.raum_teilnehmer(raum, db)}


# ── Gruppenraeume ───────────────────────────────────────────────────────────


def _gruppenmitglieder_mit_zutritt(db: Session, group_id: int, starter_id: int) -> list[int]:
    """Alle, die diesen Anruf betreten duerfen (der Starter immer eingeschlossen).

    Der Raumname geht nur an diese Menge. Wer nicht beitreten darf, erfaehrt
    auch nicht, dass gerade telefoniert wird.
    """
    ids: list[int] = []
    for (member_id,) in (
        db.query(ChatGroupMember.user_id).filter(ChatGroupMember.group_id == group_id).all()
    ):
        if member_id == starter_id or SocialService.has_group_permission(
            db, group_id, member_id, "join_group_calls"
        ):
            ids.append(member_id)
    return ids


@router.post(
    "/groups/{group_id}/start",
    response_model=GroupCallRoomResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def gruppenanruf_starten(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.assert_group_call_permission(db, group_id, user.id, "start_group_calls")
    _konfiguration_oder_fehler(db)
    raum, max_peers = GroupCallRoomRegistry.create(group_id, max_peers=GRUPPE_MAX_TEILNEHMER)
    ereignis = {
        "type": "group_call_started",
        "group_id": group_id,
        "room_token": raum,
        "max_peers": max_peers,
        "starter": {
            "user_id": user.id,
            "username": user.username,
            "avatar_url": user.avatar_url,
        },
    }
    for member_id in _gruppenmitglieder_mit_zutritt(db, group_id, user.id):
        SyncEventService.publish(ereignis, user_id=member_id)
    return {"room_token": raum, "group_id": group_id, "max_peers": max_peers}


@router.post(
    "/groups/{group_id}/end",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def gruppenanruf_beenden(
    group_id: int,
    req: GroupCallRoomJoinRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Schliesst den Raum fuer alle.

    Braucht das Startrecht, damit ein Gast, der nur geht, den Anruf nicht fuer
    alle beendet; sein eigenes Verlassen bleibt still.
    """
    SocialService.assert_group_call_permission(db, group_id, user.id, "start_group_calls")
    raum = GroupCallRoomRegistry.get(req.room_token)
    if raum is None or raum[0] != group_id:
        raise HTTPException(
            status_code=404, detail="Gruppenanruf nicht gefunden oder abgelaufen."
        )
    GroupCallRoomRegistry.discard(req.room_token)
    ereignis = {
        "type": "group_call_ended",
        "group_id": group_id,
        "room_token": req.room_token,
        "ended_by": user.id,
    }
    for member_id in _gruppenmitglieder_mit_zutritt(db, group_id, user.id):
        SyncEventService.publish(ereignis, user_id=member_id)
    return {"ok": True}


# ── Betreiber-Einstellungen ─────────────────────────────────────────────────


@admin_router.get("/status", response_model=LivekitStatusResponse)
def livekit_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> dict:
    return livekit_service.status(db)


@admin_router.put(
    "/config",
    response_model=LivekitStatusResponse,
    dependencies=[Depends(verify_csrf)],
)
def livekit_konfigurieren(
    req: LivekitConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.settings.write")),
) -> dict:
    try:
        livekit_service.speichere_konfiguration(
            req.modus, req.url, req.api_key, req.api_secret, db=db
        )
    except LivekitZielAbgelehnt as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="panel.livekit.update",
        target_type="setting",
        target_id="livekit",
        # Bewusst ohne Schluessel und ohne Geheimnis: der Audit-Eintrag soll
        # sagen, dass umgestellt wurde, nicht worauf.
        details={"modus": req.modus},
        commit=True,
    )
    return livekit_service.status(db)


@admin_router.post(
    "/test",
    response_model=LivekitTestResponse,
    dependencies=[Depends(verify_csrf)],
)
def livekit_testen(
    req: LivekitTestRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.write")),
) -> dict:
    """Prueft eingegebene Zugangsdaten, ohne sie zu speichern.

    Leere Felder meinen den gespeicherten Stand. Sonst muesste der Betreiber
    Schluessel und Geheimnis zum Testen jedes Mal neu eintippen, obwohl die
    Oberflaeche beide nur maskiert anzeigt.
    """
    try:
        url = livekit_service.normalisiere_externe_url(req.url)
    except LivekitZielAbgelehnt as exc:
        return {"erreichbar": False, "meldung": str(exc), "raeume_aktiv": 0}

    bestand = livekit_service.konfiguration(db)
    schluessel = (req.api_key or "").strip()
    secret = (req.api_secret or "").strip()
    if bestand.modus == "extern":
        if not schluessel:
            schluessel = bestand.api_key
        # Das gespeicherte Geheimnis gehoert zum gespeicherten Schluessel. Zu
        # einem frisch eingetippten Schluessel passt es nicht, dann muss auch
        # das Geheimnis neu kommen.
        if not secret and schluessel == bestand.api_key:
            secret = bestand.api_secret

    erreichbar, meldung, raeume = livekit_service.verbindung_pruefen(url, schluessel, secret)
    return {"erreichbar": erreichbar, "meldung": meldung, "raeume_aktiv": raeume}
