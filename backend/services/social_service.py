from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from fastapi import HTTPException
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import hashlib
from models import (
    User,
    UserFriend,
    UserPresence,
    E2eeBlindEnvelope,
    ChatGroup,
    ChatGroupMember,
    ChatStory,
    DirectChat,
)
from services.panel_settings_service import PanelSettingsService
from services.sync_event_service import SyncEventService
from services.achievement_service import AchievementService
from services.notification_service import NotificationService
from services.call_room_service import GroupCallRoomRegistry

logger = logging.getLogger(__name__)


#: Alle Rechte, die eine Gruppenrolle tragen kann. Wer hier nichts stehen hat,
#: kann nicht gesetzt werden — siehe ``SocialService.assert_known_permissions``.
GROUP_PERMISSIONS: frozenset[str] = frozenset(
    {
        "send_messages",
        "attach_media",
        "invite_members",
        "start_group_calls",
        "join_group_calls",
        "share_screen",
        "mute_in_calls",
        "kick_from_calls",
        "kick_members",
        "delete_messages",
        "manage_roles",
        "mention_everyone",
        "pin_messages",
    }
)

#: Rechte rund um den Gruppenanruf. Eigentümer und Administratoren haben sie
#: ohne Eintrag — sie können sie sich ohnehin jederzeit selbst geben.
GROUP_CALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "start_group_calls",
        "join_group_calls",
        "share_screen",
        "mute_in_calls",
        "kick_from_calls",
    }
)

#: Rechte, die in die laufende Unterhaltung eingreifen: alle auf einmal wecken
#: und eine Nachricht über den Verlauf heften. Dieselbe Begründung wie bei den
#: Anrufrechten — wer die Rollen verwaltet, kann sie sich ohnehin selbst
#: eintragen, und ein Eigentümer, der seine eigene Gruppe nicht erreichen darf,
#: wäre kein Schutz, sondern ein Rätsel.
GROUP_MODERATION_PERMISSIONS: frozenset[str] = frozenset(
    {
        "mention_everyone",
        "pin_messages",
    }
)

#: Was der Rechte-Dialog vor dem 17.09.2026 geschrieben hat. Wird beim Lesen
#: übersetzt, damit bereits gesetzte Haken nicht verloren gehen.
GROUP_PERMISSION_ALIASES: dict[str, tuple[str, ...]] = {
    "call_start": ("start_group_calls",),
    "call_join": ("join_group_calls",),
    "call_share": ("share_screen",),
    "call_moderate": ("mute_in_calls", "kick_from_calls"),
}


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
        """Liefert alle bestätigten Freunde des Benutzers samt aktueller Präsenz.

        Je Freund steht hier genau ein Eintrag. Die Eindeutigkeit in
        ``user_friends`` gilt je Richtung: ``(a, b)`` und ``(b, a)`` sind zwei
        erlaubte Zeilen, und beide erfüllen den Filter unten. Wo ein solches
        Paar im Bestand liegt, zählt die ältere Zeile; dafür steht die feste
        Sortierung.
        """
        rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .order_by(UserFriend.id)
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
        bereits_gelistet: set[int] = set()
        for r in rels:
            fid = r.friend_id if r.user_id == user_id else r.user_id
            if fid in bereits_gelistet:
                continue
            u = users.get(fid)
            if not u or not u.is_active:
                continue
            bereits_gelistet.add(fid)
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
        # Jede Zeile dieser Beziehung, nicht nur die erste. Liegt die
        # Freundschaft gespiegelt im Bestand, bliebe die zweite Zeile stehen und
        # die beiden wären nach dem Entfernen weiterhin befreundet.
        db.query(UserFriend).filter(
            or_(
                and_(UserFriend.user_id == user_id, UserFriend.friend_id == other_id),
                and_(UserFriend.user_id == other_id, UserFriend.friend_id == user_id),
            )
        ).delete(synchronize_session=False)
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
            # Erst die Spiegelzeilen derselben Beziehung entfernen: bliebe eine
            # davon auf "accepted" stehen, wäre der Blockierte weiterhin ein
            # bestätigter Freund, und `social_privacy="friends"` gäbe ihm
            # weiter Einblick. Das Löschen geht der Umschreibung voraus, sonst
            # stößt sie auf die Eindeutigkeit von (user_id, friend_id).
            db.query(UserFriend).filter(
                UserFriend.id != rel.id,
                or_(
                    and_(UserFriend.user_id == user_id, UserFriend.friend_id == target_user_id),
                    and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == user_id),
                ),
            ).delete(synchronize_session=False)
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
    def get_blocked_users(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        """Liefert alle von diesem Benutzer blockierten Kontakte."""
        rels = (
            db.query(UserFriend)
            .filter_by(user_id=user_id, status="blocked")
            .all()
        )
        if not rels:
            return []
        blocked_ids = [r.friend_id for r in rels]
        users = {u.id: u for u in db.query(User).filter(User.id.in_(blocked_ids)).all()}
        results = []
        for r in rels:
            u = users.get(r.friend_id)
            if u and u.is_active:
                results.append({
                    "id": r.id,
                    "user_id": u.id,
                    "username": u.username,
                    "avatar_url": u.avatar_url,
                    "status": "blocked",
                    "created_at": r.created_at,
                })
        return results

    @classmethod
    def derive_blind_mailbox_id(cls, user_a_id: int, user_b_id: int, salt: str = "") -> str:
        """Deterministische Hash-Berechnung der blinden E2EE-Mailbox-ID für zwei Benutzer.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts und personal_proposals.py.
        """
        min_id = min(user_a_id, user_b_id)
        max_id = max(user_a_id, user_b_id)
        raw = f"msm:dm:{min_id}:{max_id}{f':{salt}' if salt else ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_user_device_mailbox_id(cls, user_id: int) -> str:
        """Deterministische Hash-Berechnung der blinden Geräte-Mailbox-ID für die Geräte eines Benutzers.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts.
        """
        raw = f"msm:devices:{user_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_group_blind_mailbox_id(cls, group_id: int) -> str:
        """Deterministische Hash-Berechnung der blinden Gruppen-Mailbox-ID.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts.
        """
        raw = f"msm:group:{group_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_legacy_direct_mailbox_id(cls, user_a_id: int, user_b_id: int) -> str:
        """Alte Ableitung der Mailbox-ID vor Einführung von msm:dm:<min>:<max>."""
        min_i, max_i = min(user_a_id, user_b_id), max(user_a_id, user_b_id)
        return hashlib.sha256(f"msm-e2ee-box:{min_i}:{max_i}:".encode("utf-8")).hexdigest()

    @classmethod
    def is_blocked(cls, db: Session, user_a_id: int, user_b_id: int) -> bool:
        """Prüft, ob zwischen zwei Benutzern eine gegenseitige Blockierung vorliegt."""
        return (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_a_id, UserFriend.friend_id == user_b_id),
                    and_(UserFriend.user_id == user_b_id, UserFriend.friend_id == user_a_id),
                ),
                UserFriend.status == "blocked",
            )
            .first()
        ) is not None

    @classmethod
    def resolve_mailbox_target(
        cls,
        db: Session,
        sender_user_id: int,
        blind_mailbox_id: str,
        recipient_id: int | None = None,
    ) -> tuple[int | None, list[int], DirectChat | None, int | None]:
        """Ermittelt das Ziel einer blinden Mailbox für den Absender.

        Liefert (target_recipient_id, group_member_ids, direct_chat, target_group_id).
        Wirft HTTPException bei fehlender Berechtigung oder ungültigem Empfänger.
        """
        clean_mailbox = blind_mailbox_id.strip()
        cls.assert_social_enabled(db)
        user_device_box = cls.derive_user_device_mailbox_id(sender_user_id)

        if clean_mailbox == user_device_box or (recipient_id and recipient_id == sender_user_id):
            if clean_mailbox != user_device_box:
                raise HTTPException(
                    status_code=400,
                    detail="Mailbox-ID stimmt nicht mit der Geräte-Sync-Mailbox überein.",
                )
            return sender_user_id, [], None, None

        if recipient_id:
            expected_mailbox = cls.derive_blind_mailbox_id(sender_user_id, recipient_id)
            legacy_mailbox = cls.derive_legacy_direct_mailbox_id(sender_user_id, recipient_id)
            if clean_mailbox != expected_mailbox and clean_mailbox != legacy_mailbox:
                raise HTTPException(
                    status_code=400,
                    detail="Mailbox-ID stimmt nicht mit dem angegebenen Empfänger überein.",
                )
            chat = cls.ensure_direct_chat(db, sender_user_id, recipient_id)
            return recipient_id, [], chat, None

        # 1. Direktchat über bekannte Mailbox-ID in DB
        chat = db.query(DirectChat).filter_by(blind_mailbox_id=clean_mailbox).first()
        if chat:
            if sender_user_id not in (chat.user_a_id, chat.user_b_id):
                raise HTTPException(status_code=403, detail="Keine Berechtigung für diesen Chat.")
            other_id = chat.get_other_user_id(sender_user_id)
            if cls.is_blocked(db, sender_user_id, other_id):
                raise HTTPException(status_code=403, detail="Benutzer ist blockiert.")
            chat.updated_at = _now()
            db.commit()
            return other_id, [], chat, None

        # 2. Kandidatensuche über aktive Benutzer (O(N) Fallback)
        candidates = db.query(User.id).filter(User.is_active == True, User.id != sender_user_id).all()
        for (cand_id,) in candidates:
            if (
                cls.derive_blind_mailbox_id(sender_user_id, cand_id) == clean_mailbox
                or cls.derive_legacy_direct_mailbox_id(sender_user_id, cand_id) == clean_mailbox
            ):
                if cls.is_blocked(db, sender_user_id, cand_id):
                    raise HTTPException(status_code=403, detail="Benutzer ist blockiert.")
                chat = cls.ensure_direct_chat(db, sender_user_id, cand_id)
                return cand_id, [], chat, None

        # 3. Gruppen-Mailbox prüfen
        user_groups = (
            db.query(ChatGroupMember.group_id)
            .filter_by(user_id=sender_user_id)
            .all()
        )
        for (gid,) in user_groups:
            if cls.derive_group_blind_mailbox_id(gid) == clean_mailbox:
                group_member_ids = [
                    m.user_id
                    for m in db.query(ChatGroupMember.user_id)
                    .filter_by(group_id=gid)
                    .all()
                ]
                return None, group_member_ids, None, gid

        return None, [], None, None

    @classmethod
    def can_message_user(cls, db: Session, sender_id: int, target_user_id: int) -> tuple[bool, str | None]:
        """Prüft Berechtigung zum Senden von Direktnachrichten zwischen zwei Benutzern.

        Regeln:
        1. Selbstgespräche nicht erlaubt.
        2. Blockierung verbietet jegliche Kommunikation.
        3. Existiert bereits ein Chat, darf geantwortet/weitergeschrieben werden (in beide Richtungen).
        4. Bestätigte Freunde dürfen sich immer schreiben.
        5. Nicht-Freunde dürfen schreiben, wenn der Empfänger ein öffentliches Profil hat.
        6. Ansonsten (Empfänger ist 'private' oder 'friends' ohne bestehenden Chat) verboten.
        """
        if sender_id == target_user_id:
            return False, "Selbstgespräche werden nicht unterstützt."

        # Prüfe auf Blockierung
        blocked = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == sender_id, UserFriend.friend_id == target_user_id),
                    and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == sender_id),
                ),
                UserFriend.status == "blocked",
            )
            .first()
        )
        if blocked:
            return False, "Kommunikation nicht möglich: Benutzer ist blockiert."

        # Prüfe ob bereits ein DirectChat existiert (Antwort-Erlaubnis in beide Richtungen)
        min_id, max_id = min(sender_id, target_user_id), max(sender_id, target_user_id)
        existing_chat = (
            db.query(DirectChat)
            .filter(DirectChat.user_a_id == min_id, DirectChat.user_b_id == max_id)
            .first()
        )
        if existing_chat:
            return True, None

        # Prüfe auch, ob schon Umschläge in der abgeleiteten Mailbox existieren
        derived_mid = cls.derive_blind_mailbox_id(sender_id, target_user_id)
        existing_envelope = (
            db.query(E2eeBlindEnvelope)
            .filter(E2eeBlindEnvelope.blind_mailbox_id == derived_mid)
            .first()
        )
        if existing_envelope:
            return True, None

        # Prüfe ob Freunde
        if cls.is_confirmed_friend(db, sender_id, target_user_id):
            return True, None

        # Empfänger laden
        target = db.query(User).filter_by(id=target_user_id).first()
        if not target or not target.is_active:
            return False, "Empfänger nicht gefunden oder inaktiv."

        target_privacy = getattr(target, "social_privacy", "friends")
        if target_privacy == "public":
            return True, None

        return False, "Dieser Benutzer nimmt Nachrichten nur von bestätigten Freunden an."

    @classmethod
    def ensure_direct_chat(cls, db: Session, sender_id: int, target_user_id: int) -> DirectChat:
        """Stellt sicher, dass ein DirectChat-Eintrag existiert, sofern die Berechtigung vorliegt."""
        can_msg, reason = cls.can_message_user(db, sender_id, target_user_id)
        if not can_msg:
            raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung zum Senden einer Nachricht.")

        min_id, max_id = min(sender_id, target_user_id), max(sender_id, target_user_id)
        chat = (
            db.query(DirectChat)
            .filter(DirectChat.user_a_id == min_id, DirectChat.user_b_id == max_id)
            .first()
        )
        if not chat:
            blind_mid = cls.derive_blind_mailbox_id(sender_id, target_user_id)
            chat = DirectChat(
                user_a_id=min_id,
                user_b_id=max_id,
                blind_mailbox_id=blind_mid,
                initiated_by_user_id=sender_id,
                created_at=_now(),
                updated_at=_now(),
            )
            try:
                db.add(chat)
                db.commit()
                db.refresh(chat)
            except Exception:
                db.rollback()
                chat = (
                    db.query(DirectChat)
                    .filter(DirectChat.user_a_id == min_id, DirectChat.user_b_id == max_id)
                    .first()
                )
                if not chat:
                    raise
        else:
            chat.updated_at = _now()
            db.commit()
        return chat

    @classmethod
    def list_direct_chats(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        """Liefert alle aktiven 1:1-Chats des Benutzers samt Präsenz und Freundesstatus."""
        cls.assert_social_enabled(db)
        chats = (
            db.query(DirectChat)
            .filter(or_(DirectChat.user_a_id == user_id, DirectChat.user_b_id == user_id))
            .order_by(DirectChat.updated_at.desc())
            .all()
        )
        if not chats:
            return []

        other_ids = [c.get_other_user_id(user_id) for c in chats]
        users = {u.id: u for u in db.query(User).filter(User.id.in_(other_ids)).all()}

        blocked_rels = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "blocked",
            )
            .all()
        )
        blocked_user_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in blocked_rels}

        results = []
        for c in chats:
            oid = c.get_other_user_id(user_id)
            other = users.get(oid)
            if not other or not other.is_active:
                continue

            is_friend = cls.is_confirmed_friend(db, user_id, oid)
            is_blocked = oid in blocked_user_ids
            pres = cls.get_presence_for_viewer(db, user_id, other)

            results.append({
                "id": c.id,
                "other_user_id": other.id,
                "other_username": other.username,
                "other_avatar_url": other.avatar_url,
                "blind_mailbox_id": c.blind_mailbox_id,
                "is_friend": is_friend,
                "is_blocked": is_blocked,
                "other_privacy": getattr(other, "social_privacy", "friends"),
                "presence": pres,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            })
        return results

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
            # Öffentlich & nicht unsichtbar: Chat-Partner und autorisierte Abonnenten einbeziehen
            relevant_viewer_ids = {r.friend_id if r.user_id == user_id else r.user_id for r in friend_rels}
            direct_chats = (
                db.query(DirectChat)
                .filter(or_(DirectChat.user_a_id == user_id, DirectChat.user_b_id == user_id))
                .all()
            )
            for dc in direct_chats:
                relevant_viewer_ids.add(dc.get_other_user_id(user_id))
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
        u = db.query(User).filter_by(id=user_id).first()
        pres = cls.get_presence(db, user_id)
        is_invisible = pres.get("status") == "invisible"
        privacy = getattr(u, "social_privacy", "friends") if u else "friends"

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

        if privacy == "public":
            direct_chats = (
                db.query(DirectChat)
                .filter(or_(DirectChat.user_a_id == user_id, DirectChat.user_b_id == user_id))
                .all()
            )
            for dc in direct_chats:
                target_user_ids.add(dc.get_other_user_id(user_id))

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
        stats = AchievementService.get_user_stats(db, target_user.id)
        achievements = AchievementService.get_user_achievements(db, target_user.id)

        return {
            "user_id": target_user.id,
            "username": target_user.username,
            "avatar_url": target_user.avatar_url,
            "privacy": privacy,
            "restricted": False,
            "is_friend": is_friend,
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
            User.is_active == True,
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
            results.append(cls.get_profile(db, viewer_user_id, u))
        return results

    # --- DIS Zero-Knowledge E2EE Blind Relay ---

    # Die E2EE-Schluessel haengen am Geraet, nicht am Konto:
    # `services/e2ee_device_service.py`. Hier standen bis 09/2026
    # `save_e2ee_public_key`, `get_e2ee_public_key`, `get_e2ee_keyring` und
    # `save_e2ee_keyring` — der Kontoschluesselbund, den ein Mensch mit einem
    # abgetippten Wiederherstellungsschluessel oeffnete. Er ist mit dem Double
    # Ratchet unvereinbar: zwei Geraete mit demselben privaten Schluessel
    # entschluesseln dieselbe Nachricht und driften auseinander.

    @classmethod
    def relay_blind_envelope(
        cls,
        db: Session,
        blind_mailbox_id: str,
        ciphertext_envelope: str,
        sender_user_id: int | None = None,
        recipient_id: int | None = None,
        client_uuid: str | None = None,
        is_control: bool = False,
        control_type: str | None = None,
    ) -> E2eeBlindEnvelope:

        """Speichert einen blinden E2EE-Umschlag mit serverseitiger Berechtigungsprüfung.

        sender_user_id und recipient_id werden im SSE-Event mitgeliefert, damit
        Outgoing Echo Prevention und striktes Empfänger-Filtering greifen.
        client_uuid garantiert Idempotenz bei Netzwerk-Schwankungen und Retries.
        """
        clean_mailbox = blind_mailbox_id.strip()
        clean_envelope = ciphertext_envelope.strip()
        clean_client_uuid = client_uuid.strip() if client_uuid and client_uuid.strip() else None

        from schemas.social import validate_e2ee_envelope_format
        try:
            validate_e2ee_envelope_format(clean_envelope)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        target_recipient_id: int | None = None
        group_member_ids: list[int] = []

        if sender_user_id:
            target_recipient_id, group_member_ids, _, _ = cls.resolve_mailbox_target(
                db,
                sender_user_id=sender_user_id,
                blind_mailbox_id=clean_mailbox,
                recipient_id=recipient_id,
            )
            if target_recipient_id is None and not group_member_ids:
                raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

        # Idempotenz-Prüfung: Erst NACH erfolgreicher Autorisierung prüfen,
        # ob dieser Umschlag bereits mit dieser client_uuid existiert.
        #
        # Dieselbe Prüfung stand bis 09/2026 zusätzlich **vor** dem
        # Berechtigungsblock. Damit war sie ein Weg daran vorbei: die
        # Mailbox-Kennung ist `sha256("msm:dm:<min>:<max>")` und für jeden
        # ausrechenbar, und wer mit einer passenden client_uuid ankam, bekam den
        # gespeicherten Umschlag zurück, ohne dass je geprüft wurde, ob er zu
        # diesem Gespräch gehört. Aufgefallen ist es, weil der zugehörige Test
        # aus dem falschen Grund grün war (sein Umschlag war schon formal
        # ungültig und flog früher raus).
        if clean_client_uuid:
            existing = (
                db.query(E2eeBlindEnvelope)
                .filter(
                    E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                    E2eeBlindEnvelope.client_uuid == clean_client_uuid,
                )
                .first()
            )
            if existing:
                return existing

        # Wiedereinspielung: derselbe Chiffretext, irgendwo schon einmal
        # gesehen. Zwei echte Verschlüsselungen desselben Textes ergeben nie
        # dasselbe Byte — der Ratchet zieht je Nachricht einen neuen Schlüssel,
        # AES-GCM eine neue Nonce. Ein Treffer ist also eine Kopie, kein Zufall.
        #
        # Die Reihenfolge ist wichtig: Der Wiederholungsversuch eines legitimen
        # Absenders trägt dieselbe client_uuid und ist eine Zeile weiter oben
        # schon beantwortet. Stünde diese Prüfung davor, bekäme jeder
        # Netzwerk-Retry eine 409 statt der Bestätigung.
        envelope_hash = hashlib.sha256(clean_envelope.encode("utf-8")).hexdigest()
        replay_found = (
            db.query(E2eeBlindEnvelope.id)
            .filter(
                or_(
                    E2eeBlindEnvelope.ciphertext_sha256 == envelope_hash,
                    and_(
                        E2eeBlindEnvelope.ciphertext_sha256.is_(None),
                        E2eeBlindEnvelope.ciphertext_envelope == clean_envelope,
                    ),
                )
            )
            .first()
        )
        if replay_found:
            raise HTTPException(
                status_code=409,
                detail="Replay-Angriff erkannt: Dieser verschlüsselte Umschlag wurde bereits übertragen.",
            )

        envelope = E2eeBlindEnvelope(
            blind_mailbox_id=clean_mailbox,
            ciphertext_envelope=clean_envelope,
            ciphertext_sha256=envelope_hash,
            client_uuid=clean_client_uuid,
            created_at=_now(),
        )
        db.add(envelope)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if clean_client_uuid:
                existing = (
                    db.query(E2eeBlindEnvelope)
                    .filter(
                        E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                        E2eeBlindEnvelope.client_uuid == clean_client_uuid,
                    )
                    .first()
                )
                if existing:
                    return existing
            raise

        msg_payload = {
            "type": "e2ee_blind_message",
            "blind_mailbox_id": clean_mailbox,
            "id": envelope.id,
            "client_uuid": envelope.client_uuid,
            "created_at": envelope.created_at.isoformat(),
            "sender_user_id": sender_user_id,
            "recipient_id": recipient_id if recipient_id is not None else target_recipient_id,
            "is_control": is_control,
            "control_type": control_type,
        }

        if target_recipient_id and sender_user_id:
            SyncEventService.publish(msg_payload, user_id=target_recipient_id)
            if target_recipient_id != sender_user_id:
                SyncEventService.publish(msg_payload, user_id=sender_user_id)
                # Push-Dispatching für Offline-Empfänger vorbereiten (strikte Echo- & Foreground-Prüfung)
                NotificationService.prepare_push_dispatch(
                    target_user_id=target_recipient_id,
                    sender_user_id=sender_user_id,
                    title="Neue Nachricht",
                    is_e2ee=True,
                    is_control=is_control,
                    control_type=control_type,
                    has_active_foreground_connection=SyncEventService.has_active_subscribers(target_recipient_id),
                )
        elif group_member_ids:
            # Gruppen-Nachrichten zielgerichtet nur an Mitglieder ausliefern (Zero Privacy Leak)
            for g_uid in group_member_ids:
                SyncEventService.publish(msg_payload, user_id=g_uid)
                if sender_user_id and not NotificationService.is_outgoing_echo(
                    sender_user_id=sender_user_id, current_user_id=g_uid
                ):
                    NotificationService.prepare_push_dispatch(
                        target_user_id=g_uid,
                        sender_user_id=sender_user_id,
                        title="Neue Gruppennachricht",
                        is_e2ee=True,
                        is_control=is_control,
                        control_type=control_type,
                        has_active_foreground_connection=SyncEventService.has_active_subscribers(g_uid),
                        extra_data={"is_group": True},
                    )
        else:
            SyncEventService.publish(msg_payload)

        return envelope

    @classmethod
    def assert_mailbox_participant(cls, db: Session, user_id: int, blind_mailbox_id: str) -> None:
        """Prüft, ob ein Konto zu einer blinden Mailbox gehört.

        Die Mailbox nennt ihre Teilnehmer nicht, das ist ihr Zweck. Geprüft wird
        deshalb andersherum: aus den Direktchats, Gruppen und Freundschaften
        dieses Kontos werden dieselben Kennungen abgeleitet, die auch der Client
        rechnet. Ist die gesuchte nicht darunter, gehört das Konto nicht dazu.

        Die eigene Geräte-Mailbox zählt mit, eine fremde nie: deren Kennung
        entsteht aus einer anderen Konto-Id und taucht in keiner der Ableitungen
        hier auf.
        """
        clean = (blind_mailbox_id or "").strip()
        if not clean:
            raise HTTPException(status_code=400, detail="Mailbox-ID fehlt.")

        if clean == cls.derive_user_device_mailbox_id(user_id):
            return

        chat = db.query(DirectChat).filter_by(blind_mailbox_id=clean).first()
        if chat and user_id in (chat.user_a_id, chat.user_b_id):
            return

        for (gid,) in (
            db.query(ChatGroupMember.group_id).filter(ChatGroupMember.user_id == user_id).all()
        ):
            if cls.derive_group_blind_mailbox_id(gid) == clean:
                return

        friends = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )
        for f in friends:
            other = f.friend_id if f.user_id == user_id else f.user_id
            if cls.derive_blind_mailbox_id(user_id, other) == clean:
                return

        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

    @classmethod
    def delete_blind_envelopes(
        cls, db: Session, blind_mailbox_id: str, client_uuid: str, user_id: int
    ) -> int:
        """Entfernt die Umschläge einer Nachricht aus einer blinden Mailbox.

        Eine gelöschte Nachricht muss auch hier verschwinden. Bliebe der
        Chiffretext liegen, hätte das Löschen nur die Anzeige auf zwei Geräten
        geändert: ein neu eingerichtetes Gerät holte die Mailbox von vorn und
        bekäme sie zurück.

        Eine Nachricht liegt als mehrere Umschläge da, einer je Zielgerät,
        auseinandergehalten durch `<kennung>#<geraet>`. Gelöscht wird deshalb
        die logische Kennung samt aller Gerätekopien — und nur sie, nie die
        Mailbox als Ganzes.

        Grenze, die bleibt: Wer zu einer Mailbox gehört, kann darin jeden
        Umschlag löschen, dessen Kennung er kennt. Der Server kann das nicht
        enger fassen, ohne zu wissen, wer welchen Umschlag geschrieben hat — und
        genau das weiß er absichtlich nicht. In einem Direktchat trifft das nur
        Kopien, die der Löschende ohnehin schon hat; in einer Gruppe kann ein
        Mitglied damit eine fremde Nachricht aus dem Relais nehmen, bevor andere
        sie abholen. Das ist der Preis der blinden Adressierung und die
        Alternative wäre, den Absender an den Umschlag zu schreiben.

        Nebenwirkung: Ein gelöschter Umschlag fällt aus der
        Wiedereinspielungsprüfung. Wer denselben Chiffretext erneut einliefert,
        beschädigt damit nur seine eigene Sitzung — der Ratchet-Kopf adressiert
        ein einzelnes Gerät, und andere Geräte werten ihn als fremd.
        """
        cls.assert_social_enabled(db)
        clean_mailbox = (blind_mailbox_id or "").strip()
        clean_uuid = (client_uuid or "").strip()
        if not clean_uuid:
            raise HTTPException(status_code=400, detail="client_uuid fehlt.")

        cls.assert_mailbox_participant(db, user_id, clean_mailbox)

        rows = (
            db.query(E2eeBlindEnvelope)
            .filter(
                E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                or_(
                    E2eeBlindEnvelope.client_uuid == clean_uuid,
                    E2eeBlindEnvelope.client_uuid.startswith(f"{clean_uuid}#", autoescape=True),
                ),
            )
            .all()
        )
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)

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
            return query.order_by(E2eeBlindEnvelope.id.asc()).limit(min(limit, 200)).all()

        total = query.count()
        fetch_limit = min(limit, 200)
        if total > fetch_limit:
            sub = query.order_by(E2eeBlindEnvelope.id.desc()).limit(fetch_limit).subquery()
            from sqlalchemy import select
            return (
                db.query(E2eeBlindEnvelope)
                .filter(E2eeBlindEnvelope.id.in_(select(sub.c.id)))
                .order_by(E2eeBlindEnvelope.id.asc())
                .all()
            )
        return query.order_by(E2eeBlindEnvelope.id.asc()).limit(fetch_limit).all()

    @classmethod
    def sync_mailboxes(
        cls, db: Session, current_user: User, since_id: int = 0
    ) -> list[dict[str, Any]]:
        """Ermittelt alle blinden Mailboxen des Benutzers mit Umschlägen nach since_id.

        Zero-Knowledge Invariante:
        Es werden AUSSCHLIESSLICH Mailboxen zurückgegeben, an denen der Benutzer
        nachweislich beteiligt ist (DirectChat, ChatGroupMember oder bestätigter UserFriend).
        """
        cls.assert_social_enabled(db)
        uid = current_user.id
        effective_since = max(0, since_id) if since_id is not None else 0

        # 1. 1:1 DirectChats des Benutzers
        direct_mids = [
            row[0]
            for row in db.query(DirectChat.blind_mailbox_id)
            .filter(or_(DirectChat.user_a_id == uid, DirectChat.user_b_id == uid))
            .all()
            if row[0]
        ]

        # 2. Chat-Gruppen des Benutzers
        group_ids = [
            row[0]
            for row in db.query(ChatGroupMember.group_id)
            .filter(ChatGroupMember.user_id == uid)
            .all()
        ]
        group_mids = [
            hashlib.sha256(f"msm:group:{gid}".encode("utf-8")).hexdigest()
            for gid in group_ids
        ]

        # 3. Bestätigte Freunde (zur Absicherung vor persistiertem DirectChat)
        friends = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == uid, UserFriend.friend_id == uid),
                UserFriend.status == "accepted",
            )
            .all()
        )
        friend_ids = {f.friend_id if f.user_id == uid else f.user_id for f in friends}
        friend_mids = [
            cls.derive_blind_mailbox_id(uid, fid)
            for fid in friend_ids
        ]

        device_mid = cls.derive_user_device_mailbox_id(uid)
        all_mids = list(set(direct_mids) | set(group_mids) | set(friend_mids) | {device_mid})
        if not all_mids:
            return []

        # 4. Aggregiere Mailbox-Statistiken über E2eeBlindEnvelope in Batches à 500
        results: list[dict[str, Any]] = []
        chunk_size = 500
        for i in range(0, len(all_mids), chunk_size):
            chunk = all_mids[i : i + chunk_size]
            rows = (
                db.query(
                    E2eeBlindEnvelope.blind_mailbox_id,
                    func.max(E2eeBlindEnvelope.id).label("max_envelope_id"),
                    func.count(E2eeBlindEnvelope.id).label("unread_count"),
                )
                .filter(
                    E2eeBlindEnvelope.blind_mailbox_id.in_(chunk),
                    E2eeBlindEnvelope.id > effective_since,
                )
                .group_by(E2eeBlindEnvelope.blind_mailbox_id)
                .all()
            )
            for mid, max_id, count in rows:
                results.append({
                    "blind_mailbox_id": mid,
                    "max_envelope_id": int(max_id),
                    "unread_count": int(count),
                })

        results.sort(key=lambda item: item["max_envelope_id"], reverse=True)
        return results

    @classmethod
    def broadcast_typing_signal(
        cls,
        blind_mailbox_id: str,
        status: str,
        sender_id: int,
        sender_username: str,
        db: Session | None = None,
        recipient_id: int | None = None,
    ) -> None:
        """Verteilt ein flüchtiges Tipp- oder Sprachaufnahme-Signal ohne Speicherung."""
        clean_mailbox = blind_mailbox_id.strip()

        target_recipient_id: int | None = recipient_id
        group_member_ids: list[int] = []
        if db:
            if not target_recipient_id:
                try:
                    resolved_target, all_members, _, _ = cls.resolve_mailbox_target(
                        db,
                        sender_user_id=sender_id,
                        blind_mailbox_id=clean_mailbox,
                        recipient_id=recipient_id,
                    )
                    target_recipient_id = resolved_target
                    if all_members:
                        group_member_ids = [m for m in all_members if m != sender_id]
                except HTTPException:
                    return

            if target_recipient_id and cls.is_blocked(db, sender_id, target_recipient_id):
                return

        payload = {
            "type": "e2ee_typing_signal",
            "blind_mailbox_id": clean_mailbox,
            "status": status,
            "sender_id": sender_id,
            "sender_username": sender_username,
        }

        if target_recipient_id:
            SyncEventService.publish(payload, user_id=target_recipient_id)
        elif group_member_ids:
            for g_uid in group_member_ids:
                SyncEventService.publish(payload, user_id=g_uid)
        else:
            SyncEventService.publish(payload)

    # --- Chat-Gruppen & Öffentliche Einladungslinks ---

    @classmethod
    def get_group_member(cls, db: Session, group_id: int, user_id: int) -> ChatGroupMember | None:
        return (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == user_id)
            .first()
        )

    @classmethod
    def expand_group_permissions(cls, raw: str | None) -> set[str]:
        """Parst eine gespeicherte Rechteliste und löst Altbezeichnungen auf.

        Der Rechte-Dialog hat eine Zeit lang eigene Schlüssel geschrieben
        (``call_start`` statt ``start_group_calls`` und so fort), die hier nie
        geprüft wurden — gesetzte Haken blieben wirkungslos. Statt die Daten zu
        migrieren, werden die alten Namen beim Lesen übersetzt: bestehende
        Gruppen behalten ihre Einstellung, und neue Daten sprechen nur noch das
        kanonische Vokabular.
        """
        gesetzt: set[str] = set()
        for teil in (raw or "").split(","):
            name = teil.strip()
            if not name:
                continue
            gesetzt.update(GROUP_PERMISSION_ALIASES.get(name, (name,)))
        return gesetzt

    @classmethod
    def effective_permissions(
        cls, member_permissions: str | None, group_default: str | None, role: str
    ) -> set[str]:
        """Was ein Mitglied tatsächlich darf — ohne Datenbank, ohne Seiteneffekt.

        Die eine Stelle, an der die Regel steht. ``has_group_permission``
        entscheidet damit den einzelnen Zugriff, ``list_user_groups`` rechnet
        damit die Marken für die Oberfläche aus. Ohne diese Funktion liefe
        beides auseinander, und genau dann zeigt die Oberfläche einen Knopf, den
        das Backend danach mit 403 beantwortet.

        ``member_permissions is None`` heißt „kein eigener Eintrag", und dann
        gilt die Vorgabe der Gruppe. Ein leerer String heißt dagegen
        „ausdrücklich nichts" und bleibt leer.

        Eigentümer und Administratoren bekommen die Anruf- und
        Moderationsrechte pauschal: sie verwalten die Rollen und könnten sie
        sich mit zwei Klicks selbst eintragen. Beim Beitreten ist das Absicht —
        wer einen Raum öffnen darf, darf ihn betreten, sonst bekäme der
        Startende einen Raum ohne Zutritt.
        """
        raw = member_permissions if member_permissions is not None else group_default
        gesetzt = cls.expand_group_permissions(raw)
        if role in ("owner", "admin"):
            gesetzt |= GROUP_CALL_PERMISSIONS
            gesetzt |= GROUP_MODERATION_PERMISSIONS
        return gesetzt

    @classmethod
    def has_group_permission(cls, db: Session, group_id: int, user_id: int, permission: str) -> bool:
        """Checks membership and effective group permission without mutating state."""
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        group_default: str | None = None
        if member.permissions is None:
            group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
            group_default = group.default_permissions if group else None
        return permission in cls.effective_permissions(
            member.permissions, group_default, member.role
        )

    @classmethod
    def assert_known_permissions(cls, raw: str | None) -> str | None:
        """Weist unbekannte Rechtenamen ab, statt sie stumm zu speichern.

        Ein Tippfehler in einer Rechteliste blieb bisher folgenlos sichtbar: der
        Haken stand im Dialog, geprüft wurde er nie. Genau so entstand die
        Lücke zwischen ``call_start`` und ``start_group_calls``.
        """
        if raw is None:
            return None
        namen = [teil.strip() for teil in raw.split(",") if teil.strip()]
        unbekannt = sorted(
            n for n in namen if n not in GROUP_PERMISSIONS and n not in GROUP_PERMISSION_ALIASES
        )
        if unbekannt:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannte Berechtigung: {', '.join(unbekannt)}",
            )
        # Kanonisch und ohne Dubletten zurückschreiben.
        return ",".join(sorted(cls.expand_group_permissions(raw)))

    @classmethod
    def assert_group_call_permission(
        cls, db: Session, group_id: int, user_id: int, permission: str
    ) -> ChatGroupMember:
        member = cls.get_group_member(db, group_id, user_id)
        if not member or not cls.has_group_permission(db, group_id, user_id, permission):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diesen Gruppenanruf.")
        return member

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

        default_by_group = {g.id: g.default_permissions for g in groups}

        members_by_group: dict[int, list[dict[str, Any]]] = {}
        for mem, uname, uavatar in all_members:
            # Einmal ausgerechnet, zweimal gebraucht: fuer die Rechteliste und
            # fuer die Marken darunter. ``effective_permissions`` ist dieselbe
            # Funktion, die ``has_group_permission`` befragt — kein zweiter
            # Regelsatz, der auseinanderlaufen koennte.
            wirksam = cls.effective_permissions(
                mem.permissions, default_by_group.get(mem.group_id), mem.role
            )
            members_by_group.setdefault(mem.group_id, []).append({
                "user_id": mem.user_id,
                "username": uname,
                "avatar_url": uavatar,
                "role": mem.role,
                # Kanonisch nach aussen, damit die Oberflaeche nur ein
                # Vokabular kennt. Was in der Spalte steht, kann noch alt sein.
                "permissions": (
                    ",".join(sorted(cls.expand_group_permissions(mem.permissions)))
                    if mem.permissions is not None
                    else None
                ),
                # Der Server kann den Inhalt einer Nachricht nicht lesen und
                # deshalb nicht pruefen, ob jemand ``@everyone`` geschrieben
                # oder eine Nachricht angeheftet hat. Das entscheidet der
                # **empfangende** Client — und er braucht dafuer die Rechtelage
                # des *Absenders*, nicht seine eigene. Darum steht die Marke
                # hier an jedem Mitglied und nicht nur an der Gruppe.
                "can_mention_everyone": "mention_everyone" in wirksam,
                "can_pin_messages": "pin_messages" in wirksam,
                "joined_at": mem.joined_at,
            })

        user_role_by_group = {m.group_id: m.role for m in memberships}

        results = []
        for g in groups:
            mems = members_by_group.get(g.id, [])
            offener_raum = GroupCallRoomRegistry.find_for_group(g.id)
            results.append({
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "avatar_url": g.avatar_url,
                "invite_code": g.invite_code,
                "owner_user_id": g.owner_user_id,
                "default_permissions": ",".join(
                    sorted(
                        cls.expand_group_permissions(
                            g.default_permissions or "send_messages,invite_members"
                        )
                    )
                ),
                "member_count": len(mems),
                "role": user_role_by_group.get(g.id, "member"),
                # Dieselbe Entscheidung, die der Anruf-Endpunkt trifft. Ohne sie
                # muesste das Frontend die Regel nachbauen und wuerde einen Knopf
                # zeigen, den das Backend danach mit 403 beantwortet.
                "can_start_call": cls.has_group_permission(
                    db, g.id, user_id, "start_group_calls"
                ),
                "can_join_call": cls.has_group_permission(
                    db, g.id, user_id, "join_group_calls"
                ),
                "can_share_screen": cls.has_group_permission(db, g.id, user_id, "share_screen"),
                "can_mute_others": cls.has_group_permission(db, g.id, user_id, "mute_in_calls"),
                "can_kick_from_call": cls.has_group_permission(
                    db, g.id, user_id, "kick_from_calls"
                ),
                # Ob **ich** den Knopf sehe. Die Schranke sitzt beim Empfaenger,
                # das hier ist nur die Bequemlichkeit: eine Auswahl anzubieten,
                # die beim Gegenueber folgenlos verpufft, waere irrefuehrend.
                "can_mention_everyone": cls.has_group_permission(
                    db, g.id, user_id, "mention_everyone"
                ),
                "can_pin_messages": cls.has_group_permission(db, g.id, user_id, "pin_messages"),
                "created_at": g.created_at,
                "members": mems,
                "room_token": offener_raum,
                "live_call": offener_raum is not None,
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
        target_mem.permissions = cls.assert_known_permissions(permissions) if permissions else None
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

        clean_perms = cls.assert_known_permissions(default_permissions) or ""
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

        stories = stories_query.order_by(ChatStory.created_at.desc()).all()

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
