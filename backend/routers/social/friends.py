from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    verify_csrf,
)
from models import User
from schemas.social import (
    FriendRequestCreate,
    FriendResponse,
)
from services.social_service import SocialService
from routers.social.basis import _check_social_enabled

logger = logging.getLogger(__name__)

router = APIRouter()



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
