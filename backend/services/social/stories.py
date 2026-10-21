from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    ChatStory,
)
from models.dis_text import vorab_entschluesselt
from services.achievement_service import AchievementService
from services.social.basis import _now
from services.social.grenzen import (
    MAX_AKTIVE_STORIES,
    aktuelle_grenze,
)

logger = logging.getLogger(__name__)


class StoriesMixin:
    @classmethod
    def cleanup_expired_stories(cls, db: Session) -> int:
        """Löscht abgelaufene Stories. Angezeigt werden sie nach 24 Stunden
        nicht mehr, gespeichert blieben sie ohne das für immer."""
        deleted = (
            db.query(ChatStory)
            .filter(ChatStory.expires_at <= _now())
            .delete(synchronize_session=False)
        )
        if deleted > 0:
            db.commit()
            logger.info("Stories: %d abgelaufene bereinigt.", deleted)
        return deleted

    @classmethod
    def create_story(
        cls,
        db: Session,
        user: User,
        content: str,
        media_url: str | None = None,
        background: str = "gradient-1",
    ) -> ChatStory:
        cls.assert_social_enabled(db)
        from datetime import timedelta
        grenze = aktuelle_grenze("MAX_AKTIVE_STORIES", MAX_AKTIVE_STORIES)
        clean_content = content.strip()
        if not clean_content:
            raise HTTPException(status_code=422, detail="Story-Inhalt darf nicht leer sein.")

        aktive = (
            db.query(func.count(ChatStory.id))
            .filter(ChatStory.user_id == user.id, ChatStory.expires_at > _now())
            .scalar()
        )
        if aktive >= grenze:
            raise HTTPException(
                status_code=429,
                detail=f"Mehr als {grenze} Stories gleichzeitig gehen nicht. Ältere laufen nach 24 Stunden ab.",
            )

        if media_url:
            from services.chat_media_validator import validate_story_media_url, ChatMediaSecurityError
            try:
                media_url = validate_story_media_url(media_url)
            except ChatMediaSecurityError as e:
                raise HTTPException(status_code=422, detail=e.detail)

        now = _now()
        story = ChatStory(
            user_id=user.id,
            content=clean_content,
            media_url=media_url,
            background=background or "gradient-1",
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        db.add(story)
        # Stories verfallen nach 24 Stunden; die Zustandsprüfung sähe sie nur so lange.
        AchievementService.melde(db, user.id, "social_status_story")
        db.commit()
        db.refresh(story)
        return story

    @classmethod
    def list_active_stories(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        cls.assert_social_enabled(db)
        now = _now()
        friend_ids = {user_id}
        friends = (
            db.query(UserFriend)
            .filter(
                (UserFriend.user_id == user_id) | (UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )
        for f in friends:
            friend_ids.add(f.friend_id if f.user_id == user_id else f.user_id)

        # Blockierte Benutzer ermitteln
        blocked_rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "blocked",
            )
            .all()
        )
        blocked_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in blocked_rels}

        # Sichtbar: Eigene Stories + Stories von bestätigten Freunden + Stories von Profilen mit "public"
        stories_query = (
            db.query(ChatStory, User.username, User.avatar_url, User.social_privacy)
            .join(User, User.id == ChatStory.user_id)
            .filter(
                ChatStory.expires_at > now,
                or_(
                    ChatStory.user_id.in_(friend_ids),
                    User.social_privacy == "public",
                ),
            )
        )
        if blocked_ids:
            stories_query = stories_query.filter(ChatStory.user_id.notin_(blocked_ids))

        stories_query = stories_query.order_by(ChatStory.created_at.desc())
        with vorab_entschluesselt(db, stories_query, ChatStory.content, ChatStory.media_url):
            stories = stories_query.all()

        return [
            {
                "id": s.id,
                "user_id": s.user_id,
                "username": uname,
                "avatar_url": uavatar,
                "content": s.content,
                "media_url": s.media_url,
                "background": s.background,
                "created_at": s.created_at,
                "expires_at": s.expires_at,
                "is_self": s.user_id == user_id,
            }
            for s, uname, uavatar, _ in stories
        ]

    @classmethod
    def delete_story(cls, db: Session, user: User, story_id: int) -> None:
        cls.assert_social_enabled(db)
        story = db.query(ChatStory).filter(ChatStory.id == story_id).first()
        if not story:
            return
        if story.user_id != user.id:
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Löschen dieser Story.")
        db.delete(story)
        db.commit()
