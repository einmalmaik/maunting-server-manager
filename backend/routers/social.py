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
