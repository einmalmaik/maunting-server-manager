"""Anrufe: Einladungen, LiveKit-Zugangstoken und die Betreiber-Umschaltung.

Eigene Datei, weil `social.py` mit Freundschaften, Umschlaegen, Gruppen und
Stories bereits gut gefuellt ist und Anrufe ein abgeschlossenes Thema sind.

Was hier **nicht** steht, ist Absicht: keine Anrufhistorie, keine Dauer, kein
Gespraechspartner in der Datenbank. Wer mit wem spricht, weiss der Prozess, so
lange der Raum lebt, und danach niemand mehr.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import ChatGroup, ChatGroupMember, User
from schemas.calls import (
    ActiveCallInfo,
    ActiveCallResponse,
    CallHeartbeatRequest,
    CallKeyRelayRequest,
    CallLeaveRequest,
    CallMuteRequest,
    CallParticipantCountResponse,
    CallTokenRequest,
    CallTokenResponse,
    DirectCallResponse,
    LivekitConfigUpdate,
    LivekitStatusResponse,
    LivekitTestRequest,
    LivekitTestResponse,
    PendingCallInfo,
    PendingCallResponse,
    PendingGroupCallInfo,
)
from schemas.social import GroupCallRoomJoinRequest, GroupCallRoomResponse
from services import audit_service, livekit_service
from services.call_room_service import (
    EINLADUNG_TTL_SEKUNDEN,
    GRUPPE_MAX_TEILNEHMER,
    CallRoomService,
    GroupCallRoomRegistry,
    UserActiveCallRegistry,
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


def _melde_anrufzustand(user_ids: list[int] | set[int] | int, active_call: dict | None = None) -> None:
    """Verteilt den neuen Anrufstatus an alle angegebenen Benutzerkonten."""
    targets = [user_ids] if isinstance(user_ids, int) else list(user_ids)
    for uid in targets:
        SyncEventService.publish(
            {
                "type": "user_call_state_changed",
                "user_id": uid,
                "active_call": active_call,
            },
            user_id=uid,
        )


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

    raum = CallRoomService.issue(user.id, target_user_id, mode=mode)
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
    UserActiveCallRegistry.remove_room(signaling_token)
    # `rejected_by`, nicht `recipient_id`: dieses Ereignis geht an den Anrufer,
    # und der Abgelehnte ist hier der Absender der Ablehnung. Unter dem Namen
    # der Nachbarereignisse (`direct_call_invitation`, `direct_call_cancelled`)
    # stand hier das Gegenteil von dem, was sie damit meinen — der Client las es
    # als „an dich gerichtet", verglich es mit dem eigenen Konto und legte
    # deshalb nie auf.
    SyncEventService.publish(
        {
            "type": "direct_call_rejected",
            "signaling_token": signaling_token,
            "rejected_by": user.id,
        },
        user_id=anrufer_id,
    )
    _melde_anrufzustand([user.id, anrufer_id], None)
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
    if CallRoomService.is_accepted(signaling_token):
        return {"ok": True}
    empfaenger_id = CallRoomService.cancel(signaling_token, user.id)
    if empfaenger_id is None:
        raise HTTPException(
            status_code=404, detail="Anruf nicht gefunden oder bereits abgelaufen."
        )
    UserActiveCallRegistry.remove_room(signaling_token)
    SyncEventService.publish(
        {
            "type": "direct_call_cancelled",
            "signaling_token": signaling_token,
            "caller_id": user.id,
            "recipient_id": empfaenger_id,
        },
        user_id=empfaenger_id,
    )
    _melde_anrufzustand([user.id, empfaenger_id], None)
    return {"ok": True}


# ── Aktiver Anruf & Cross-Device Handoff ────────────────────────────────────


@router.get(
    "/active",
    response_model=ActiveCallResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def aktiver_anruf(
    user: User = Depends(get_current_user),
) -> dict:
    """Liefert den derzeit aktiven Anruf des Kontos über alle Geräte/Plattformen zurück."""
    call = UserActiveCallRegistry.get(user.id)
    return {"has_active_call": call is not None, "call": call}


@router.get(
    "/pending",
    response_model=PendingCallResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def ausstehende_anrufe(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Prüft auf eingehende Direkt-Anrufe und aktive Gruppenanrufe des Nutzers."""
    pending = CallRoomService.get_pending_invitation(user.id)
    pending_call = None
    if pending is not None:
        caller = db.query(User).filter_by(id=pending["caller_id"], is_active=True).first()
        if caller:
            pending_call = {
                "signaling_token": pending["signaling_token"],
                "caller_id": caller.id,
                "caller_username": caller.username,
                "caller_avatar_url": caller.avatar_url,
                "mode": pending.get("mode", "audio"),
                "expires_in": pending["expires_in"],
            }

    group_calls: list[dict] = []
    user_groups = (
        db.query(ChatGroupMember.group_id)
        .filter(ChatGroupMember.user_id == user.id)
        .all()
    )
    group_ids = [row[0] for row in user_groups]
    if group_ids:
        groups = db.query(ChatGroup).filter(ChatGroup.id.in_(group_ids)).all()
        for g in groups:
            room_token = GroupCallRoomRegistry.find_for_group(g.id)
            if room_token:
                participants_count = livekit_service.raum_teilnehmer(room_token, db)
                group_calls.append({
                    "group_id": g.id,
                    "group_name": g.name,
                    "avatar_url": g.avatar_url,
                    "room_token": room_token,
                    "participant_count": participants_count,
                })

    return {
        "has_pending_call": pending_call is not None,
        "call": pending_call,
        "group_calls": group_calls,
    }


@router.post(
    "/leave",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def anruf_verlassen(
    req: CallLeaveRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Meldet, dass das aktuelle Gerät den Anruf verlassen hat."""
    if req.raum:
        ersteller_id = CallRoomService.ersteller(req.raum)
        if ersteller_id == user.id and not CallRoomService.is_accepted(req.raum):
            empfaenger_id = CallRoomService.cancel(req.raum, user.id)
            if empfaenger_id is not None:
                UserActiveCallRegistry.remove_room(req.raum)
                SyncEventService.publish(
                    {
                        "type": "direct_call_cancelled",
                        "signaling_token": req.raum,
                        "caller_id": user.id,
                        "recipient_id": empfaenger_id,
                    },
                    user_id=empfaenger_id,
                )
                _melde_anrufzustand(empfaenger_id, None)
    removed = UserActiveCallRegistry.leave(user.id, raum=req.raum, device_id=req.device_id)
    if removed:
        _melde_anrufzustand(user.id, None)
    return {"ok": True}


@router.post(
    "/active/terminate",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def aktiven_anruf_beenden(
    user: User = Depends(get_current_user),
) -> dict:
    """Beendet den aktiven Anruf aus der Ferne (z. B. über den Auflegen-Knopf im Webinterface)."""
    removed = UserActiveCallRegistry.leave(user.id)
    if removed:
        raum = removed.get("raum")
        if raum and removed.get("art") == "direkt":
            partner_data = removed.get("partner")
            partner_id = (
                partner_data.get("user_id") if isinstance(partner_data, dict) else None
            )
            empfaenger_id = (
                CallRoomService.cancel(raum, user.id)
                or CallRoomService.reject(raum, user.id)
                or partner_id
            )
            if empfaenger_id is not None:
                SyncEventService.publish(
                    {
                        "type": "direct_call_cancelled",
                        "signaling_token": raum,
                        "caller_id": user.id,
                        "recipient_id": empfaenger_id,
                    },
                    user_id=empfaenger_id,
                )
                SyncEventService.publish(
                    {
                        "type": "call_ended_remotely",
                        "raum": raum,
                        "message": "Der Anruf wurde beendet.",
                    },
                    user_id=empfaenger_id,
                )
                _melde_anrufzustand(empfaenger_id, None)
        SyncEventService.publish(
            {
                "type": "call_ended_remotely",
                "raum": raum,
                "message": "Der Anruf wurde beendet.",
            },
            user_id=user.id,
        )
        _melde_anrufzustand(user.id, None)
    return {"ok": True}


@router.post(
    "/heartbeat",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def anruf_heartbeat(
    req: CallHeartbeatRequest,
    user: User = Depends(get_current_user),
) -> dict:
    ok = UserActiveCallRegistry.heartbeat(user.id, device_id=req.device_id)
    if ok:
        curr = UserActiveCallRegistry.get(user.id)
        if curr and curr.get("room_token"):
            CallRoomService.touch(curr["room_token"])
    return {"ok": ok}


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

    partner_id: int | None = None
    partner_username: str | None = None
    partner_avatar_url: str | None = None
    group_name: str | None = None

    if req.art == "direkt":
        if CallRoomService.is_consumed(req.raum):
            raise HTTPException(status_code=410, detail="Der Anruf ist bereits beendet.")
        if not CallRoomService.authorize(req.raum, user.id):
            raise HTTPException(
                status_code=403, detail="Für diesen Anruf liegt keine Einladung vor."
            )
        berechtigte = CallRoomService.berechtigte(req.raum)
        gegenstelle_id = next((uid for uid in berechtigte if uid != user.id), None)
        if gegenstelle_id is not None:
            partner_user = db.query(User).filter_by(id=gegenstelle_id).first()
            if partner_user:
                partner_id = partner_user.id
                partner_username = partner_user.username
                partner_avatar_url = partner_user.avatar_url
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
        gruppe_obj = db.query(ChatGroup).filter_by(id=req.group_id).first()
        if gruppe_obj:
            group_name = gruppe_obj.name

    identity = f"u{user.id}"
    user_metadata = json.dumps({
        "user_id": user.id,
        "username": user.username,
        "avatar_url": user.avatar_url,
    })
    token = livekit_service.zugangstoken(
        req.raum,
        identity,
        user.username,
        api_key=konf.api_key,
        api_secret=konf.api_secret,
        metadata=user_metadata,
    )

    curr_call, prev_call, is_handoff = UserActiveCallRegistry.register(
        user.id,
        req.raum,
        req.art,
        group_id=req.group_id,
        group_name=group_name,
        mode=req.mode or "audio",
        device_id=req.device_id,
        device_type=req.device_type,
        partner_id=partner_id,
        partner_username=partner_username,
        partner_avatar_url=partner_avatar_url,
        user_username=user.username,
        user_avatar_url=user.avatar_url,
    )

    if is_handoff and prev_call:
        # Cross-Device Handoff: Das alte Gerät wird sauber abgelöst
        SyncEventService.publish(
            {
                "type": "call_transferred",
                "raum": req.raum,
                "old_device_id": prev_call.get("device_id"),
                "new_device_id": req.device_id,
                "new_device_type": req.device_type or "web",
                "message": "Der Anruf wurde auf ein anderes Gerät übertragen.",
            },
            user_id=user.id,
        )
        if req.art == "direkt" and partner_id:
            SyncEventService.publish(
                {
                    "type": "call_partner_transferred",
                    "raum": req.raum,
                    "user_id": user.id,
                    "message": f"{user.username} wechselt das Gerät...",
                },
                user_id=partner_id,
            )
    elif prev_call and prev_call.get("raum") != req.raum:
        # Ein anderer Anruf wurde betreten -> der alte Anruf wird verlassen (wie bei Discord)
        old_raum = prev_call.get("raum")
        old_art = prev_call.get("art")
        SyncEventService.publish(
            {
                "type": "call_superseded",
                "old_raum": old_raum,
                "new_raum": req.raum,
                "message": "Du bist auf einem anderen Gerät einem anderen Anruf beigetreten.",
            },
            user_id=user.id,
        )
        if old_art == "direkt" and old_raum:
            old_partner = prev_call.get("partner")
            old_partner_id = (
                old_partner.get("user_id") if isinstance(old_partner, dict) else None
            )
            empfaenger_id = (
                CallRoomService.cancel(old_raum, user.id)
                or CallRoomService.reject(old_raum, user.id)
                or old_partner_id
            )
            if empfaenger_id is not None:
                SyncEventService.publish(
                    {
                        "type": "direct_call_cancelled",
                        "signaling_token": old_raum,
                        "caller_id": user.id,
                        "recipient_id": empfaenger_id,
                    },
                    user_id=empfaenger_id,
                )
                SyncEventService.publish(
                    {
                        "type": "call_ended_remotely",
                        "raum": old_raum,
                        "message": "Der Gesprächspartner ist einem anderen Anruf beigetreten.",
                    },
                    user_id=empfaenger_id,
                )
                _melde_anrufzustand(empfaenger_id, None)

    # Signalisiere den neuen Status an alle offenen Sitzungen des Benutzers
    _melde_anrufzustand(user.id, curr_call)

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


# ── Moderation im Raum ──────────────────────────────────────────────────────


def _moderationsziel(
    db: Session, raum: str, akteur: User, ziel_id: int, recht: str
) -> tuple[int, ChatGroupMember]:
    """Prueft, ob `akteur` gegen `ziel_id` in diesem Raum moderieren darf.

    Vier Schranken, jede aus einem eigenen Grund:

    * Nur Gruppenraeume. Im Zweiergespraech gibt es keine Moderation, sonst
      koennte jeder sein Gegenueber im eigenen Anruf stummschalten.
    * Das Recht selbst, beim Ausfuehren geprueft — nicht beim Anzeigen des
      Knopfes. Die Flags in der Gruppenliste sagen nur, was sichtbar ist.
    * Nicht gegen sich selbst: dafuer gibt es den Mikrofonknopf.
    * Rang: dieselbe Regel wie beim Entfernen aus der Gruppe. Wer den
      Eigentuemer nicht rauswerfen darf, darf ihm auch nicht das Wort nehmen.
    """
    gruppe = GroupCallRoomRegistry.get(raum)
    if gruppe is None:
        raise HTTPException(
            status_code=404, detail="Gruppenanruf nicht gefunden oder abgelaufen."
        )
    group_id = gruppe[0]
    akteur_mem = SocialService.assert_group_call_permission(db, group_id, akteur.id, recht)
    if ziel_id == akteur.id:
        raise HTTPException(status_code=400, detail="Das geht nicht gegen dich selbst.")

    ziel_mem = SocialService.get_group_member(db, group_id, ziel_id)
    if not ziel_mem:
        raise HTTPException(status_code=404, detail="Gruppenmitglied nicht gefunden.")
    if ziel_mem.role == "owner":
        raise HTTPException(
            status_code=403, detail="Der Eigentümer der Gruppe lässt sich nicht moderieren."
        )
    if ziel_mem.role == "admin" and akteur_mem.role != "owner":
        raise HTTPException(
            status_code=403, detail="Nur der Eigentümer kann Administratoren moderieren."
        )
    return group_id, ziel_mem


@router.post(
    "/{raum}/teilnehmer/{target_user_id}/stumm",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def teilnehmer_stummschalten(
    raum: str,
    target_user_id: int,
    req: CallMuteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Nimmt einem Teilnehmer im Gruppenanruf das Mikrofon oder gibt es zurueck.

    Der Entzug wirkt am Medienserver: der Betroffene kann sich nicht selbst
    wieder freischalten. Kamera und Bildschirmfreigabe bleiben unberuehrt.
    """
    group_id, _ziel = _moderationsziel(db, raum, user, target_user_id, "mute_in_calls")
    try:
        livekit_service.setze_mikrofonrecht(raum, f"u{target_user_id}", not req.stumm, db)
    except livekit_service.LivekitNichtErreichbar as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="social.call.mute" if req.stumm else "social.call.unmute",
        target_type="chat_group",
        target_id=str(group_id),
        # Ohne Raumnamen und ohne Gespraechsinhalt: der Eintrag haelt fest, wer
        # wem das Wort genommen hat, nicht worueber gesprochen wurde.
        details={"target_user_id": target_user_id},
        commit=True,
    )
    return {"ok": True, "stumm": req.stumm}


@router.post(
    "/{raum}/teilnehmer/{target_user_id}/entfernen",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def teilnehmer_entfernen(
    raum: str,
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Wirft jemanden aus dem Gruppenanruf.

    Die Gruppenmitgliedschaft bleibt bestehen: wer das Beitrittsrecht hat, kann
    sofort wiederkommen. Ein dauerhafter Ausschluss ist eine Sache der
    Gruppenrechte, nicht eines Klicks im Anruffenster.
    """
    group_id, _ziel = _moderationsziel(db, raum, user, target_user_id, "kick_from_calls")
    try:
        livekit_service.entferne_teilnehmer(raum, f"u{target_user_id}", db)
    except livekit_service.LivekitNichtErreichbar as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="social.call.kick",
        target_type="chat_group",
        target_id=str(group_id),
        details={"target_user_id": target_user_id},
        commit=True,
    )
    return {"ok": True}


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
    betroffene = UserActiveCallRegistry.remove_room(req.room_token)
    ereignis = {
        "type": "group_call_ended",
        "group_id": group_id,
        "room_token": req.room_token,
        "ended_by": user.id,
    }
    alle_empfaenger = set(betroffene + _gruppenmitglieder_mit_zutritt(db, group_id, user.id))
    for member_id in alle_empfaenger:
        SyncEventService.publish(ereignis, user_id=member_id)
    _melde_anrufzustand(alle_empfaenger, None)
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
