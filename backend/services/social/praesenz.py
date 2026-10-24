from __future__ import annotations

from datetime import timezone
import logging
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    UserPresence,
)
from services.sync_event_service import SyncEventService
from services.social.basis import _now

logger = logging.getLogger(__name__)


class PraesenzMixin:
    @classmethod
    def get_presence(cls, db: Session, user_id: int) -> dict[str, Any]:
        pres = db.query(UserPresence).filter_by(user_id=user_id).first()
        if not pres:
            return {
                "status": "offline",
                "device_type": "web",
                "custom_status": None,
                "activity_label": None,
                "activity_detail": None,
                "updated_at": None,
            }

        raw_status = pres.status
        if raw_status not in ("offline", "invisible"):
            if not pres.updated_at:
                raw_status = "offline"
            else:
                updated_dt = pres.updated_at if pres.updated_at.tzinfo else pres.updated_at.replace(tzinfo=timezone.utc)
                if (_now() - updated_dt).total_seconds() > 120:
                    raw_status = "offline"

        return {
            "status": raw_status,
            "device_type": pres.device_type,
            "custom_status": pres.custom_status,
            "activity_label": pres.activity_label,
            "activity_detail": pres.activity_detail,
            "updated_at": pres.updated_at,
        }

    @classmethod
    def get_presence_for_viewer(
        cls, db: Session, viewer_user_id: int | None, target_user: User | int
    ) -> dict[str, Any] | None:
        """Ermittelt den Präsenzstatus unter strikter Einhaltung der 3-Stufen- und Maskierungsregeln:
        - Unsichtbar: Unabhängig von Öffentlich/Freunde/Privat komplett als 'offline' maskiert (keine Aktivitäten).
        - Öffentlich: Jeder sieht Online-Status und Aktivitäten.
        - Freunde: Nur bestätigte Freunde sehen Details wie Stories und Aktivitäten. Für Fremde komplett verborgen.
        - Privat: Nutzer wird als 'Online' angezeigt (falls nicht unsichtbar), aber Aktivitäten
          und Profilinhalte sind für Nicht-Freunde strikt verborgen (nur reiner Online-Indikator).
        """
        if isinstance(target_user, int):
            user = db.query(User).filter_by(id=target_user).first()
            if not user:
                return {
                    "status": "offline",
                    "device_type": "web",
                    "custom_status": None,
                    "activity_label": None,
                    "activity_detail": None,
                    "updated_at": None,
                }
        else:
            user = target_user

        pres = cls.get_presence(db, user.id)
        is_self = viewer_user_id is not None and viewer_user_id == user.id
        raw_status = pres["status"]

        # 1. Unsichtbar-Modus: Unabhängig von Öffentlich/Freunde/Privat komplett als Offline maskieren
        if raw_status == "invisible":
            if is_self:
                return pres
            return {
                "status": "offline",
                "device_type": "web",
                "custom_status": None,
                "activity_label": None,
                "activity_detail": None,
                "updated_at": None,
            }

        if is_self:
            return pres

        # Blockierte Benutzer sehen niemals Präsenz- oder Statusdaten
        if viewer_user_id and viewer_user_id != user.id:
            blocked = (
                db.query(UserFriend)
                .filter(
                    or_(
                        and_(UserFriend.user_id == viewer_user_id, UserFriend.friend_id == user.id),
                        and_(UserFriend.user_id == user.id, UserFriend.friend_id == viewer_user_id),
                    ),
                    UserFriend.status == "blocked",
                )
                .first()
            )
            if blocked:
                return None

        privacy = getattr(user, "social_privacy", "friends")
        is_friend = cls.is_confirmed_friend(db, viewer_user_id, user.id) if viewer_user_id else False

        # 2. Öffentlich: Jeder sieht den Online-Status und aktuelle Aktivitäten
        if privacy == "public":
            return pres

        # 3. Freunde: Nur bestätigte Freunde sehen Details wie Stories und Aktivitäten.
        # Für Fremde wird dies komplett ausgeblendet.
        if privacy == "friends":
            if is_friend:
                return pres
            # Für Fremde komplett ausgeblendet
            return None

        # 4. Privat: Der Nutzer wird zwar als "Online" angezeigt (sofern nicht unsichtbar),
        # aber Details wie Stories, Profilinhalte und aktuelle Aktivitäten ("was der User macht")
        # sind für alle Nicht-Freunde strikt verborgen. Man sieht lediglich den reinen Online-Indikator, mehr nicht.
        if privacy == "private":
            if is_friend:
                return pres
            # Reiner Online-Indikator für Nicht-Freunde (keine Aktivitäten, kein custom_status)
            return {
                "status": raw_status,
                "device_type": "web",
                "custom_status": None,
                "activity_label": None,
                "activity_detail": None,
                "updated_at": None,
            }

        return None

    @classmethod
    def update_presence(cls, db: Session, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """Aktualisiert Online-Status, Gerätetyp und Rich Presence des Nutzers."""
        status = data.get("status", "online")
        device_type = data.get("device_type")
        custom_status = data.get("custom_status")
        activity_label = data.get("activity_label")
        activity_detail = data.get("activity_detail")

        pres = db.query(UserPresence).filter_by(user_id=user_id).first()
        if not pres:
            pres = UserPresence(
                user_id=user_id,
                status=status,
                device_type=device_type or "web",
                custom_status=custom_status,
                activity_label=activity_label,
                activity_detail=activity_detail,
                updated_at=_now(),
            )
            db.add(pres)
        else:
            pres.status = status
            if device_type:
                pres.device_type = device_type
            pres.custom_status = custom_status
            pres.activity_label = activity_label
            pres.activity_detail = activity_detail
            pres.updated_at = _now()

        db.commit()

        user_obj = db.query(User).filter_by(id=user_id).first()
        privacy = getattr(user_obj, "social_privacy", "friends") if user_obj else "friends"

        subscriber_user_ids = {sub.user_id for sub in SyncEventService._subscribers.values()}
        friend_rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )

        # Ghost-Mode & Privacy: Wenn status == 'invisible' oder privacy == 'private'/'friends',
        # darf der Status NIEMALS an Nicht-Freunde (Fremde, passive Subscriber, fremde Chatpartner)
        # durch Echtzeit-Events oder Joins geleakt werden.
        if pres.status == "invisible" or privacy in ("private", "friends"):
            # Nur bestätigte Freunde erhalten gefilterte Updates (bei invisible strikt als offline maskiert)
            relevant_viewer_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in friend_rels}
        else:
            # Öffentlich & nicht unsichtbar: Freunde und autorisierte Abonnenten.
            #
            # Die Chatpartner standen hier bis Stufe 6b — sie kamen aus
            # `direct_chats.user_a_id`/`user_b_id`, und mit den Spalten geht
            # dieser Zweig. Wer die Anwesenheit eines Gesprächspartners sehen
            # will, wird Freund oder abonniert ein öffentliches Profil.
            #
            # Der Tausch ist bewusst: ein Anwesenheitsereignis, das der Server
            # an „alle, mit denen dieses Konto schreibt" verteilt, setzt voraus,
            # dass er diese Liste führt. Genau die soll es nicht mehr geben.
            relevant_viewer_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in friend_rels}
            if user_obj and getattr(user_obj, "social_privacy", "friends") == "public":
                relevant_viewer_ids |= subscriber_user_ids

        # Für jeden relevanten Betrachter serverseitig gefiltertes Event publizieren
        for viewer_id in relevant_viewer_ids:
            if viewer_id == user_id:
                continue
            pres_for_viewer = cls.get_presence_for_viewer(db, viewer_id, user_obj or user_id)
            if pres_for_viewer is not None:
                pres_payload = dict(pres_for_viewer)
                if pres_payload.get("updated_at") and hasattr(pres_payload["updated_at"], "isoformat"):
                    pres_payload["updated_at"] = pres_payload["updated_at"].isoformat()
                SyncEventService.publish(
                    {
                        "type": "friend_presence_updated",
                        "friend_id": user_id,
                        "user_id": user_id,
                        "presence": pres_payload,
                    },
                    user_id=viewer_id,
                )

        return {
            "status": pres.status,
            "device_type": pres.device_type,
            "custom_status": pres.custom_status,
            "activity_label": pres.activity_label,
            "activity_detail": pres.activity_detail,
            "updated_at": pres.updated_at,
        }

    @classmethod
    def broadcast_user_joined(cls, db: Session, user_id: int, user_username: str) -> None:
        """Broadcastet ein user_joined Event unter strikter Einhaltung des Ghost-Mode und der Privatsphäre."""
        pres = cls.get_presence(db, user_id)
        is_invisible = pres.get("status") == "invisible"

        # Ghost-Mode: Wenn unsichtbar, niemals an irgendwen broadcasten!
        if is_invisible:
            return

        rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )
        target_user_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in rels}

        # Die Chatpartner eines öffentlichen Profils standen hier bis Stufe 6b.
        # Sie kamen aus `direct_chats.user_a_id`/`user_b_id`; mit den Spalten
        # geht dieser Zweig. Benachrichtigt werden damit Freunde — und für ein
        # öffentliches Profil die Abonnenten, die derselbe Weg schon kannte.

        # Blockierte Benutzer niemals benachrichtigen (Datenschutz-Invariante)
        blocked_rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "blocked",
            )
            .all()
        )
        blocked_user_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in blocked_rels}
        target_user_ids -= blocked_user_ids

        for target_id in target_user_ids:
            SyncEventService.publish(
                {
                    "type": "user_joined",
                    "user_id": user_id,
                    "username": user_username,
                },
                user_id=target_id,
            )
