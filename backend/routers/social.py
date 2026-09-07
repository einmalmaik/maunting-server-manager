from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, get_optional_user, verify_csrf
from models import User
from schemas.social import (
    AchievementResponse,
    AchievementsOverviewResponse,
    ActivityPingRequest,
    E2eeBlindEnvelopeCreate,
    E2eeBlindEnvelopeResponse,
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
)
from services.achievement_service import AchievementService
from services.social_service import SocialService

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
    user: User = Depends(get_current_user),
) -> dict:
    envelope = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=req.blind_mailbox_id,
        ciphertext_envelope=req.ciphertext_envelope,
        sender_user_id=user.id,
        recipient_user_id=req.recipient_user_id,
        group_id=req.group_id,
    )
    return {
        "id": envelope.id,
        "blind_mailbox_id": envelope.blind_mailbox_id,
        "ciphertext_envelope": envelope.ciphertext_envelope,
        "created_at": envelope.created_at,
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

