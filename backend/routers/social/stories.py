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
    ChatStoryCreate,
    ChatStoryResponse,
)
from services.social_service import SocialService
from routers.social.basis import _check_social_enabled

logger = logging.getLogger(__name__)

router = APIRouter()



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
