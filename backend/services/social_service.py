from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from fastapi import HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    UserPresence,
    E2eeBlindEnvelope,
)
from services.panel_settings_service import PanelSettingsService
from services.sync_event_service import SyncEventService
from services.achievement_service import AchievementService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SocialService:
    """Verwaltet Freundschaften, Privatsphäre, Rich Presence und die blinde E2EE-Relais-Mailbox."""

    @classmethod
    def is_social_enabled(cls, db: Session | None = None) -> bool:
        """Globaler Betreiber-Master-Toggle aus den Panel-Einstellungen."""
        return PanelSettingsService.get("social_enabled", "true", db).lower() != "false"

    @classmethod
    def assert_social_enabled(cls, db: Session | None = None) -> None:
        if not cls.is_social_enabled(db):
            raise HTTPException(
                status_code=403,
                detail="Das Social- und Errungenschaftssystem ist vom Betreiber deaktiviert.",
            )

    @classmethod
    def is_confirmed_friend(cls, db: Session, user_a_id: int, user_b_id: int) -> bool:
        """Prüft, ob eine bestätigte Freundschaft zwischen zwei Konten vorliegt."""
        if user_a_id == user_b_id:
            return True
        rel = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_a_id, UserFriend.friend_id == user_b_id),
                    and_(UserFriend.user_id == user_b_id, UserFriend.friend_id == user_a_id),
                ),
                UserFriend.status == "accepted",
            )
            .first()
        )
        return rel is not None

    @classmethod
    def get_friends(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        """Liefert alle bestätigten Freunde des Benutzers samt aktueller Präsenz."""
        rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )

        friend_user_ids = [
            r.friend_id if r.user_id == user_id else r.user_id for r in rels
        ]
        if not friend_user_ids:
            return []

        users = {u.id: u for u in db.query(User).filter(User.id.in_(friend_user_ids)).all()}
        presences = {
            p.user_id: p
            for p in db.query(UserPresence).filter(UserPresence.user_id.in_(friend_user_ids)).all()
        }

        results = []
        for r in rels:
            fid = r.friend_id if r.user_id == user_id else r.user_id
            u = users.get(fid)
            if not u or not u.is_active:
                continue
            pres = presences.get(fid)
            presence_dict = None
            if pres and pres.status != "invisible":
                st = pres.status
                if st != "offline":
                    if not pres.updated_at:
                        st = "offline"
                    else:
                        updated_dt = pres.updated_at if pres.updated_at.tzinfo else pres.updated_at.replace(tzinfo=timezone.utc)
                        if (_now() - updated_dt).total_seconds() > 120:
                            st = "offline"
                presence_dict = {
                    "status": st,
                    "device_type": pres.device_type,
                    "custom_status": pres.custom_status,
                    "activity_label": pres.activity_label,
                    "activity_detail": pres.activity_detail,
                    "updated_at": pres.updated_at,
                }
            else:
                presence_dict = {
                    "status": "offline",
                    "device_type": pres.device_type if pres else "web",
                    "custom_status": None,
                    "activity_label": None,
                    "activity_detail": None,
                    "updated_at": None,
                }

            results.append({
                "id": r.id,
                "user_id": u.id,
                "username": u.username,
                "avatar_url": u.avatar_url,
                "status": r.status,
                "is_requester": r.user_id == user_id,
                "created_at": r.created_at,
                "presence": presence_dict,
            })
        return results

    @classmethod
    def get_requests(cls, db: Session, user_id: int) -> dict[str, list[dict[str, Any]]]:
        """Liefert offene eingehende und ausgehende Freundschaftsanfragen."""
        incoming_rows = (
            db.query(UserFriend)
            .filter(UserFriend.friend_id == user_id, UserFriend.status == "pending")
            .all()
        )
        outgoing_rows = (
            db.query(UserFriend)
            .filter(UserFriend.user_id == user_id, UserFriend.status == "pending")
            .all()
        )

        all_user_ids = {r.user_id for r in incoming_rows} | {r.friend_id for r in outgoing_rows}
        users = {u.id: u for u in db.query(User).filter(User.id.in_(all_user_ids)).all()} if all_user_ids else {}

        def _format(r: UserFriend, other_id: int, is_req: bool):
            u = users.get(other_id)
            return {
                "id": r.id,
                "user_id": other_id,
                "username": u.username if u else "Unbekannt",
                "avatar_url": u.avatar_url if u else None,
                "status": r.status,
                "is_requester": is_req,
                "created_at": r.created_at,
                "presence": None,
            }

        return {
            "incoming": [_format(r, r.user_id, False) for r in incoming_rows],
            "outgoing": [_format(r, r.friend_id, True) for r in outgoing_rows],
        }

    @classmethod
    def send_friend_request(cls, db: Session, user_id: int, target_username: str) -> dict[str, Any]:
        """Sendet eine neue Freundschaftsanfrage an einen Ziel-Benutzer."""
        clean_name = target_username.strip()
        target = db.query(User).filter(User.username.ilike(clean_name)).first()
        if not target or not target.is_active:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if target.id == user_id:
            raise HTTPException(status_code=400, detail="Sie können sich nicht selbst als Freund hinzufügen")

        existing = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_id, UserFriend.friend_id == target.id),
                    and_(UserFriend.user_id == target.id, UserFriend.friend_id == user_id),
                )
            )
            .first()
        )
        if existing:
            if existing.status == "blocked":
                raise HTTPException(status_code=400, detail="Aktion nicht möglich: Benutzer ist blockiert")
            if existing.status == "accepted":
                raise HTTPException(status_code=400, detail="Sie sind bereits mit diesem Benutzer befreundet")
            if existing.status == "pending":
                if existing.user_id == user_id:
                    raise HTTPException(status_code=400, detail="Freundschaftsanfrage wurde bereits gesendet")
                else:
                    # Der andere hat bereits angefragt -> automatisch annehmen!
                    existing.status = "accepted"
                    existing.updated_at = _now()
                    db.commit()
                    AchievementService.unlock_achievement(db, user_id, "social_handshake")
                    AchievementService.unlock_achievement(db, target.id, "social_handshake")
                    SyncEventService.publish(
                        {"type": "friend_request_accepted", "friend_id": user_id},
                        user_id=target.id,
                    )
                    return {"id": existing.id, "status": "accepted", "message": "Freundschaftsanfrage bestätigt"}

        req = UserFriend(
            user_id=user_id,
            friend_id=target.id,
            status="pending",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(req)
        db.commit()

        # Benachrichtigung via SSE
        SyncEventService.publish(
            {"type": "friend_request_received", "from_user_id": user_id},
            user_id=target.id,
        )
        return {"id": req.id, "status": "pending", "message": "Freundschaftsanfrage erfolgreich gesendet"}

    @classmethod
    def accept_friend_request(cls, db: Session, user_id: int, request_id: int) -> dict[str, Any]:
        """Nimmt eine eingehende Freundschaftsanfrage an."""
        req = db.query(UserFriend).filter_by(id=request_id).first()
        if not req or req.friend_id != user_id or req.status != "pending":
            raise HTTPException(status_code=404, detail="Freundschaftsanfrage nicht gefunden")

        req.status = "accepted"
        req.updated_at = _now()
        db.commit()

        AchievementService.unlock_achievement(db, user_id, "social_handshake")
        AchievementService.unlock_achievement(db, req.user_id, "social_handshake")

        SyncEventService.publish(
            {"type": "friend_request_accepted", "friend_id": user_id},
            user_id=req.user_id,
        )
        return {"ok": True, "status": "accepted", "message": "Freundschaft angenommen"}

    @classmethod
    def decline_or_cancel_request(cls, db: Session, user_id: int, request_id: int) -> bool:
        """Lehnt eine Anfrage ab oder zieht eine eigene zurück."""
        req = db.query(UserFriend).filter_by(id=request_id).first()
        if not req or (req.user_id != user_id and req.friend_id != user_id):
            raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")

        db.delete(req)
        db.commit()
        return True

    @classmethod
    def remove_friend(cls, db: Session, user_id: int, target_user_id: int) -> bool:
        """Entfernt eine bestehende Freundschaft (unterstützt Ziel-Benutzer-ID oder Beziehungs-ID)."""
        rel = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_id, UserFriend.friend_id == target_user_id),
                    and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == user_id),
                    and_(
                        UserFriend.id == target_user_id,
                        or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                    ),
                )
            )
            .first()
        )
        if not rel:
            raise HTTPException(status_code=404, detail="Freundschaft nicht gefunden")

        other_id = rel.friend_id if rel.user_id == user_id else rel.user_id
        db.delete(rel)
        db.commit()

        SyncEventService.publish(
            {"type": "friend_removed", "friend_id": user_id},
            user_id=other_id,
        )
        return True

    @classmethod
    def block_user(cls, db: Session, user_id: int, target_user_id: int) -> bool:
        """Blockiert einen Benutzer und löscht ggf. bestehende Freundschaft."""
        rel = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_id, UserFriend.friend_id == target_user_id),
                    and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == user_id),
                )
            )
            .first()
        )
        if rel:
            rel.user_id = user_id
            rel.friend_id = target_user_id
            rel.status = "blocked"
            rel.updated_at = _now()
        else:
            rel = UserFriend(
                user_id=user_id,
                friend_id=target_user_id,
                status="blocked",
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(rel)
        db.commit()
        return True

    @classmethod
    def unblock_user(cls, db: Session, user_id: int, target_user_id: int) -> bool:
        """Hebt eine Blockierung auf."""
        rel = (
            db.query(UserFriend)
            .filter_by(user_id=user_id, friend_id=target_user_id, status="blocked")
            .first()
        )
        if not rel:
            return False
        db.delete(rel)
        db.commit()
        return True

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

        # Freunde über Statusänderung per SSE informieren (falls nicht unsichtbar)
        friends = cls.get_friends(db, user_id)
        presence_payload = {
            "type": "friend_presence_updated",
            "friend_id": user_id,
            "presence": {
                "status": "offline" if status == "invisible" else status,
                "device_type": pres.device_type,
                "custom_status": custom_status,
                "activity_label": activity_label,
                "activity_detail": activity_detail,
                "updated_at": pres.updated_at.isoformat(),
            },
        }
        for f in friends:
            SyncEventService.publish(presence_payload, user_id=f["user_id"])

        return {
            "status": pres.status,
            "device_type": pres.device_type,
            "custom_status": pres.custom_status,
            "activity_label": pres.activity_label,
            "activity_detail": pres.activity_detail,
            "updated_at": pres.updated_at,
        }

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
        status = pres.status
        if status not in ("offline", "invisible"):
            if not pres.updated_at:
                status = "offline"
            else:
                updated_dt = pres.updated_at if pres.updated_at.tzinfo else pres.updated_at.replace(tzinfo=timezone.utc)
                if (_now() - updated_dt).total_seconds() > 120:
                    status = "offline"

        return {
            "status": status,
            "device_type": pres.device_type,
            "custom_status": pres.custom_status,
            "activity_label": pres.activity_label,
            "activity_detail": pres.activity_detail,
            "updated_at": pres.updated_at,
        }

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
        cls, db: Session, viewer_user_id: int | None, target_user: User
    ) -> dict[str, Any]:
        """Liefert das Benutzerprofil unter Berücksichtigung des 3-Stufen-Modells."""
        is_self = viewer_user_id is not None and viewer_user_id == target_user.id
        privacy = getattr(target_user, "social_privacy", "friends")

        allowed = False
        if is_self or privacy == "public":
            allowed = True
        elif privacy == "friends" and viewer_user_id:
            allowed = cls.is_confirmed_friend(db, viewer_user_id, target_user.id)

        if not allowed:
            return {
                "user_id": target_user.id,
                "username": target_user.username,
                "avatar_url": target_user.avatar_url,
                "privacy": privacy,
                "restricted": True,
                "presence": None,
                "stats": None,
                "achievements": None,
            }

        pres_data = cls.get_presence(db, target_user.id)
        if pres_data["status"] == "invisible" and not is_self:
            pres_data["status"] = "offline"

        stats = AchievementService.get_user_stats(db, target_user.id)
        achievements = AchievementService.get_user_achievements(db, target_user.id)

        return {
            "user_id": target_user.id,
            "username": target_user.username,
            "avatar_url": target_user.avatar_url,
            "privacy": privacy,
            "restricted": False,
            "presence": pres_data,
            "stats": stats,
            "achievements": achievements,
        }

    # --- DIS Zero-Knowledge E2EE Blind Relay ---

    @classmethod
    def save_e2ee_public_key(cls, db: Session, user_id: int, public_key: str) -> None:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        user.social_e2ee_public_key = public_key
        db.commit()

    @classmethod
    def get_e2ee_public_key(cls, db: Session, user_id: int) -> str | None:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return None
        return user.social_e2ee_public_key

    @classmethod
    def relay_blind_envelope(
        cls,
        db: Session,
        blind_mailbox_id: str,
        ciphertext_envelope: str,
        sender_user_id: int | None = None,
        recipient_user_id: int | None = None,
    ) -> E2eeBlindEnvelope:
        """Speichert einen blinden E2EE-Umschlag ohne jegliche Nutzerverknüpfung."""
        clean_mailbox = blind_mailbox_id.strip()
        clean_envelope = ciphertext_envelope.strip()

        envelope = E2eeBlindEnvelope(
            blind_mailbox_id=clean_mailbox,
            ciphertext_envelope=clean_envelope,
            created_at=_now(),
        )
        db.add(envelope)
        db.commit()

        if sender_user_id:
            AchievementService.unlock_achievement(db, sender_user_id, "social_zero_knowledge")

        # Gezielt nur an die teilnehmenden Benutzer versenden (kein globaler Broadcast)
        targets: set[int] = set()
        if sender_user_id:
            targets.add(sender_user_id)
        if recipient_user_id:
            targets.add(recipient_user_id)

        for target_id in targets:
            SyncEventService.publish(
                {
                    "type": "e2ee_blind_message",
                    "blind_mailbox_id": clean_mailbox,
                    "id": envelope.id,
                    "created_at": envelope.created_at.isoformat(),
                },
                user_id=target_id,
            )

        return envelope

    @classmethod
    def get_blind_envelopes(
        cls, db: Session, blind_mailbox_id: str, since_id: int = 0, limit: int = 50
    ) -> list[E2eeBlindEnvelope]:
        """Holt blinde Umschläge aus einer Mailbox ab."""
        query = db.query(E2eeBlindEnvelope).filter(
            E2eeBlindEnvelope.blind_mailbox_id == blind_mailbox_id
        )
        if since_id > 0:
            query = query.filter(E2eeBlindEnvelope.id > since_id)
        return query.order_by(E2eeBlindEnvelope.id.asc()).limit(min(limit, 100)).all()
