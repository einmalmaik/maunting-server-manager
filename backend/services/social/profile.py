from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
)
from services.achievement_service import AchievementService

logger = logging.getLogger(__name__)


class ProfilMixin:
    @classmethod
    def update_privacy(cls, db: Session, user_id: int, privacy: str) -> str:
        """Setzt die 3-Stufen Privatsphäre: 'private', 'friends', 'public'."""
        if privacy not in ("private", "friends", "public"):
            raise HTTPException(status_code=400, detail="Ungültige Privatsphäre-Einstellung")
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        user.social_privacy = privacy
        db.commit()
        return user.social_privacy

    @classmethod
    def get_profile(
        cls,
        db: Session,
        viewer_user_id: int | None,
        target_user: User,
        *,
        mit_erfolgen: bool = True,
    ) -> dict[str, Any]:
        """Liefert das Benutzerprofil unter Berücksichtigung des 3-Stufen-Modells.

        ``mit_erfolgen=False`` laesst Zahlen und Erfolge weg (``None``). Die
        Liste der oeffentlichen Profile zeigt nur Name, Bild und Status, und
        die Erfolge kosteten dort je Profil mehrere Abfragen, alle 15 Sekunden.
        """
        is_self = viewer_user_id is not None and viewer_user_id == target_user.id
        privacy = getattr(target_user, "social_privacy", "friends")
        is_friend = cls.is_confirmed_friend(db, viewer_user_id, target_user.id) if viewer_user_id else False

        # Blockierung prüfen
        if viewer_user_id and viewer_user_id != target_user.id:
            blocked = (
                db.query(UserFriend)
                .filter(
                    or_(
                        and_(UserFriend.user_id == viewer_user_id, UserFriend.friend_id == target_user.id),
                        and_(UserFriend.user_id == target_user.id, UserFriend.friend_id == viewer_user_id),
                    ),
                    UserFriend.status == "blocked",
                )
                .first()
            )
            if blocked:
                return {
                    "user_id": target_user.id,
                    "username": target_user.username,
                    "avatar_url": None,
                    "privacy": privacy,
                    "restricted": True,
                    "is_friend": False,
                    "presence": None,
                    "stats": None,
                    "achievements": None,
                }

        allowed = False
        if is_self or privacy == "public":
            allowed = True
        elif privacy == "friends" and viewer_user_id:
            allowed = is_friend

        if not allowed:
            return {
                "user_id": target_user.id,
                "username": target_user.username,
                "avatar_url": target_user.avatar_url,
                "privacy": privacy,
                "restricted": True,
                "is_friend": is_friend,
                "presence": None,
                "stats": None,
                "achievements": None,
            }

        pres_data = cls.get_presence_for_viewer(db, viewer_user_id, target_user)
        stats = None
        achievements = None
        if mit_erfolgen:
            stats = AchievementService.get_user_stats(db, target_user.id, fuer_fremde=not is_self)
            achievements = AchievementService.get_user_achievements(
                db, target_user.id, fuer_fremde=not is_self, pruefen=False
            )

        return {
            "user_id": target_user.id,
            "username": target_user.username,
            "avatar_url": target_user.avatar_url,
            "privacy": privacy,
            "restricted": False,
            "is_friend": is_friend,
            # Nur der Tag: die Uhrzeit der Registrierung sagt niemandem etwas.
            "member_since": target_user.created_at.date() if target_user.created_at else None,
            "presence": pres_data,
            "stats": stats,
            "achievements": achievements,
        }

    @classmethod
    def get_public_profiles(
        cls,
        db: Session,
        viewer_user_id: int | None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Ermittelt alle aktiven Benutzer mit öffentlichem Profil ('public') für die Discovery.

        Zwingend auf DB-/Query-Ebene: Profile mit 'friends' oder 'private' tauchen NIEMALS auf.
        """
        query = db.query(User).filter(
            User.is_active.is_(True),
            User.social_privacy == "public",
        )
        if viewer_user_id:
            query = query.filter(User.id != viewer_user_id)
            blocked_rels = (
                db.query(UserFriend)
                .filter(
                    or_(UserFriend.user_id == viewer_user_id, UserFriend.friend_id == viewer_user_id),
                    UserFriend.status == "blocked",
                )
                .all()
            )
            blocked_ids = {r.friend_id if r.user_id == viewer_user_id else r.user_id for r in blocked_rels}
            if blocked_ids:
                query = query.filter(User.id.notin_(blocked_ids))

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(User.username.ilike(term))

        users = query.order_by(User.username.asc()).offset(offset).limit(limit).all()
        results = []
        for u in users:
            results.append(cls.get_profile(db, viewer_user_id, u, mit_erfolgen=False))
        return results
