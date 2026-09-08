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
    ChatGroup,
    ChatGroupMember,
    ChatStory,
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

        sender = db.query(User).filter_by(id=user_id).first()
        sender_name = sender.username if sender else "Jemand"

        # Benachrichtigung via SSE
        SyncEventService.publish(
            {
                "type": "friend_request_received",
                "from_user_id": user_id,
                "from_username": sender_name,
            },
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

    @classmethod
    def get_public_profiles(
        cls,
        db: Session,
        viewer_user_id: int | None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Ermittelt alle aktiven Benutzer mit öffentlichem Profil ('public') für die Discovery."""
        query = db.query(User).filter(
            User.is_active == True,
            User.social_privacy == "public",
        )
        if viewer_user_id:
            query = query.filter(User.id != viewer_user_id)

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(User.username.ilike(term))

        users = query.order_by(User.username.asc()).offset(offset).limit(limit).all()
        results = []
        for u in users:
            results.append(cls.get_profile(db, viewer_user_id, u))
        return results

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
        group_id: int | None = None,
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
        if group_id:
            group_members = (
                db.query(ChatGroupMember.user_id)
                .filter(ChatGroupMember.group_id == group_id)
                .all()
            )
            for gm in group_members:
                targets.add(gm[0])

        for target_id in targets:
            SyncEventService.publish(
                {
                    "type": "e2ee_blind_message",
                    "blind_mailbox_id": clean_mailbox,
                    "id": envelope.id,
                    "created_at": envelope.created_at.isoformat(),
                    "group_id": group_id,
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

    # --- Chat-Gruppen & Öffentliche Einladungslinks ---

    @classmethod
    def create_group(
        cls,
        db: Session,
        user: User,
        name: str,
        description: str | None = None,
        avatar_url: str | None = None,
    ) -> ChatGroup:
        cls.assert_social_enabled(db)
        clean_name = name.strip()
        if not 2 <= len(clean_name) <= 64:
            raise HTTPException(status_code=422, detail="Gruppenname muss zwischen 2 und 64 Zeichen lang sein.")

        import secrets
        invite_code = secrets.token_urlsafe(16)

        group = ChatGroup(
            name=clean_name,
            description=description.strip() if description else None,
            avatar_url=avatar_url,
            invite_code=invite_code,
            owner_user_id=user.id,
            created_at=_now(),
        )
        db.add(group)
        db.flush()

        member = ChatGroupMember(
            group_id=group.id,
            user_id=user.id,
            role="owner",
            joined_at=_now(),
        )
        db.add(member)
        db.commit()
        db.refresh(group)
        return group

    @classmethod
    def list_user_groups(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        cls.assert_social_enabled(db)
        memberships = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.user_id == user_id)
            .all()
        )
        if not memberships:
            return []

        group_ids = [m.group_id for m in memberships]
        groups = db.query(ChatGroup).filter(ChatGroup.id.in_(group_ids)).all()
        all_members = (
            db.query(ChatGroupMember, User.username, User.avatar_url)
            .join(User, User.id == ChatGroupMember.user_id)
            .filter(ChatGroupMember.group_id.in_(group_ids))
            .all()
        )

        members_by_group: dict[int, list[dict[str, Any]]] = {}
        for mem, uname, uavatar in all_members:
            members_by_group.setdefault(mem.group_id, []).append({
                "user_id": mem.user_id,
                "username": uname,
                "avatar_url": uavatar,
                "role": mem.role,
                "permissions": mem.permissions,
                "joined_at": mem.joined_at,
            })

        user_role_by_group = {m.group_id: m.role for m in memberships}

        results = []
        for g in groups:
            mems = members_by_group.get(g.id, [])
            results.append({
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "avatar_url": g.avatar_url,
                "invite_code": g.invite_code,
                "owner_user_id": g.owner_user_id,
                "default_permissions": g.default_permissions or "send_messages,invite_members",
                "member_count": len(mems),
                "role": user_role_by_group.get(g.id, "member"),
                "created_at": g.created_at,
                "members": mems,
            })

        return sorted(results, key=lambda x: x["name"].casefold())

    @classmethod
    def get_group_by_invite_code(cls, db: Session, invite_code: str) -> ChatGroup:
        cls.assert_social_enabled(db)
        clean_code = invite_code.strip()
        group = db.query(ChatGroup).filter(ChatGroup.invite_code == clean_code).first()
        if not group:
            raise HTTPException(status_code=404, detail="Einladungslink ist ungültig oder abgelaufen.")
        return group

    @classmethod
    def join_group_by_invite_code(cls, db: Session, user: User, invite_code: str) -> ChatGroup:
        cls.assert_social_enabled(db)
        group = cls.get_group_by_invite_code(db, invite_code)
        existing = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group.id, ChatGroupMember.user_id == user.id)
            .first()
        )
        if not existing:
            new_member = ChatGroupMember(
                group_id=group.id,
                user_id=user.id,
                role="member",
                joined_at=_now(),
            )
            db.add(new_member)
            db.commit()
            db.refresh(group)

            SyncEventService.publish(
                {
                    "type": "chat_group_joined",
                    "group_id": group.id,
                    "user_id": user.id,
                    "username": user.username,
                },
                user_id=user.id,
            )

        return group

    @classmethod
    def leave_group(cls, db: Session, user: User, group_id: int) -> None:
        cls.assert_social_enabled(db)
        member = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == user.id)
            .first()
        )
        if not member:
            return

        was_owner = member.role == "owner"
        db.delete(member)
        db.flush()

        # Wenn keine Mitglieder mehr da sind, Gruppe entfernen
        remaining = db.query(ChatGroupMember).filter(ChatGroupMember.group_id == group_id).all()
        if not remaining:
            group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
            if group:
                db.delete(group)
        elif was_owner:
            # Nachfolge für Eigentümer bestimmen
            new_owner = remaining[0]
            new_owner.role = "owner"
            group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
            if group:
                group.owner_user_id = new_owner.user_id
        db.commit()

    @classmethod
    def delete_group(cls, db: Session, user: User, group_id: int) -> None:
        cls.assert_social_enabled(db)
        group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
        if group.owner_user_id != user.id:
            mem = (
                db.query(ChatGroupMember)
                .filter(
                    ChatGroupMember.group_id == group_id,
                    ChatGroupMember.user_id == user.id,
                    ChatGroupMember.role == "owner",
                )
                .first()
            )
            if not mem:
                raise HTTPException(status_code=403, detail="Nur der Eigentümer kann die Gruppe löschen.")
        db.delete(group)
        db.commit()

    @classmethod
    def update_member_role_permissions(
        cls,
        db: Session,
        group_id: int,
        target_user_id: int,
        role: str,
        permissions: str | None,
        caller: User,
    ) -> dict[str, Any]:
        cls.assert_social_enabled(db)
        caller_mem = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == caller.id)
            .first()
        )
        if not caller_mem or caller_mem.role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Ändern von Gruppenrollen.")

        target_mem = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == target_user_id)
            .first()
        )
        if not target_mem:
            raise HTTPException(status_code=404, detail="Gruppenmitglied nicht gefunden.")

        if target_mem.role == "owner" and caller.id != target_mem.user_id:
            raise HTTPException(status_code=403, detail="Die Rolle des Gruppen-Eigentümers kann nicht geändert werden.")

        if target_mem.role == "admin" and caller_mem.role != "owner" and caller.id != target_user_id:
            raise HTTPException(status_code=403, detail="Nur der Eigentümer kann die Rolle anderer Administratoren anpassen.")

        target_mem.role = role
        target_mem.permissions = permissions.strip() if permissions else None
        db.commit()
        db.refresh(target_mem)

        SyncEventService.publish(
            {
                "type": "chat_group_member_updated",
                "group_id": group_id,
                "user_id": target_user_id,
                "role": target_mem.role,
                "permissions": target_mem.permissions,
            },
            user_id=target_user_id,
        )

        target_user = db.query(User).filter(User.id == target_user_id).first()
        return {
            "user_id": target_mem.user_id,
            "username": target_user.username if target_user else f"User #{target_mem.user_id}",
            "avatar_url": target_user.avatar_url if target_user else None,
            "role": target_mem.role,
            "permissions": target_mem.permissions,
            "joined_at": target_mem.joined_at,
        }

    @classmethod
    def kick_group_member(
        cls,
        db: Session,
        group_id: int,
        target_user_id: int,
        caller: User,
    ) -> None:
        cls.assert_social_enabled(db)
        caller_mem = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == caller.id)
            .first()
        )
        if not caller_mem:
            raise HTTPException(status_code=403, detail="Du bist kein Mitglied dieser Gruppe.")

        can_kick = (
            caller_mem.role in ("owner", "admin")
            or (caller_mem.permissions and "kick_members" in caller_mem.permissions)
        )
        if not can_kick:
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Entfernen von Mitgliedern.")

        target_mem = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == target_user_id)
            .first()
        )
        if not target_mem:
            raise HTTPException(status_code=404, detail="Gruppenmitglied nicht gefunden.")

        if target_mem.role == "owner":
            raise HTTPException(status_code=403, detail="Der Eigentümer der Gruppe kann nicht entfernt werden.")

        if target_mem.role == "admin" and caller_mem.role != "owner":
            raise HTTPException(status_code=403, detail="Nur der Eigentümer kann Administratoren entfernen.")

        db.delete(target_mem)
        db.commit()

        SyncEventService.publish(
            {
                "type": "chat_group_member_kicked",
                "group_id": group_id,
                "user_id": target_user_id,
            },
            user_id=target_user_id,
        )

    @classmethod
    def update_group_default_permissions(
        cls,
        db: Session,
        group_id: int,
        default_permissions: str,
        caller: User,
    ) -> ChatGroup:
        cls.assert_social_enabled(db)
        caller_mem = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == caller.id)
            .first()
        )
        if not caller_mem or caller_mem.role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung zum Konfigurieren der Standard-Gruppenrechte.")

        group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")

        clean_perms = default_permissions.strip()
        group.default_permissions = clean_perms
        db.commit()
        db.refresh(group)

        # Notify all group members
        group_members = (
            db.query(ChatGroupMember.user_id)
            .filter(ChatGroupMember.group_id == group_id)
            .all()
        )
        for gm in group_members:
            SyncEventService.publish(
                {
                    "type": "chat_group_permissions_updated",
                    "group_id": group.id,
                    "default_permissions": group.default_permissions,
                },
                user_id=gm[0],
            )

        return group

    # --- Stories (Temporäre Statusmeldungen, 24h) ---

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
        clean_content = content.strip()
        if not clean_content:
            raise HTTPException(status_code=422, detail="Story-Inhalt darf nicht leer sein.")

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

        stories = (
            db.query(ChatStory, User.username, User.avatar_url)
            .join(User, User.id == ChatStory.user_id)
            .filter(
                ChatStory.user_id.in_(friend_ids),
                ChatStory.expires_at > now,
            )
            .order_by(ChatStory.created_at.desc())
            .all()
        )

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
            for s, uname, uavatar in stories
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

