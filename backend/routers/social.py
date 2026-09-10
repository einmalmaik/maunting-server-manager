import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket
from starlette.websockets import WebSocketDisconnect
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import get_current_user, get_optional_user, verify_csrf, get_current_user_for_ws, ws_subprotokoll
from models import User
from schemas.chat_media import (
    ChatMediaUploadRequest,
    ChatMediaUploadResponse,
    ChatMediaSignedUrlResponse,
)
from schemas.social import (
    AchievementResponse,
    AchievementsOverviewResponse,
    ActivityPingRequest,
    E2eeBlindEnvelopeCreate,
    E2eeBlindEnvelopeResponse,
    E2eeTypingSignalCreate,
    E2eePublicKeyResponse,
    E2eePublicKeyUpdate,
    FriendRequestCreate,
    FriendResponse,
    PresenceInfo,
    PresenceUpdateRequest,
    PrivacyUpdateRequest,
    SocialProfileResponse,
    UserStatsResponse,
    ChatGroupCreate,
    ChatGroupResponse,
    ChatGroupMemberResponse,
    ChatGroupMemberUpdate,
    ChatGroupPermissionsUpdate,
    ChatGroupInvitePublicResponse,
    ChatStoryCreate,
    ChatStoryResponse,
    DirectChatResponse,
    CanMessageResponse,
)
from services.achievement_service import AchievementService
from services.chat_media_service import ChatMediaService
from services.chat_media_validator import sanitize_attachment_filename
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social", tags=["social"])


# --- Betreiber-Toggle Guard Dependency ---
def _check_social_enabled(db: Session = Depends(get_db)):
    SocialService.assert_social_enabled(db)


# --- Freundesliste & Anfragen ---

@router.get("/friends", response_model=list[FriendResponse], dependencies=[Depends(_check_social_enabled)])
def list_friends(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return SocialService.get_friends(db, user.id)


@router.get("/friends/requests", dependencies=[Depends(_check_social_enabled)])
def list_friend_requests(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.get_requests(db, user.id)


@router.post("/friends/request", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def send_friend_request(
    req: FriendRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.send_friend_request(db, user.id, req.username)


@router.post("/friends/{request_id}/accept", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
@router.post("/friends/accept/{request_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def accept_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.accept_friend_request(db, user.id, request_id)


@router.post("/friends/{request_id}/decline", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
@router.post("/friends/decline/{request_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def decline_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.decline_or_cancel_request(db, user.id, request_id)
    return {"ok": True, "message": "Freundschaftsanfrage abgelehnt"}


@router.delete("/friends/{target_user_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def remove_friend(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.remove_friend(db, user.id, target_user_id)
    return {"ok": True, "message": "Freund entfernt"}


@router.post("/friends/{target_user_id}/block", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def block_user(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.block_user(db, user.id, target_user_id)
    return {"ok": True, "message": "Benutzer blockiert"}


@router.post("/friends/{target_user_id}/unblock", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def unblock_user(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.unblock_user(db, user.id, target_user_id)
    return {"ok": True, "message": "Blockierung aufgehoben"}


@router.get("/friends/blocked", dependencies=[Depends(_check_social_enabled)])
def get_blocked_users(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return SocialService.get_blocked_users(db, user.id)


# --- Status, Geräte & Rich Presence ---

@router.get("/presence/me", response_model=PresenceInfo, dependencies=[Depends(_check_social_enabled)])
def get_own_presence(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.get_presence(db, user.id)


@router.post("/presence", response_model=PresenceInfo, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def update_presence(
    req: PresenceUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return SocialService.update_presence(db, user.id, req.model_dump(exclude_none=True))


# --- Aktive Interaktionszeit („Spielzeit“) ---

@router.post("/activity/ping", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
@router.post("/activity-time", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def record_activity_ping(
    req: ActivityPingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return AchievementService.record_activity_time(
        db, user.id, category=req.category, seconds=req.seconds
    )


# --- Privatsphäre (3-Stufen-Modell) ---

@router.patch("/privacy", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def update_privacy_setting(
    req: PrivacyUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    updated = SocialService.update_privacy(db, user.id, req.privacy)
    return {"ok": True, "social_privacy": updated}


# --- Errungenschaften & Rarity ---

@router.get("/achievements", response_model=AchievementsOverviewResponse, dependencies=[Depends(_check_social_enabled)])
def get_own_achievements(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return AchievementService.get_achievements_overview(db, user.id)


@router.get("/achievements/list", response_model=list[AchievementResponse], dependencies=[Depends(_check_social_enabled)])
def get_own_achievements_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return AchievementService.get_user_achievements(db, user.id)


@router.get("/stats", response_model=UserStatsResponse, dependencies=[Depends(_check_social_enabled)])
def get_own_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    return AchievementService.get_user_stats(db, user.id)


# --- Profile (Privat / Freunde / Öffentlich) ---

@router.get("/profile/user/{target_user_id}", response_model=SocialProfileResponse, dependencies=[Depends(_check_social_enabled)])
@router.get("/profile/{target_user_id}", response_model=SocialProfileResponse, dependencies=[Depends(_check_social_enabled)])
def get_user_profile(
    target_user_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> dict:
    target = db.query(User).filter_by(id=target_user_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    viewer_id = current_user.id if current_user else None
    return SocialService.get_profile(db, viewer_id, target)


@router.get("/profiles/public", response_model=list[SocialProfileResponse], dependencies=[Depends(_check_social_enabled)])
def list_public_profiles(
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> list[dict]:
    viewer_id = current_user.id if current_user else None
    return SocialService.get_public_profiles(
        db, viewer_user_id=viewer_id, search=search, limit=min(max(limit, 1), 100), offset=max(offset, 0)
    )


@router.get("/profile/public/{username}", response_model=SocialProfileResponse, dependencies=[Depends(_check_social_enabled)])
def get_public_profile(
    username: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> dict:
    target = db.query(User).filter(User.username.ilike(username.strip())).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    viewer_id = current_user.id if current_user else None
    return SocialService.get_profile(db, viewer_id, target)


# --- E2EE Zero-Knowledge Blind Relais Mailbox ---

@router.post("/e2ee/public-key", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def set_e2ee_public_key(
    req: E2eePublicKeyUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.save_e2ee_public_key(db, user.id, req.public_key)
    return {"ok": True, "message": "E2EE-Schlüssel aktualisiert"}


@router.get("/e2ee/public-key/{target_user_id}", response_model=E2eePublicKeyResponse, dependencies=[Depends(_check_social_enabled)])
def get_e2ee_public_key(
    target_user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    target = db.query(User).filter_by(id=target_user_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    pub_key = SocialService.get_e2ee_public_key(db, target_user_id)
    return {
        "user_id": target.id,
        "username": target.username,
        "public_key": pub_key,
    }


@router.post("/e2ee/relay", response_model=E2eeBlindEnvelopeResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def relay_e2ee_message(
    req: E2eeBlindEnvelopeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    envelope = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=req.blind_mailbox_id,
        ciphertext_envelope=req.ciphertext_envelope,
        sender_user_id=current_user.id,
        recipient_id=req.recipient_id,
        client_uuid=req.client_uuid,
        is_control=req.is_control,
        control_type=req.control_type,
    )

    return {
        "id": envelope.id,
        "blind_mailbox_id": envelope.blind_mailbox_id,
        "ciphertext_envelope": envelope.ciphertext_envelope,
        "client_uuid": envelope.client_uuid,
        "created_at": envelope.created_at,
    }


# --- Chat-Medien & E2EE Anhaenge ---

@router.post(
    "/media/upload",
    response_model=ChatMediaUploadResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def upload_chat_media(
    req: ChatMediaUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Laedt einen clientseitig verschluesselten E2EE-Medienblob hoch.

    Der Server nimmt ausschliesslich verschluesselte Blobs entgegen (Zero-Knowledge).
    """
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=current_user,
        blind_mailbox_id=req.blind_mailbox_id,
        ciphertext_blob=req.ciphertext_blob,
        file_name=req.file_name,
        media_type=req.media_type,
        group_id=req.group_id,
        recipient_id=req.recipient_id,
    )
    return {
        "id": media.id,
        "blind_mailbox_id": media.blind_mailbox_id,
        "file_name": media.file_name,
        "media_type": media.media_type,
        "size_bytes": media.size_bytes,
        "sha256": media.sha256,
        "created_at": media.created_at,
    }


@router.get(
    "/media/{media_id}/signed-url",
    response_model=ChatMediaSignedUrlResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def get_chat_media_signed_url(
    media_id: str,
    ttl: int = Query(900, ge=60, le=86400),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Erzeugt eine zeitlich begrenzte signierte URL fuer einen Medienblob.

    Fremde ohne Chat-Mitgliedschaft werden mit 403 Forbidden abgewiesen.
    """
    signed_url, expires_at = ChatMediaService.generate_signed_url(
        db, user=current_user, media_id=media_id, ttl_seconds=ttl
    )
    return {
        "media_id": media_id,
        "signed_url": signed_url,
        "expires_at": expires_at,
    }


@router.get(
    "/media/{media_id}/download",
    dependencies=[Depends(_check_social_enabled)],
)
def download_chat_media_blob(
    media_id: str,
    token: str = Query(...),
    expires: int = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
) -> Response:
    """Liefert den verschluesselten Medienblob anhand einer signierten URL aus.

    Validiert Signatur, Ablaufzeit und Chat-Mitgliedschaft.
    """
    media = ChatMediaService.get_media_by_signed_url(
        db, media_id=media_id, token=token, expires=expires, user_id=user_id
    )
    safe_name = sanitize_attachment_filename(media.file_name)
    return Response(
        content=media.ciphertext_blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.e2ee"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, no-cache, no-store",
        },
    )


# --- Direkte Chats (1:1 Unterhaltungen & Berechtigungsprüfung) ---

@router.get("/chats", response_model=list[DirectChatResponse], dependencies=[Depends(_check_social_enabled)])
@router.get("/direct-chats", response_model=list[DirectChatResponse], dependencies=[Depends(_check_social_enabled)])
def list_my_direct_chats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Liefert alle aktiven 1:1-Chats des authentifizierten Benutzers."""
    return SocialService.list_direct_chats(db, user.id)


@router.get("/chat/can-message/{target_user_id}", response_model=CanMessageResponse, dependencies=[Depends(_check_social_enabled)])
def check_can_message_user(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Prüft, ob der angemeldete Nutzer den Zielnutzer direkt anschreiben darf."""
    can_msg, reason = SocialService.can_message_user(db, user.id, target_user_id)
    mid = SocialService.derive_blind_mailbox_id(user.id, target_user_id) if can_msg else None
    return {
        "can_message": can_msg,
        "reason": reason,
        "blind_mailbox_id": mid,
    }


@router.post("/chat/start/{target_user_id}", response_model=DirectChatResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def start_or_get_direct_chat(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Initiiert oder holt einen direkten Chatkanal mit dem Zielnutzer."""
    chat = SocialService.ensure_direct_chat(db, user.id, target_user_id)
    other = db.query(User).filter_by(id=target_user_id).first()
    if not other:
        raise HTTPException(status_code=404, detail="Zielnutzer nicht gefunden")
    is_friend = SocialService.is_confirmed_friend(db, user.id, target_user_id)
    pres = SocialService.get_presence_for_viewer(db, user.id, other)
    return {
        "id": chat.id,
        "other_user_id": other.id,
        "other_username": other.username,
        "other_avatar_url": other.avatar_url,
        "blind_mailbox_id": chat.blind_mailbox_id,
        "is_friend": is_friend,
        "other_privacy": getattr(other, "social_privacy", "friends"),
        "presence": pres,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


@router.get("/e2ee/mailbox/{blind_mailbox_id}", response_model=list[E2eeBlindEnvelopeResponse], dependencies=[Depends(_check_social_enabled)])
@router.get("/e2ee/envelopes/{blind_mailbox_id}", response_model=list[E2eeBlindEnvelopeResponse], dependencies=[Depends(_check_social_enabled)])
def fetch_blind_mailbox_envelopes(
    blind_mailbox_id: str,
    since_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    envelopes = SocialService.get_blind_envelopes(
        db, blind_mailbox_id=blind_mailbox_id, since_id=since_id, limit=limit
    )
    return [
        {
            "id": env.id,
            "blind_mailbox_id": env.blind_mailbox_id,
            "ciphertext_envelope": env.ciphertext_envelope,
            "created_at": env.created_at,
        }
        for env in envelopes
    ]


@router.post("/e2ee/typing", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def send_e2ee_typing_signal(
    req: E2eeTypingSignalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.broadcast_typing_signal(
        blind_mailbox_id=req.blind_mailbox_id,
        status=req.status,
        sender_id=user.id,
        sender_username=user.username,
        db=db,
        recipient_id=req.recipient_id,
    )
    return {"ok": True}


# --- Chat-Gruppen & Öffentliche Einladungslinks ---

@router.get("/groups", response_model=list[ChatGroupResponse], dependencies=[Depends(_check_social_enabled)])
def list_my_groups(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return SocialService.list_user_groups(db, user.id)


@router.post("/groups", response_model=ChatGroupResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def create_chat_group(
    req: ChatGroupCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    group = SocialService.create_group(
        db,
        user=user,
        name=req.name,
        description=req.description,
        avatar_url=req.avatar_url,
    )
    groups = SocialService.list_user_groups(db, user.id)
    match = next((g for g in groups if g["id"] == group.id), None)
    if match:
        return match
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "avatar_url": group.avatar_url,
        "invite_code": group.invite_code,
        "owner_user_id": group.owner_user_id,
        "member_count": 1,
        "role": "owner",
        "created_at": group.created_at,
        "members": [
            {
                "user_id": user.id,
                "username": user.username,
                "avatar_url": user.avatar_url,
                "role": "owner",
                "joined_at": group.created_at,
            }
        ],
    }


@router.get("/groups/invite/{invite_code}", response_model=ChatGroupInvitePublicResponse)
def get_group_invite_info(
    invite_code: str,
    db: Session = Depends(get_db),
) -> dict:
    """Öffentlicher Endpunkt für Einladungslinks (ohne Login-Pflicht)."""
    group = SocialService.get_group_by_invite_code(db, invite_code)
    member_count = len(group.members) if group.members else 1
    return {
        "group_id": group.id,
        "name": group.name,
        "description": group.description,
        "avatar_url": group.avatar_url,
        "member_count": member_count,
    }


@router.post("/groups/join/{invite_code}", response_model=ChatGroupResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def join_group_by_invite(
    invite_code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    group = SocialService.join_group_by_invite_code(db, user, invite_code)
    groups = SocialService.list_user_groups(db, user.id)
    match = next((g for g in groups if g["id"] == group.id), None)
    if match:
        return match
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "avatar_url": group.avatar_url,
        "invite_code": group.invite_code,
        "owner_user_id": group.owner_user_id,
        "member_count": len(group.members) if group.members else 1,
        "role": "member",
        "created_at": group.created_at,
        "members": [],
    }


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
    SocialService.kick_group_member(db, group_id=group_id, target_user_id=target_user_id, caller=user)
    return {"success": True, "message": "Mitglied aus Gruppe entfernt"}


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
    groups = SocialService.list_user_groups(db, user.id)
    match = next((g for g in groups if g["id"] == group.id), None)
    if match:
        return match
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "avatar_url": group.avatar_url,
        "invite_code": group.invite_code,
        "owner_user_id": group.owner_user_id,
        "default_permissions": group.default_permissions,
        "member_count": 1,
        "role": "owner",
        "created_at": group.created_at,
        "members": [],
    }


# --- Stories (Temporäre Statusmeldungen, 24h) ---

@router.get("/stories", response_model=list[ChatStoryResponse], dependencies=[Depends(_check_social_enabled)])
def list_active_stories(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return SocialService.list_active_stories(db, user.id)


@router.post("/stories", response_model=ChatStoryResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def create_story(
    req: ChatStoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    story = SocialService.create_story(
        db,
        user=user,
        content=req.content,
        media_url=req.media_url,
        background=req.background,
    )
    return {
        "id": story.id,
        "user_id": story.user_id,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "content": story.content,
        "media_url": story.media_url,
        "background": story.background,
        "created_at": story.created_at,
        "expires_at": story.expires_at,
        "is_self": True,
    }


@router.delete("/stories/{story_id}", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def delete_story(
    story_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    SocialService.delete_story(db, user, story_id)
    return {"success": True, "message": "Story gelöscht"}


# --- WebSocket Handler für Echtzeit-Präsenz und Messaging ---

@router.websocket("/ws")
async def social_websocket(
    websocket: WebSocket,
) -> None:
    """Echtzeit-WebSocket für Social Presence, Messaging und Live-Benachrichtigungen.

    Stabilitäts-Invariante:
    - Verhindert DB-Session-Leaks & Pool-Erschöpfung: DB-Sitzungen werden nur bedarfsgerecht
      je Aktion kurzzeitig geöffnet und direkt freigegeben.
    - Robuste Nebenläufigkeit: Sende- und Empfangs-Loops sind gekoppelt; bricht eine Seite
      ab, wird die andere unmittelbar gecancelt und alle Ressourcen freigegeben.
    - Ghost-Mode Schutz: 'invisible' und 'private' Status lecken niemals über Join-Events
      an Nicht-Freunde.
    - Nachrichten-Deduplizierung: Client-UUIDs werden bei Relays verarbeitet und quittiert.
    """
    with SessionLocal() as db:
        try:
            user = get_current_user_for_ws(websocket, db)
            user_id = user.id
            user_username = user.username
        except Exception:
            await websocket.close(code=1008)
            return

    subprotocol = ws_subprotokoll(websocket)
    await websocket.accept(subprotocol=subprotocol)

    conn_id, queue = SyncEventService.subscribe(user_id=user_id)
    ws_lock = asyncio.Lock()

    async def _send_loop():
        try:
            while True:
                event = await queue.get()
                async with ws_lock:
                    await websocket.send_json(event)
        except (asyncio.CancelledError, WebSocketDisconnect):
            raise
        except Exception:
            pass

    async def _recv_loop():
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                # Heartbeat Pong - strikt isoliert, keine Präsenz-Leaks an Dritte
                async with ws_lock:
                    await websocket.send_json({"type": "pong"})
            elif msg_type == "join":
                # WebSocket Join Event:
                # Ghost-Mode & Privacy-Schutz delegiert an SocialService
                try:
                    with SessionLocal() as db:
                        SocialService.broadcast_user_joined(db, user_id, user_username)
                    async with ws_lock:
                        await websocket.send_json({"type": "joined", "status": "ok", "user_id": user_id})
                except Exception as exc:
                    logger.debug("Fehler bei broadcast_user_joined: %s", exc)
            elif msg_type == "presence":
                try:
                    with SessionLocal() as db:
                        SocialService.update_presence(db, user_id, data)
                except Exception as exc:
                    logger.debug("Fehler bei update_presence: %s", exc)
            elif msg_type == "typing":
                blind_mailbox_id = data.get("blind_mailbox_id", "")
                status = data.get("status", "idle")
                recipient_id = data.get("recipient_id")
                try:
                    with SessionLocal() as db:
                        SocialService.broadcast_typing_signal(
                            blind_mailbox_id=blind_mailbox_id,
                            status=status,
                            sender_id=user_id,
                            sender_username=user_username,
                            db=db,
                            recipient_id=recipient_id,
                        )
                except Exception as exc:
                    logger.debug("Fehler bei broadcast_typing_signal: %s", exc)
            elif msg_type == "relay":
                blind_mailbox_id = data.get("blind_mailbox_id", "")
                ciphertext_envelope = data.get("ciphertext_envelope", "")
                recipient_id = data.get("recipient_id")
                is_control = bool(data.get("is_control", False))
                control_type = data.get("control_type")
                client_uuid = data.get("client_uuid")
                try:
                    with SessionLocal() as db:
                        envelope = SocialService.relay_blind_envelope(
                            db,
                            blind_mailbox_id=blind_mailbox_id,
                            ciphertext_envelope=ciphertext_envelope,
                            sender_user_id=user_id,
                            recipient_id=recipient_id,
                            client_uuid=client_uuid,
                            is_control=is_control,
                            control_type=control_type,
                        )
                    # Sofortige Bestätigung an den WebSocket-Sender (Acknowledge zur Queue-Bereinigung)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "relay_ack",
                            "client_uuid": envelope.client_uuid or client_uuid,
                            "id": envelope.id,
                            "blind_mailbox_id": envelope.blind_mailbox_id,
                            "created_at": envelope.created_at.isoformat() if hasattr(envelope.created_at, "isoformat") else str(envelope.created_at),
                        })
                except HTTPException as he:
                    logger.debug("HTTPException bei WebSocket Relay für User %s: %s", user_id, he.detail)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "error",
                            "error": "relay_failed",
                            "status_code": he.status_code,
                            "detail": he.detail,
                            "client_uuid": client_uuid,
                        })
                except Exception as exc:
                    logger.warning("Unerwarteter Fehler bei WebSocket Relay für User %s: %s", user_id, exc)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "error",
                            "error": "relay_failed",
                            "status_code": 500,
                            "detail": "Interner Fehler beim Verarbeiten der Nachricht.",
                            "client_uuid": client_uuid,
                        })

    send_task = asyncio.create_task(_send_loop())
    recv_task = asyncio.create_task(_recv_loop())

    try:
        # Sobald eine Task endet (z.B. Disconnect beim Lesen oder Schreiben),
        # wird die andere Task sauber gecancelt.
        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.debug("Social WebSocket getrennt: %s", e)
    finally:
        send_task.cancel()
        recv_task.cancel()
        await asyncio.gather(send_task, recv_task, return_exceptions=True)
        SyncEventService.unsubscribe(conn_id)

