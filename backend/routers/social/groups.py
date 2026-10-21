from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    verify_csrf,
)
from models import ChatGroup, ChatGroupConfig, User
from schemas.social import (
    ChatGroupCreate,
    ChatGroupResponse,
    ChatGroupMemberResponse,
    ChatGroupMemberUpdate,
    ChatGroupPermissionsUpdate,
    ChatGroupConfigWrite,
    ChatGroupConfigResponse,
    ChatGroupInviteCardUpdate,
    ChatGroupInvitePublicResponse,
)
from services.social_service import SocialService
from services.call_room_service import GroupCallRoomRegistry
from services import livekit_service
from routers.social.basis import _check_social_enabled

logger = logging.getLogger(__name__)

router = APIRouter()



@router.get("/groups", response_model=list[ChatGroupResponse], dependencies=[Depends(_check_social_enabled)])
def list_my_groups(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return SocialService.list_user_groups(db, user.id)


def _gruppe_antwort(db: Session, group: ChatGroup, user_id: int) -> dict:
    """Die Gruppe so, wie `list_user_groups` sie liefert."""
    groups = SocialService.list_user_groups(db, user_id)
    treffer = next((g for g in groups if g["id"] == group.id), None)
    if treffer:
        return treffer
    role = "owner" if group.owner_user_id == user_id else "member"
    return {
        "id": group.id,
        # Seit Stufe 6 immer leer. Der Client setzt sie aus dem
        # verschluesselten Block; hier steht nichts, was er ueberschreiben
        # muesste.
        "name": None,
        "description": None,
        "avatar_url": None,
        # Wie in `list_user_groups`: der Code geht nur an die, die einladen
        # dürfen. Dieser Zweig greift, solange die Mitgliedschaft noch nicht in
        # der Liste steht — beim Anlegen der Gruppe also für den Gründer.
        "invite_code": (
            group.invite_code if SocialService.darf_einladen(db, group.id, user_id) else None
        ),
        "owner_user_id": group.owner_user_id,
        "default_permissions": group.default_permissions,
        "member_count": len(group.members) if group.members else 1,
        "role": role,
        "created_at": group.created_at,
        "members": [
            {
                "user_id": m.user_id,
                "username": m.user.username if m.user else "",
                "avatar_url": m.user.avatar_url if m.user else None,
                "role": m.role,
                "joined_at": m.joined_at,
            }
            for m in (group.members or [])
        ],
    }


@router.post("/groups", response_model=ChatGroupResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def create_chat_group(
    req: ChatGroupCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    # `req` wird absichtlich nicht ausgewertet: seit Stufe 6 legt der Server
    # zu einer Gruppe nichts mehr ab als ihre Kennung, ihren Gruender und den
    # Einladungscode. Der Rumpf bleibt im Schema, damit ein Altclient nicht an
    # einer fehlenden Route scheitert.
    group = SocialService.create_group(db, user=user)
    return _gruppe_antwort(db, group, user.id)


@router.get("/groups/invite/{invite_code}", response_model=ChatGroupInvitePublicResponse)
def get_group_invite_info(
    invite_code: str,
    db: Session = Depends(get_db),
) -> dict:
    """Öffentlicher Endpunkt für Einladungslinks (ohne Login-Pflicht).

    Liefert zusätzlich, ob gerade telefoniert wird und wie viele im Raum sind.
    Wer den Code hat, soll beitreten können und darf deshalb sehen, ob sich das
    gerade lohnt. Mehr geht bewusst nicht hinaus: keine Namen, keine Kennungen,
    keine Nachrichten.

    **Entweder verschlüsselt oder im Klartext, nie beides.** Hat die Gruppe
    eine `invite_card`, geht nur die hinaus; Name, Beschreibung und Logo
    bleiben `None`. Beides nebeneinander auszuliefern wäre Verschlüsselung als
    Zierde — wer den Klartext danebenlegt, hat nichts verschlossen.

    Seit Stufe 6 gibt es den Klartextzweig nicht mehr — die Spalten, aus denen
    er kam, sind geräumt. Eine Gruppe ohne Karte zeigt deshalb nur noch ihre
    Zahlen; der Client schreibt „Verschlüsselte Einladung" darüber. Das ist
    kein Rückschritt gegenüber vorher, sondern das Ende des Übergangs: wer
    einlädt, hinterlegt beim Teilen eine Karte.
    """
    group = SocialService.get_group_by_invite_code(db, invite_code)
    member_count = len(group.members) if group.members else 1
    raum = _offener_gruppenraum(group.id)
    karte = (group.invite_card or "").strip() or None
    return {
        "group_id": group.id,
        "name": None,
        "description": None,
        "avatar_url": None,
        "invite_card": karte,
        "member_count": member_count,
        "live_call": raum is not None,
        "live_participants": livekit_service.raum_teilnehmer(raum, db) if raum else 0,
    }


@router.put(
    "/groups/{group_id}/invite-card",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def set_group_invite_card(
    group_id: int,
    req: ChatGroupInviteCardUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Hinterlegt die verschlüsselte Einladungskarte.

    **Wer den Link teilen darf, darf die Karte setzen** — dasselbe Recht
    (`invite_members`), weil es dieselbe Handlung ist: die Karte entsteht genau
    dann, wenn jemand einen Link baut. Ein eigenes Recht daneben wäre eine
    zweite Fassung derselben Frage, und die zweite Fassung ist irgendwann die
    nachsichtigere.

    Nicht auf Besitzer und Administratoren eingeschränkt: ein Mitglied mit
    Einladungsrecht, dessen Karte abgewiesen wird, teilt trotzdem einen Link —
    nur einen ohne Vorschau. Die Schranke schützte dann nichts und kostete eine
    kaputte Karte.

    Der Server prüft die Form, nie den Inhalt. Er kann ihn nicht lesen; das ist
    der Zweck.
    """
    if not SocialService.get_group_member(db, group_id, user.id):
        raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
    if not SocialService.darf_einladen(db, group_id, user.id):
        raise HTTPException(
            status_code=403, detail="Kein Recht, Einladungen für diese Gruppe zu erstellen."
        )
    group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")

    group.invite_card = req.invite_card
    db.commit()
    return {"ok": True}


def _offener_gruppenraum(group_id: int) -> str | None:
    """Der Raumname eines laufenden Gruppenanrufs, falls es einen gibt."""
    for token in GroupCallRoomRegistry.offene_raeume():
        eintrag = GroupCallRoomRegistry.get(token)
        if eintrag is not None and eintrag[0] == group_id:
            return token
    return None


def _gruppenadmin_oder_fehler(db: Session, group_id: int, user_id: int) -> ChatGroup:
    mitglied = SocialService.get_group_member(db, group_id, user_id)
    if not mitglied or mitglied.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403, detail="Nur Besitzer und Admins ändern das Gruppenlogo."
        )
    group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
    return group


@router.post("/groups/join/{invite_code}", response_model=ChatGroupResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def join_group_by_invite(
    invite_code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    group = SocialService.join_group_by_invite_code(db, user, invite_code)
    return _gruppe_antwort(db, group, user.id)


@router.post("/groups/{group_id}/leave", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def leave_chat_group(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.leave_group(db, user, group_id)
    return {"success": True, "message": "Gruppe verlassen"}


@router.delete("/groups/{group_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def delete_chat_group(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.delete_group(db, user, group_id)
    return {"success": True, "message": "Gruppe gelöscht"}


@router.get("/groups/{group_id}/members", response_model=list[ChatGroupMemberResponse], dependencies=[Depends(_check_social_enabled)])
def get_group_members(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    groups = SocialService.list_user_groups(db, user.id)
    match = next((g for g in groups if g["id"] == group_id), None)
    if not match:
        raise HTTPException(status_code=403, detail="Du bist kein Mitglied dieser Gruppe.")
    return match.get("members", [])


@router.patch("/groups/{group_id}/members/{target_user_id}", response_model=ChatGroupMemberResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def update_group_member_role(
    group_id: int,
    target_user_id: int,
    req: ChatGroupMemberUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.update_member_role_permissions(
        db,
        group_id=group_id,
        target_user_id=target_user_id,
        role=req.role,
        permissions=req.permissions,
        caller=user,
    )


@router.delete("/groups/{group_id}/members/{target_user_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def kick_group_member_endpoint(
    group_id: int,
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Wirft ein Mitglied hinaus; der Einladungscode ist danach ein neuer.

    Der neue Code geht in der Antwort zurück — sonst teilte der Einladende bis
    zum nächsten Laden einen toten Link. Aber nur an jemanden, der ihn ohnehin
    sehen dürfte (`darf_einladen`): wer nur hinauswerfen darf, bekommt ihn hier
    so wenig wie in der Gruppenliste.
    """
    group = SocialService.kick_group_member(
        db, group_id=group_id, target_user_id=target_user_id, caller=user
    )
    return {
        "success": True,
        "message": "Mitglied aus Gruppe entfernt",
        "invite_code": (
            group.invite_code if SocialService.darf_einladen(db, group_id, user.id) else None
        ),
    }


@router.patch("/groups/{group_id}/permissions", response_model=ChatGroupResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def update_group_permissions_endpoint(
    group_id: int,
    req: ChatGroupPermissionsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    group = SocialService.update_group_default_permissions(
        db,
        group_id=group_id,
        default_permissions=req.default_permissions,
        caller=user,
    )
    return _gruppe_antwort(db, group, user.id)


@router.get(
    "/groups/{group_id}/config",
    response_model=ChatGroupConfigResponse | None,
    dependencies=[Depends(_check_social_enabled)],
)
def get_group_config_endpoint(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatGroupConfig | None:
    # `null` heißt „diese Gruppe hat noch keinen Zustand" — der Client schreibt
    # dann mit `erwartete_revision: 0`. Ein 404 wäre hier missverständlich: die
    # Gruppe gibt es, nur den Block noch nicht.
    return SocialService.get_group_config(db, group_id=group_id, caller=user)


@router.put(
    "/groups/{group_id}/config",
    response_model=ChatGroupConfigResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def put_group_config_endpoint(
    group_id: int,
    req: ChatGroupConfigWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatGroupConfig:
    return SocialService.write_group_config(
        db,
        group_id=group_id,
        blob=req.blob,
        erwartete_revision=req.erwartete_revision,
        caller=user,
    )
