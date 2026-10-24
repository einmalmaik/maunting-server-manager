from __future__ import annotations

from collections.abc import Iterable
from datetime import timezone
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    UserPresence,
)
from services.panel_settings_service import PanelSettingsService
from services.sync_event_service import SyncEventService
from services.achievement_service import AchievementService
from services.social.basis import _now

logger = logging.getLogger(__name__)


class FreundschaftenMixin:
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
    def is_confirmed_friend_of_any(
        cls, db: Session, user_id: int, candidate_ids: Iterable[int]
    ) -> bool:
        """Gibt es unter den Kandidaten mindestens einen bestätigten Freund?

        Dieselbe Frage wie ``is_confirmed_friend``, nur gegen eine Menge und in
        einer einzigen Abfrage. Das eigene Konto zählt nie mit: sonst wäre jeder
        Raum, in dem man selbst steht, automatisch freundschaftlich gedeckt.
        """
        ids = {int(kandidat) for kandidat in candidate_ids} - {user_id}
        if not ids:
            return False
        rel = (
            db.query(UserFriend.id)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_id, UserFriend.friend_id.in_(ids)),
                    and_(UserFriend.friend_id == user_id, UserFriend.user_id.in_(ids)),
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
        # Eigens gefragt: bei gegenseitiger Sperre liegen zwei Zeilen im
        # Bestand, und `.first()` sähe womöglich nur eine davon.
        if cls.is_blocked(db, user_id, target.id):
            raise HTTPException(status_code=400, detail="Aktion nicht möglich: Benutzer ist blockiert")
        if existing:
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
        req = db.query(UserFriend).filter_by(id=request_id, status="pending").first()
        if not req or (req.user_id != user_id and req.friend_id != user_id):
            raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")

        db.delete(req)
        db.commit()
        return True

    @classmethod
    def remove_friend(cls, db: Session, user_id: int, target_user_id: int) -> bool:
        """Entfernt eine bestehende Freundschaft (unterstützt Ziel-Benutzer-ID oder Beziehungs-ID)."""
        # Sperrzeilen bleiben außen vor: Sonst höbe der Blockierte die Sperre
        # mit einem einzigen "Freund entfernen" selbst auf. Aufheben kann sie
        # nur, wer sie gesetzt hat (`unblock_user`).
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
                ),
                UserFriend.status != "blocked",
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
            ),
            UserFriend.status != "blocked",
        ).delete(synchronize_session=False)
        db.commit()

        SyncEventService.publish(
            {"type": "friend_removed", "friend_id": user_id},
            user_id=other_id,
        )
        return True

    @classmethod
    def block_user(cls, db: Session, user_id: int, target_user_id: int) -> bool:
        """Blockiert einen Benutzer und löscht ggf. bestehende Freundschaft.

        Jede Sperre ist eine eigene Zeile ``(blockierender, blockierter)``.
        Eine Sperre der Gegenseite wird nie umgeschrieben: Sonst könnte der
        Blockierte sie erst an sich ziehen und dann per ``unblock_user``
        löschen. Sperren sich beide, liegen zwei Zeilen im Bestand.
        """
        if target_user_id == user_id:
            raise HTTPException(status_code=400, detail="Sie können sich nicht selbst blockieren")
        if not db.query(User.id).filter(User.id == target_user_id).first():
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        # Freundschaft und offene Anfragen fallen weg, in beiden Richtungen:
        # bliebe eine Zeile auf "accepted" stehen, wäre der Blockierte weiter
        # ein bestätigter Freund, und `social_privacy="friends"` gäbe ihm
        # weiter Einblick.
        db.query(UserFriend).filter(
            or_(
                and_(UserFriend.user_id == user_id, UserFriend.friend_id == target_user_id),
                and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == user_id),
            ),
            UserFriend.status != "blocked",
        ).delete(synchronize_session=False)
        eigene = (
            db.query(UserFriend)
            .filter_by(user_id=user_id, friend_id=target_user_id, status="blocked")
            .first()
        )
        if not eigene:
            db.add(
                UserFriend(
                    user_id=user_id,
                    friend_id=target_user_id,
                    status="blocked",
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
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
