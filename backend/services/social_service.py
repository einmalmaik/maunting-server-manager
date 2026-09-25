from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from fastapi import HTTPException
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import hashlib
import secrets
from models import (
    User,
    UserFriend,
    UserPresence,
    E2eeBlindEnvelope,
    E2eeBlindMailbox,
    ChatGroup,
    ChatGroupConfig,
    ChatGroupMember,
    ChatStory,
    DirectChat,
)
from models.chat_group import generate_invite_code
from services.panel_settings_service import PanelSettingsService
from services.sync_event_service import SyncEventService
from services.achievement_service import AchievementService
from services.notification_service import NotificationService
from services.call_room_service import GroupCallRoomRegistry
from services import webpush_service

logger = logging.getLogger(__name__)

#: Vorhaltefrist für E2EE-Umschläge auf dem Relais-Server (30 Tage).
#: Schützt vor Datenanhäufung bei Datenlecks und erzwingt das Zero-Knowledge-Prinzip.
E2EE_ENVELOPE_RETENTION_DAYS: int = 30

#: Wie viel ein Konto am Tag ins Relais schreiben darf, in KiB. Die Größe je
#: Umschlag begrenzt das Schema; ohne Tagesbudget füllte ein Konto die Platte
#: trotzdem, nur in vielen Nachrichten statt einer. 256 MiB am Tag sind weit
#: über dem, was Text, Steuerpakete und Schlüsselverteilung je erreichen.
E2EE_RELAY_KIB_PRO_TAG: int = 256 * 1024

#: Stories je Konto, die gleichzeitig laufen dürfen. Jede trägt bis zu 8 MB
#: Bild; ohne Grenze füllte ein Konto die Platte in 24 Stunden.
MAX_AKTIVE_STORIES: int = 20


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
        "set_disappearing_messages",
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

#: Rechte, die in die laufende Unterhaltung eingreifen: alle auf einmal wecken,
#: eine Nachricht über den Verlauf heften und die Frist stellen, nach der neue
#: Nachrichten für alle verschwinden. Dieselbe Begründung wie bei den
#: Anrufrechten — wer die Rollen verwaltet, kann sie sich ohnehin selbst
#: eintragen, und ein Eigentümer, der seine eigene Gruppe nicht erreichen darf,
#: wäre kein Schutz, sondern ein Rätsel.
#:
#: Durchgesetzt wird keines davon hier: die Handlung reist verschlüsselt, und
#: der Server liest sie nie. Er rechnet nur die Marke je Mitglied aus
#: (``list_user_groups``), gegen die das **empfangende** Gerät prüft.
GROUP_MODERATION_PERMISSIONS: frozenset[str] = frozenset(
    {
        "mention_everyone",
        "pin_messages",
        "set_disappearing_messages",
    }
)

#: Rechte, die nur an einer **Rolle** hängen dürfen, nie an den Standardrechten
#: für alle (@everyone).
#:
#: ``manage_roles`` ist das eine: wer Rollen verwalten darf, kann sich jedes
#: andere Recht selbst eintragen. Als Standard für alle gesetzt, ist das keine
#: Einstellung, sondern die Abschaffung der Rollen — jedes einfache Mitglied
#: wäre dann Administrator.
#:
#: Der Dialog bietet es unter den Standardrechten schon länger nicht an
#: (``NICHT_ALS_STANDARD`` in ``GroupPermissionsModal.tsx``). Das war aber nur
#: ein ausgeblendeter Haken: ein einziger PATCH auf
#: ``/groups/<id>/permissions`` mit ``manage_roles`` wurde bis 09/2026 klaglos
#: angenommen und gespeichert. Eine Regel, die nur die Oberfläche kennt, ist
#: keine Regel. Wer die Liste hier ändert, ändert sie auch dort.
GROUP_ROLE_ONLY_PERMISSIONS: frozenset[str] = frozenset({"manage_roles"})

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
    def gegenueber_aus_mailbox(
        cls, db: Session, user_id: int, blind_mailbox_id: str
    ) -> int | None:
        """Wer steht diesem Konto in dieser abgeleiteten Mailbox gegenüber?

        Seit Stufe 6b steht in `direct_chats` kein Mensch mehr. Wo der Server
        früher `user_a_id`/`user_b_id` nachschlug, rechnet er jetzt: für jedes
        aktive Konto wird die Kennung abgeleitet, die dieses Paar ergäbe, und
        mit der gesuchten verglichen.

        **Das ist keine neue Auskunft, sondern dieselbe in teurer.** Die alten
        Kennungen `msm:dm:<min>:<max>` und `msm-e2ee-box:<min>:<max>:` sind aus
        zwei kleinen Ganzzahlen nachrechenbar; wer die Datenbank hat, konnte
        sie immer schon zuordnen. Was mit den Spalten verschwindet, ist die
        **Liste**: „zeig mir alle Gespräche von Konto 42" beantwortet keine
        Abfrage mehr. Und eine Kennung aus einem Chatgeheimnis (Stufe 3d)
        taucht hier gar nicht erst auf — sie ist unableitbar, und genau dorthin
        zieht jedes Gespräch um, sobald beide Seiten das Geheimnis haben.

        `None`, wenn die Kennung zu keinem Paar mit diesem Konto passt.
        """
        clean = (blind_mailbox_id or "").strip()
        if not clean:
            return None
        for (kandidat,) in (
            db.query(User.id).filter(User.is_active == True, User.id != user_id).all()  # noqa: E712
        ):
            if (
                cls.derive_blind_mailbox_id(user_id, kandidat) == clean
                or cls.derive_legacy_direct_mailbox_id(user_id, kandidat) == clean
            ):
                return kandidat
        return None

    @classmethod
    def _direktchat_zeile(cls, db: Session, user_a_id: int, user_b_id: int) -> DirectChat | None:
        """Die Gesprächszeile zweier Konten — gefunden über ihre Kennung.

        Der Ersatz für `filter(user_a_id=..., user_b_id=...)`. Beide Ids liegen
        dem Aufrufer in diesem Moment ohnehin vor; er rechnet daraus die
        Kennung und sucht danach. Die Zeile selbst nennt niemanden mehr.
        """
        return (
            db.query(DirectChat)
            .filter_by(blind_mailbox_id=cls.derive_blind_mailbox_id(user_a_id, user_b_id))
            .first()
        )

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
        is_control: bool = False,
        lege_chat_an: bool = True,
    ) -> tuple[int | None, DirectChat | None]:
        """Ermittelt das Ziel einer blinden Mailbox für den Absender.

        Liefert `(target_recipient_id, direct_chat)` — oder `(None, None)`,
        wenn der Server nicht weiss, wer gemeint ist. Das ist kein Fehler
        mehr, sondern der Normalfall: die Kennung **ist** die Adresse, und
        zugestellt wird über das Abo. Wirft HTTPException nur bei einer
        Kennung, die der Server auflösen kann und die der Absender nicht
        benutzen darf.

        Zwei Dinge standen hier bis 09/2026 und sind bewusst weg:

        - **Ein genanntes `recipient_id`.** Es sprang an der Auflösung vorbei
          und beantwortete dem Server die eine Frage, die er nicht stellen
          können soll.
        - **Der Gruppen-Nachschlag.** Er lieferte für *jede* Gruppennachricht
          die vollständige Mitgliederliste — der Server zählte bei jeder Zeile
          auf, wer in dieser Gruppe ist. Gruppen laufen jetzt über das
          Mailbox-Abo, und der Server erfährt nur, dass irgendwo etwas liegt.

        Was bleibt, ist der kontogebundene Direktchat-Weg. Er trägt die
        Erstaufnahme: `ensure_direct_chat` ist die Stelle, an der
        `can_message_user` über Blockierung und Privatsphäre entscheidet, und
        die gibt es erst mit MLS kryptographisch. Er fällt in Stufe 6.

        `lege_chat_an=False` schlägt nur nach und schreibt nichts. Gedacht für
        das flüchtige Tippsignal: es darf keine Zeile in `direct_chats`
        entstehen lassen, sonst legt ein „tippt gerade" in einem Gespräch, aus
        dem nie eine Nachricht wird, dauerhaft ab, dass diese beiden
        miteinander zu tun hatten.
        """
        clean_mailbox = blind_mailbox_id.strip()
        cls.assert_social_enabled(db)
        user_device_box = cls.derive_user_device_mailbox_id(sender_user_id)

        if clean_mailbox == user_device_box:
            return sender_user_id, None

        if is_control:
            ziel = cls._steuerziel_geraetemailbox(db, sender_user_id, clean_mailbox)
            if ziel is not None:
                return ziel, None

        # Der Nachschlag in `direct_chats` stand hier bis Stufe 6b: eine Zeile
        # je Gespräch, mit beiden Konto-Ids darin. Er war ein Abkürzung über
        # die Suche darunter, keine eigene Fähigkeit — dieselbe Kennung findet
        # die Kandidatensuche auch, nur ohne dass jemand aufschreiben müsste,
        # wer mit wem schreibt.
        #
        # Kandidatensuche über aktive Benutzer (O(N)).
        candidates = db.query(User.id).filter(User.is_active == True, User.id != sender_user_id).all()
        for (cand_id,) in candidates:
            if (
                cls.derive_blind_mailbox_id(sender_user_id, cand_id) == clean_mailbox
                or cls.derive_legacy_direct_mailbox_id(sender_user_id, cand_id) == clean_mailbox
            ):
                if cls.is_blocked(db, sender_user_id, cand_id):
                    raise HTTPException(status_code=403, detail="Benutzer ist blockiert.")
                if not lege_chat_an:
                    return cand_id, None
                chat = cls.ensure_direct_chat(db, sender_user_id, cand_id)
                return cand_id, chat

        return None, None

    @classmethod
    def _steuerziel_geraetemailbox(
        cls, db: Session, sender_user_id: int, clean_mailbox: str
    ) -> int | None:
        """Die Geräte-Mailbox eines Kontos, mit dem der Absender schon zu tun hat.

        Der Weg, auf dem ein Schlüssel sein Ziel erreicht, ohne durch die
        Mailbox des Gesprächs zu laufen. Bis 09/2026 ging er durch sie hindurch,
        und das hatte zwei Folgen: die Mailbox liess sich nicht mit einem
        Besitznachweis verschliessen (der Schlüssel dafür lag dahinter), und 99
        von 100 Umschlägen im Lesefenster waren Schlüsselzustellungen.

        Eng gefasst, und jede Einschränkung hat ihren Grund:

        - **Nur Steuerumschläge.** Eine Nachricht gehört nie in eine fremde
          Geräte-Mailbox; sonst wäre dieser Weg ein Chat am Gespräch vorbei, den
          kein Verlauf und keine Blockierung je zu sehen bekäme. Der Server kann
          nicht hineinsehen, aber er kann darauf bestehen, dass der Umschlag als
          Steuerung deklariert ist — und kein Client zeigt Steuerung je als Text.
        - **Nur an bestehende Beziehungen.** Nicht an jedes Konto: die Kennung
          ist aus einer kleinen Ganzzahl nachrechenbar, und ohne diese Schranke
          könnte jeder jedem in die Geräte-Mailbox schreiben.

        **Drei Quellen, und die zweite und dritte kamen mit Stufe 3e dazu.**
        Vorher zählten allein gemeinsame Gruppen — passend, solange hier nur
        Gruppenschlüssel liefen. Seit auch das Chatgeheimnis eines Direktchats
        diesen Weg nimmt, war das zu eng: zwei Freunde ohne gemeinsame Gruppe
        bekamen ein 403, das Geheimnis erreichte die Gegenseite nie, und ihr
        Chat wäre für immer auf der abgeleiteten Kennung stehen geblieben — der
        einen, die der Server selbst ausrechnen kann.

        Bewusst **nicht** dabei: ein Fremder mit öffentlichem Profil. Der darf
        mir eine erste Nachricht schreiben, und genau dabei entsteht die
        Chatzeile, die ihn ab dann hier hereinlässt. Ihn schon vorher
        zuzulassen, hiesse, jedem Unbekannten das Lesefenster zu öffnen, das
        mein Gerät bei *jedem* Gespräch abholt.

        Was damit möglich bleibt: wer mit mir in einer Gruppe ist oder mit mir
        schreibt, kann mein Lesefenster mit Steuerumschlägen füllen. Das konnte
        er vorher auch — über die Mailbox, die ich ohnehin lese.

        Liefert die Konto-Id des Empfängers oder `None`, wenn die Kennung zu
        keiner erreichbaren Geräte-Mailbox gehört.
        """
        kandidaten: set[int] = set()

        gruppen = [
            gid
            for (gid,) in db.query(ChatGroupMember.group_id)
            .filter(ChatGroupMember.user_id == sender_user_id)
            .all()
        ]
        if gruppen:
            kandidaten.update(
                uid
                for (uid,) in db.query(ChatGroupMember.user_id)
                .filter(
                    ChatGroupMember.group_id.in_(gruppen),
                    ChatGroupMember.user_id != sender_user_id,
                )
                .all()
            )

        for freundschaft in (
            db.query(UserFriend)
            .filter(
                or_(
                    UserFriend.user_id == sender_user_id,
                    UserFriend.friend_id == sender_user_id,
                ),
                UserFriend.status == "accepted",
            )
            .all()
        ):
            kandidaten.add(
                freundschaft.friend_id
                if freundschaft.user_id == sender_user_id
                else freundschaft.user_id
            )

        for uid in kandidaten:
            if cls.derive_user_device_mailbox_id(uid) == clean_mailbox:
                # Eine Blockierung schneidet auch diesen Weg ab. Sonst wäre er
                # die Hintertür, durch die ein Blockierter weiter zustellt.
                if cls.is_blocked(db, sender_user_id, uid):
                    return None
                return uid

        # Die dritte Quelle, ohne dass jemand aufschreibt, wer mit wem schreibt.
        #
        # Bis Stufe 6b standen hier die Konto-Ids aus `direct_chats`. Jetzt wird
        # es andersherum gerechnet: erst das Konto bestimmen, zu dem diese
        # Geräte-Mailbox gehört, dann fragen, ob es zwischen ihm und dem
        # Absender eine Gesprächszeile gibt. Die Zeile wird über die Kennung
        # gefunden, die das Paar ergäbe — beide Ids liegen in diesem Moment vor,
        # und der Server erfährt nichts, was er nicht ohnehin in Händen hält.
        #
        # Der Unterschied zur alten Fassung ist keiner in der Antwort, sondern
        # in der Frage: „gehören diese beiden zusammen?" lässt sich beantworten,
        # „mit wem gehört Konto 42 zusammen?" nicht mehr.
        for (uid,) in (
            db.query(User.id)
            .filter(User.is_active == True, User.id != sender_user_id)  # noqa: E712
            .all()
        ):
            if cls.derive_user_device_mailbox_id(uid) != clean_mailbox:
                continue
            if cls.is_blocked(db, sender_user_id, uid):
                return None
            return uid if cls._direktchat_zeile(db, sender_user_id, uid) else None
        return None

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

        # Prüfe ob bereits ein DirectChat existiert (Antwort-Erlaubnis in beide
        # Richtungen). Gesucht wird über die Kennung, die dieses Paar ergibt —
        # seit Stufe 6b stehen in der Zeile keine Konto-Ids mehr.
        if cls._direktchat_zeile(db, sender_id, target_user_id):
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

        blind_mid = cls.derive_blind_mailbox_id(sender_id, target_user_id)
        chat = db.query(DirectChat).filter_by(blind_mailbox_id=blind_mid).first()
        if not chat:
            # Seit Stufe 6b ohne `user_a_id`, `user_b_id` und
            # `initiated_by_user_id`: die Zeile sagt „dieses Gespräch gibt es",
            # nicht „diese beiden führen es".
            chat = DirectChat(
                blind_mailbox_id=blind_mid,
                created_at=_now(),
                updated_at=_now(),
            )
            try:
                db.add(chat)
                db.commit()
                db.refresh(chat)
            except Exception:
                # Zwei gleichzeitige Erstnachrichten laufen in den eindeutigen
                # Riegel auf `blind_mailbox_id`. Der Verlierer nimmt die Zeile
                # des Gewinners.
                db.rollback()
                chat = db.query(DirectChat).filter_by(blind_mailbox_id=blind_mid).first()
                if not chat:
                    raise
        else:
            chat.updated_at = _now()
            db.commit()
        return chat

    # `list_direct_chats` gab es hier bis Stufe 6b.
    #
    # Sie beantwortete „mit wem schreibt Konto 42?" — genau die Frage, die der
    # Server nicht mehr beantworten koennen soll. Dafuer las sie
    # `direct_chats.user_a_id`/`user_b_id`, und solange sie existierte, mussten
    # diese Spalten existieren.
    #
    # Die Liste fuehrt jetzt der Client: `gespraechsListe.ts` haelt sie
    # versiegelt im oertlichen Speicher, gespeist aus `POST /chat/start` (dort
    # nennt der Aufrufer das Gegenueber ohnehin selbst) und aus dem, was in
    # seinen Mailboxen ankommt. Der Weg ist derselbe wie beim Gruppennamen in
    # Stufe 6a.
    #
    # Was dabei verloren geht, und das gehoert ausgesprochen: die Anwesenheit
    # eines Gespraechspartners, mit dem man nicht befreundet ist, wird nicht
    # mehr angezeigt. Sie kam aus dieser Abfrage. Wer sie sehen will, wird
    # Freund — und das ist ohnehin, was die Einstellung „nur Freunde" meint.

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
        u = db.query(User).filter_by(id=user_id).first()
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
        client_uuid: str | None = None,
        is_control: bool = False,
        control_type: str | None = None,
        mailbox_token: str | None = None,
        push_ausnahme: str | None = None,
    ) -> E2eeBlindEnvelope:

        """Speichert einen blinden E2EE-Umschlag mit serverseitiger Berechtigungsprüfung.

        sender_user_id wird im SSE-Event mitgeliefert, damit Outgoing Echo
        Prevention greift. Eine Empfängerkennung nimmt diese Stelle nicht mehr
        entgegen — was der Server über das Ziel weiss, rechnet er selbst aus
        oder er weiss es nicht.
        client_uuid garantiert Idempotenz bei Netzwerk-Schwankungen und Retries.

        `push_ausnahme` ist der SHA-256 der eigenen Push-Adresse des Absenders.
        Auf dem kontogebundenen Weg braucht es ihn nicht — dort erkennt
        `is_outgoing_echo` den Absender an seiner Kennung. Auf dem Mailbox-Weg
        gibt es keine Kennung mehr, an der man ihn erkennen könnte, und er
        bekäme sonst die Meldung über seine eigene Nachricht.
        """
        clean_mailbox = blind_mailbox_id.strip()
        clean_envelope = ciphertext_envelope.strip()
        clean_client_uuid = client_uuid.strip() if client_uuid and client_uuid.strip() else None

        from schemas.social import validate_e2ee_envelope_format
        try:
            validate_e2ee_envelope_format(clean_envelope)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        #: Bleibt `None`, wenn der Server zu dieser Mailbox kein Konto kennt.
        #: Dann läuft die Zustellung über das Abo — er weiss schlicht nicht,
        #: wer gemeint ist, und das ist der Normalfall.
        target_recipient_id: int | None = None

        if sender_user_id:
            target_recipient_id, _ = cls.resolve_mailbox_target(
                db,
                sender_user_id=sender_user_id,
                blind_mailbox_id=clean_mailbox,
                is_control=is_control,
            )
            if target_recipient_id is None:
                # Kein kontogebundenes Ziel. Früher war das immer ein 403, und
                # das musste es auch sein: jede Mailbox war aus kleinen
                # Ganzzahlen nachrechenbar, eine unbekannte Kennung also
                # entweder ein Tippfehler oder ein Versuch.
                #
                # Jetzt sind es zwei Fälle, und beide sind legitim:
                #
                # - Eine Kennung aus einem Geheimnis. Der Server kann sie
                #   niemandem zuordnen; der Besitznachweis ist dort nicht eine
                #   zusätzliche Schranke, sondern die einzige Berechtigung, die
                #   es überhaupt gibt.
                # - Eine Gruppen-Altkennung. Seit der Gruppen-Nachschlag weg
                #   ist, landet auch sie hier — und für sie gibt es keinen
                #   Nachweis, sondern nur die Mitgliedschaft.
                #
                # `hat_zugang` ist genau diese eine Tür: Nachweis **oder**
                # Teilnahme, und eine Mailbox mit hinterlegtem Nachweis öffnet
                # sich nicht mehr allein durch Mitgliedschaft. Der Unterschied
                # zum nachsichtigen `assert_mailbox_token` bleibt: eine
                # erfundene Kennung ohne beides ist weiter 403.
                if not cls.hat_zugang(db, sender_user_id, clean_mailbox, mailbox_token):
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

        # Erst hier gezählt: ein Retry mit derselben client_uuid ist oben schon
        # beantwortet und kostet kein zweites Mal.
        if sender_user_id:
            from limits import parse
            from middleware.rate_limit import limiter

            kib = len(clean_envelope) // 1024 + 1
            if not limiter.limiter.hit(
                parse(f"{E2EE_RELAY_KIB_PRO_TAG}/day"), f"e2ee-relay-kib:{sender_user_id}", cost=kib
            ):
                raise HTTPException(
                    status_code=429,
                    detail="Tageslimit für Nachrichten erreicht. Bitte morgen erneut versuchen.",
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
            # Nur, was der Server selbst herausgefunden hat. Auf dem
            # Mailbox-Weg steht hier `None`, und das ist die Wahrheit: er
            # weiss nicht, wer gemeint ist.
            "recipient_id": target_recipient_id,
            "is_control": is_control,
            "control_type": control_type,
        }

        if target_recipient_id and sender_user_id:
            SyncEventService.publish(msg_payload, user_id=target_recipient_id)
            if target_recipient_id != sender_user_id:
                SyncEventService.publish(msg_payload, user_id=sender_user_id)
                # Zwei getrennte Schritte, und das mit Absicht: der erste
                # entscheidet, ob überhaupt jemand etwas erfahren darf (Echo,
                # Steuersignal, offener Tab) und streicht jeden Inhalt aus dem
                # Payload. Der zweite stellt zu. Was `prepare_push_dispatch`
                # herausgibt, ist deshalb schon bereinigt — dort steht nie mehr
                # als „Neue Nachricht".
                #
                # `sende_an_konto` kehrt sofort zurück und schickt im
                # Hintergrund. Ein langsamer Push-Dienst darf den Absender hier
                # nicht warten lassen.
                nutzlast = NotificationService.prepare_push_dispatch(
                    target_user_id=target_recipient_id,
                    sender_user_id=sender_user_id,
                    title="Neue Nachricht",
                    is_e2ee=True,
                    is_control=is_control,
                    control_type=control_type,
                    has_active_foreground_connection=SyncEventService.has_active_subscribers(target_recipient_id),
                )
                if nutzlast:
                    webpush_service.sende_an_konto(db, target_recipient_id, nutzlast)
        else:
            # Kein kontogebundenes Ziel — die Mailbox **ist** die Adresse.
            #
            # Daneben stand bis 09/2026 ein eigener Gruppenzweig: der Server
            # schlug die Mitgliederliste nach und stellte Zeile für Zeile an
            # jedes Konto einzeln zu. Das war die teuerste Auskunft im ganzen
            # Messenger — bei **jeder** Nachricht zählte er auf, wer in dieser
            # Gruppe ist. Gruppen nehmen jetzt denselben Weg wie alles andere.
            #
            # Und hier selbst stand ein `publish(msg_payload)` ohne jede
            # Angabe, und das ist systemweit: jeder verbundene Client erfuhr,
            # dass in dieser Mailbox etwas liegt, samt Kennung und laufender
            # Nummer. Erreichbar war der Zweig nur ohne `sender_user_id`, also
            # selten — aber ausgerechnet in einem Messenger, der niemandem
            # verraten soll, wer wann mit wem spricht, war das die falsche
            # Voreinstellung.
            #
            # Zugestellt wird jetzt an die Abonnenten dieser Mailbox. Wer sie
            # nicht abonniert hat, erfährt nichts — und abonnieren darf nur,
            # wer teilnimmt oder den Besitznachweis hat.
            SyncEventService.publish(msg_payload, mailbox_id=clean_mailbox)

            # Und derselbe Weg für den geschlossenen Tab. `sende_an_mailbox`
            # fragt keine Konten ab — es gibt hier keines zu fragen, und genau
            # deshalb steht es hier: ein `sende_an_konto` an dieser Stelle wäre
            # die eine Zeile, die dem Server wieder verriete, wer Post in einer
            # Mailbox bekommt, die er sonst niemandem zuordnen kann.
            nutzlast = NotificationService.prepare_mailbox_push(
                is_control=is_control, control_type=control_type
            )
            if nutzlast:
                webpush_service.sende_an_mailbox(
                    db,
                    clean_mailbox,
                    nutzlast,
                    ausser_abdruck=push_ausnahme,
                )

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

        # Der Direktchat: bis Stufe 6b ein Nachschlag in `direct_chats`, jetzt
        # eine Rechnung. `gegenueber_aus_mailbox` leitet die Kennung für jedes
        # aktive Konto ab und vergleicht — dieselbe Antwort, ohne dass jemand
        # aufschreiben müsste, wer mit wem schreibt.
        #
        # Die Zeile wird zusätzlich verlangt: eine abgeleitete Kennung passt
        # rechnerisch zu jedem Paar, auch zu einem, das nie miteinander zu tun
        # hatte. Ohne sie wäre jede Konto-Kombination eine offene Tür.
        gegenueber = cls.gegenueber_aus_mailbox(db, user_id, clean)
        if gegenueber is not None and cls._direktchat_zeile(db, user_id, gegenueber):
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

    # ------------------------------------------------------------------
    # Blinder Besitznachweis (Stufe 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _verifier_von(auth_token: str) -> str:
        """Was der Server von einem Token behält: seinen SHA-256, sonst nichts."""
        return hashlib.sha256(auth_token.strip().lower().encode("utf-8")).hexdigest()

    @classmethod
    def _mailbox_ist_ableitbar(cls, db: Session, mailbox_id: str) -> bool:
        """Gehört diese Kennung zu einer Mailbox, die der Server selbst ausrechnen kann?

        Gemeint sind die Altkennungen: `sha256("msm:group:<id>")`,
        `sha256("msm:devices:<id>")` und die in `direct_chats` hinterlegten. Sie
        sind aus kleinen Ganzzahlen nachrechenbar — und genau deshalb darf sie
        nicht der Nächstbeste mit einem Besitznachweis belegen. Täte er es,
        wäre das eine Sperre gegen die echten Mitglieder, ohne je etwas
        entschlüsselt zu haben.

        Eine Kennung aus einem Gruppengeheimnis taucht hier nie auf. Sie ist
        unableitbar, und das ist der Unterschied, an dem diese Prüfung hängt.

        Der Preis ist ein Durchlauf über die Gruppen- und Konto-Ids. Er läuft
        nur beim Registrieren einer noch leeren, unbeanspruchten Mailbox —
        nicht im Sende- oder Lesepfad.
        """
        if db.query(DirectChat).filter_by(blind_mailbox_id=mailbox_id).first():
            return True
        for (gid,) in db.query(ChatGroup.id).all():
            if cls.derive_group_blind_mailbox_id(gid) == mailbox_id:
                return True
        for (uid,) in db.query(User.id).all():
            if cls.derive_user_device_mailbox_id(uid) == mailbox_id:
                return True
        return False

    @classmethod
    def register_blind_mailbox(
        cls, db: Session, user_id: int, mailbox_id: str, auth_token: str
    ) -> None:
        """Hinterlegt den Besitznachweis einer Mailbox — der authentifizierte Übergang.

        Drei Regeln, und jede schließt eine Tür, die sonst offen stünde:

        - **Ein hinterlegter Nachweis wird nie überschrieben.** Derselbe Token
          ist idempotent, ein anderer ist 409. Sonst wäre ein übernommenes
          Panel-Konto ein Generalschlüssel für jede Mailbox — dieselbe Lehre
          wie beim Tresor.
        - **Eine Mailbox mit Umschlägen gehört nie dem Nächstbesten.** Wer für
          sie registrieren will, muss nach den heutigen Regeln dazugehören.
          Das ist der Weg für den Bestand: eine gewachsene Gruppe bekommt
          ihren Nachweis von einem echten Mitglied.
        - **Eine ableitbare Kennung trägt nie einen Nachweis**, auch nicht von
          einem Mitglied. Dort entscheidet die Teilnahme, und der Client
          registriert sie nie (`sichereBesitznachweis` in
          `gruppenSchluessel.ts`). Ein Nachweis dort sperrte nur die übrigen
          Teilnehmer aus.

          Was der Server hier nicht prüfen kann, sind Kennungen von Konten,
          Gruppen und Paaren, die es noch nicht gibt: `msm:devices:<n+1>` ist
          heute ausrechenbar, gehört aber noch niemandem. Belegt sie jemand,
          fällt der Nachweis in `assert_mailbox_token`, sobald ein echtes
          Konto die Kennung ohne ihn anfasst.

        Was übrig bleibt, ist der Normalfall der Stufe 3: eine unableitbare,
        leere Kennung aus einem frisch erzeugten Gruppengeheimnis. Die darf,
        wer angemeldet ist — dem Server ist sie nicht zuzuordnen, und sie
        enthält nichts, was jemandem gehören könnte.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean:
            raise HTTPException(status_code=400, detail="Mailbox-ID fehlt.")
        verifier = cls._verifier_von(auth_token)

        vorhanden = db.get(E2eeBlindMailbox, clean)
        if vorhanden is not None:
            if secrets.compare_digest(vorhanden.auth_verifier, verifier):
                return
            raise HTTPException(
                status_code=409, detail="Für diese Mailbox ist bereits ein Besitznachweis hinterlegt."
            )

        if cls._ist_ableitbar_fuer(db, user_id, clean):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")
        hat_umschlaege = (
            db.query(E2eeBlindEnvelope.id).filter(E2eeBlindEnvelope.blind_mailbox_id == clean).first()
            is not None
        )
        if hat_umschlaege:
            cls.assert_mailbox_participant(db, user_id, clean)

        db.add(
            E2eeBlindMailbox(
                mailbox_id=clean,
                auth_verifier=verifier,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Zwei Geräte haben gleichzeitig registriert. Derselbe Token ist
            # kein Fehler, ein anderer schon.
            db.rollback()
            nun = db.get(E2eeBlindMailbox, clean)
            if nun is None or not secrets.compare_digest(nun.auth_verifier, verifier):
                raise HTTPException(
                    status_code=409,
                    detail="Für diese Mailbox ist bereits ein Besitznachweis hinterlegt.",
                )

    @classmethod
    def _ist_ableitbar_fuer(cls, db: Session, user_id: int, mailbox_id: str) -> bool:
        """Kann der Server diese Kennung ausrechnen, aus Sicht dieses Kontos?

        `_mailbox_ist_ableitbar` kennt Geräte-, Gruppen- und hinterlegte
        Direktchat-Kennungen. Dazu kommt das Paar aus diesem Konto und jedem
        anderen: ein Erstkontakt hat noch keine Zeile in `direct_chats`.
        """
        return (
            cls._mailbox_ist_ableitbar(db, mailbox_id)
            or cls.gegenueber_aus_mailbox(db, user_id, mailbox_id) is not None
        )

    @classmethod
    def assert_mailbox_token(
        cls, db: Session, mailbox_id: str, auth_token: str | None, user_id: int | None = None
    ) -> None:
        """Prüft den Besitznachweis, falls für diese Mailbox einer hinterlegt ist.

        Ohne hinterlegten Nachweis tut diese Prüfung nichts — der Bestand läuft
        weiter wie bisher und wird von `assert_mailbox_participant` gehalten.
        Ist einer hinterlegt, gilt er: ein fehlendes oder falsches Token ist
        403, und zwar bevor irgendetwas gelesen, geschrieben oder gelöscht
        wird.

        Verglichen wird mit `compare_digest`. Ein Vergleich, der beim ersten
        abweichenden Zeichen abbricht, verrät über viele Versuche den Verifier
        — und der ist hier das Einzige, was es zu erraten gibt.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean:
            return
        eintrag = db.get(E2eeBlindMailbox, clean)
        if eintrag is None:
            return
        if auth_token and secrets.compare_digest(eintrag.auth_verifier, cls._verifier_von(auth_token)):
            return
        # Ein Nachweis auf einer ausrechenbaren Kennung ist angemaßt: der
        # Client registriert dort nie (siehe `register_blind_mailbox`). Belegt
        # hat sie jemand, als es Konto, Gruppe oder Paar noch nicht gab, um
        # die späteren Teilnehmer auszusperren. Er fällt hier weg; ob der
        # Anfragende hinein darf, prüft danach wie immer die Teilnahme.
        if user_id is not None and cls._ist_ableitbar_fuer(db, user_id, clean):
            logger.warning("Angemaßten Besitznachweis auf einer ableitbaren Mailbox entfernt.")
            db.delete(eintrag)
            db.commit()
            return
        raise HTTPException(status_code=403, detail="Kein gültiger Besitznachweis für diese Mailbox.")

    @classmethod
    def hat_gueltigen_nachweis(
        cls, db: Session, mailbox_id: str, auth_token: str | None
    ) -> bool:
        """Liegt für diese Mailbox ein Nachweis vor, und stimmt das Token?

        Der Unterschied zu `assert_mailbox_token` ist der Fall „kein Nachweis
        hinterlegt": dort ist er ein stilles Ja (der Bestand läuft weiter),
        hier ein klares Nein. Denn wer hiermit fragt, will keine Schranke
        prüfen — er will wissen, ob der Nachweis eine **Eintrittskarte** ist.

        Gebraucht wird das für Mailboxen, die der Server nicht ausrechnen
        kann: dort gibt es keine Mitgliedschaft nachzuschlagen, und dann ist
        der Nachweis die einzige Berechtigung, die es überhaupt gibt. Ein
        stilles Ja wäre dort ein offenes Tor für jede erfundene Kennung.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean or not auth_token:
            return False
        eintrag = db.get(E2eeBlindMailbox, clean)
        if eintrag is None:
            return False
        return secrets.compare_digest(eintrag.auth_verifier, cls._verifier_von(auth_token))

    @classmethod
    def hat_zugang(cls, db: Session, user_id: int, mailbox_id: str, token: str | None) -> bool:
        """Darf dieses Konto diese eine Mailbox öffnen?

        **Die eine Tür.** Es gibt zwei Arten hineinzukommen, und beide stehen
        nur hier: das Konto gehört zur Mailbox (der Bestand), oder es legt den
        Besitznachweis vor (das Neue). Jede Route, die eine einzelne Mailbox
        aufschließt, fragt hier — und `erlaubte_mailboxen` fragt für Listen
        dasselbe.

        Der zweite Weg ist der Grund, warum Stufe 3 überhaupt funktionieren
        kann: sobald die Kennung aus einem Gruppengeheimnis fällt, **kann** der
        Server die Mitgliedschaft nicht mehr nachschlagen. Er weiß nicht, wem
        die Mailbox gehört, und soll es nicht wissen. Bliebe es beim
        Teilnehmerschloss allein, wäre die neue Kennung für jeden verschlossen,
        auch für ihre Besitzer.

        Der erste Weg bleibt, solange es ableitbare Kennungen gibt. Er ist
        zugleich die Schranke, die ein hinausgeworfenes Mitglied hält: das
        Gruppengeheimnis kennt es noch, den Nachweis kann es sich ausrechnen —
        erst MLS trägt die Zugehörigkeit kryptographisch.
        """
        mid = (mailbox_id or "").strip()
        if not mid:
            return False
        if cls.hat_gueltigen_nachweis(db, mid, token):
            return True
        try:
            cls.assert_mailbox_participant(db, user_id, mid)
            # Eine Mailbox mit hinterlegtem Nachweis öffnet sich nicht allein
            # durch Mitgliedschaft — sonst wäre der eine Weg die Hintertür
            # neben der verschlossenen Vordertür des anderen.
            cls.assert_mailbox_token(db, mid, token, user_id)
        except HTTPException:
            return False
        return True

    @classmethod
    def assert_mailbox_zugang(
        cls, db: Session, user_id: int, mailbox_id: str, token: str | None
    ) -> None:
        """`hat_zugang`, aber mit 403 statt `False`.

        **Eine Antwort für jeden Grund.** 403 ist, was
        `assert_mailbox_participant` schon immer für einen Fremden gab; dass
        auch der fehlende und der falsche Nachweis so antworten, ist der
        Zugewinn: vorher unterschied sich „kein Mitglied" (403 aus der
        Teilnehmerprüfung) nicht von „verschlossen" (403 aus der
        Nachweisprüfung) nur zufällig — jetzt kann es sich gar nicht mehr
        unterscheiden. Aus der Antwort ist nicht zu lesen, ob es diese Mailbox
        gibt, ob sie einen Nachweis trägt oder wer dazugehört.
        """
        if not cls.hat_zugang(db, user_id, mailbox_id, token):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

    @classmethod
    def erlaubte_mailboxen(
        cls, db: Session, user_id: int, eintraege: Iterable[tuple[str, str | None]]
    ) -> list[str]:
        """Filtert eine Wunschliste von Mailboxen auf die erlaubten.

        Die Tür, durch die ein Client sagt „darüber will ich Bescheid wissen".
        Es gibt zwei davon in den Routern — den Echtzeitstrom und die
        Push-Adresse —, und sie müssen dieselbe Prüfung haben. Stünde sie
        zweimal da, wäre die zweite Fassung irgendwann die nachsichtigere, und
        Push wäre der Weg, über den man erfährt, was der Strom einem nicht sagt.

        Geprüft wird je Kennung mit `hat_zugang` — dieselbe Tür, durch die auch
        eine einzelne Mailbox aufgeht.

        **Was durchfällt, fällt still durch.** Die Antwort nennt nur, was
        erlaubt ist, nie warum etwas fehlt — eine einzelne Ablehnung wäre eine
        Auskunft darüber, welche Mailboxen es gibt.
        """
        erlaubt: list[str] = []
        for kennung, token in eintraege:
            mid = (kennung or "").strip()
            if mid and cls.hat_zugang(db, user_id, mid, token):
                erlaubt.append(mid)
        return erlaubt

    @classmethod
    def delete_blind_envelopes(
        cls,
        db: Session,
        blind_mailbox_id: str,
        client_uuid: str,
        user_id: int,
        mailbox_token: str | None = None,
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

        cls.assert_mailbox_zugang(db, user_id, clean_mailbox, mailbox_token)

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
    def cleanup_expired_envelopes(
        cls, db: Session, days: int = E2EE_ENVELOPE_RETENTION_DAYS
    ) -> int:
        """Löscht blinde Umschläge, die älter als `days` Tage sind (Default: 30 Tage).

        Zero-Knowledge / Vorhaltefrist-Garantie:
        Der Relais-Server speichert Umschläge maximal 30 Tage.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = (
            db.query(E2eeBlindEnvelope)
            .filter(E2eeBlindEnvelope.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        if deleted > 0:
            db.commit()
            logger.info(
                "E2EE-Vorhaltefrist: %d abgelaufene Umschläge (> %d Tage) bereinigt.",
                deleted,
                days,
            )
        return deleted

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
    def get_blind_envelopes(
        cls, db: Session, blind_mailbox_id: str, since_id: int = 0, limit: int = 50
    ) -> list[E2eeBlindEnvelope]:
        """Holt blinde Umschläge aus einer Mailbox ab (maximal 30 Tage alt)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=E2EE_ENVELOPE_RETENTION_DAYS)
        query = db.query(E2eeBlindEnvelope).filter(
            E2eeBlindEnvelope.blind_mailbox_id == blind_mailbox_id,
            E2eeBlindEnvelope.created_at >= cutoff,
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
        nachweislich beteiligt ist (ChatGroupMember oder bestätigter UserFriend).

        **Die Direktchats fehlen hier seit Stufe 6b.** Sie kamen aus
        `direct_chats.user_a_id`/`user_b_id` — der Abfrage „mit wem schreibt
        Konto 42?", die es nicht mehr geben soll. Was bleibt, sind die
        Gruppen, die Freunde und die eigene Geräte-Mailbox; alles andere
        abonniert der Client selbst über `erlaubte_mailboxen`, wo er seine
        Kennungen nennt und der Server nur prüft, statt sie aufzuzählen.
        """
        cls.assert_social_enabled(db)
        uid = current_user.id
        effective_since = max(0, since_id) if since_id is not None else 0

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
        all_mids = list(set(group_mids) | set(friend_mids) | {device_mid})
        if not all_mids:
            return []

        # 4. Aggregiere Mailbox-Statistiken über E2eeBlindEnvelope in Batches à 500
        cutoff = datetime.now(timezone.utc) - timedelta(days=E2EE_ENVELOPE_RETENTION_DAYS)
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
                    E2eeBlindEnvelope.created_at >= cutoff,
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
    ) -> None:
        """Verteilt ein flüchtiges Tipp- oder Sprachaufnahme-Signal ohne Speicherung.

        Nimmt keine Empfängerkennung mehr entgegen. Bis 09/2026 war ein
        genanntes `recipient_id` der ganze Weg: es sprang an der
        Mailbox-Auflösung vorbei, und die einzige verbleibende Prüfung war die
        Blockierung. Jedes angemeldete Konto konnte damit jedem anderen
        „tippt gerade" schicken, mit einer frei erfundenen Kennung. Wer das
        Signal bekommt, entscheidet jetzt allein die Mailbox.
        """
        clean_mailbox = blind_mailbox_id.strip()

        target_recipient_id: int | None = None
        if db:
            try:
                target_recipient_id, _ = cls.resolve_mailbox_target(
                    db,
                    sender_user_id=sender_id,
                    blind_mailbox_id=clean_mailbox,
                    lege_chat_an=False,
                )
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
        else:
            # An die Abonnenten dieser Mailbox, an niemanden sonst.
            #
            # Hier stand ein `publish(payload)` ohne Angabe — systemweit. Und
            # anders als im Relais war dieser Zweig von aussen erreichbar:
            # `resolve_mailbox_target` kehrt bei einer Kennung, die zu nichts
            # gehört, ohne Ausnahme zurück (`None, [], None, None`), und
            # `broadcast_typing_signal` fängt nur `HTTPException`. Ein
            # Tippsignal auf eine erfundene Kennung ging damit an **jeden**
            # verbundenen Client — samt `sender_id` und `sender_username`.
            #
            # Das ist genau die Präsenzauskunft, die der Messenger sonst
            # sorgfältig auf die Gegenseite beschränkt.
            SyncEventService.publish(payload, mailbox_id=clean_mailbox)

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
    def darf_einladen(cls, db: Session, group_id: int, user_id: int) -> bool:
        """Ob dieses Mitglied neue Leute in die Gruppe holen darf.

        Steht hier und nicht in ``effective_permissions``, weil Eigentümer und
        Administratoren ``invite_members`` nicht automatisch tragen — die
        Vorgabe erweitert für sie nur die Anruf- und Moderationsrechte. Ein
        Eigentümer, der niemanden in seine eigene Gruppe holen darf, wäre kein
        Schutz, sondern ein Rätsel.

        Der Aufrufer entscheidet damit, ob er den **Einladungscode** überhaupt
        zu sehen bekommt. Bis 09/2026 ging er an jedes Mitglied heraus, bei
        jedem Abruf der Gruppenliste: ``invite_members`` war damit nicht nur
        ungeprüft, es war strukturell unprüfbar. Wer den Code hat, kommt rein —
        also ist ihn nicht zu bekommen die einzige Schranke, die es geben kann.
        """
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        if member.role in ("owner", "admin"):
            return True
        return cls.has_group_permission(db, group_id, user_id, "invite_members")

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
    def create_group(cls, db: Session, user: User) -> ChatGroup:
        """Legt eine Gruppe an — ohne Namen.

        Bis Stufe 6 nahm diese Stelle einen Namen entgegen, prüfte seine Länge
        und legte ihn ab. Sie tut nichts davon mehr: Name, Beschreibung und
        Logo liegen im verschlüsselten Gruppenblock und in der
        Einladungskarte, und dieser Server kennt sie nicht.

        Was bleibt, ist das Gerüst, das er tragen muss und nur er tragen kann:
        eine Kennung, ein Eigentümer, ein Einladungscode, eine erste
        Mitgliedschaft.
        """
        cls.assert_social_enabled(db)

        import secrets
        invite_code = secrets.token_urlsafe(16)

        group = ChatGroup(
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
                # deshalb nicht pruefen, ob jemand ``@everyone`` geschrieben,
                # eine Nachricht angeheftet oder die Verfallsfrist gestellt
                # hat. Das entscheidet der **empfangende** Client — und er
                # braucht dafuer die Rechtelage des *Absenders*, nicht seine
                # eigene. Darum steht die Marke hier an jedem Mitglied und
                # nicht nur an der Gruppe.
                "can_mention_everyone": "mention_everyone" in wirksam,
                "can_pin_messages": "pin_messages" in wirksam,
                "can_set_disappearing_messages": "set_disappearing_messages" in wirksam,
                "joined_at": mem.joined_at,
            })

        user_role_by_group = {m.group_id: m.role for m in memberships}

        results = []
        for g in groups:
            mems = members_by_group.get(g.id, [])
            offener_raum = GroupCallRoomRegistry.find_for_group(g.id)
            results.append({
                "id": g.id,
                # Seit Stufe 6 leer, und zwar an der Quelle: die Spalten sind
                # geräumt, und niemand schreibt mehr hinein. Der Client setzt
                # Name, Beschreibung und Logo aus dem verschlüsselten Block.
                "name": None,
                "description": None,
                "avatar_url": None,
                # Nur für die, die einladen dürfen. Der Code ist ein Geheimnis,
                # das Zugang gewährt — er hat in der Antwort an ein Mitglied
                # ohne dieses Recht nichts verloren.
                "invite_code": (
                    g.invite_code if cls.darf_einladen(db, g.id, user_id) else None
                ),
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
                "can_set_disappearing_messages": cls.has_group_permission(
                    db, g.id, user_id, "set_disappearing_messages"
                ),
                "created_at": g.created_at,
                "members": mems,
                "room_token": offener_raum,
                "live_call": offener_raum is not None,
            })

        # Nach Alter, nicht nach Namen: seit 09/2026 kennt der Server keinen
        # Namen mehr, nach dem er sortieren koennte. Die alphabetische Ordnung
        # macht der Client, sobald er die Namen entschluesselt hat -- dort
        # liegt das Wissen, und nur dort kann sie stimmen. `created_at` ist
        # ohnehin Teil der Antwort und verraet damit nichts Neues.
        return sorted(results, key=lambda x: (x["created_at"], x["id"]))

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
        cls.trage_mitglied_aus(db, group_id, user.id)
        db.commit()

    @classmethod
    def trage_mitglied_aus(cls, db: Session, group_id: int, user_id: int) -> None:
        """Nimmt jemanden aus einer Gruppe, ohne zu committen.

        Bleibt niemand übrig, fällt die Gruppe weg. Geht der Eigentümer, erbt
        ein Admin, sonst das am längsten beigetretene Mitglied. Auch die
        Kontolöschung läuft hier durch: `chat_groups.owner_user_id` kaskadiert,
        ohne Nachfolger nähme das Löschen eines Eigentümers seine Gruppen
        allen anderen Mitgliedern weg.
        """
        group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
        if not group:
            return
        member = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == user_id)
            .first()
        )
        was_owner = group.owner_user_id == user_id or (member is not None and member.role == "owner")
        if member:
            db.delete(member)
            db.flush()

        remaining = (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id)
            .order_by(
                (ChatGroupMember.role == "admin").desc(),
                ChatGroupMember.joined_at,
                ChatGroupMember.id,
            )
            .all()
        )
        if not remaining:
            db.delete(group)
        elif was_owner:
            remaining[0].role = "owner"
            group.owner_user_id = remaining[0].user_id
        db.flush()

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
    ) -> ChatGroup:
        """Wirft ein Mitglied hinaus und erneuert dabei den Einladungscode.

        Wer hinausgeworfen wird, kennt den Code oft: er hatte das
        Einladungsrecht, oder der Link stand im Verlauf. Bis 09/2026 blieb der
        Code gültig, und der Rauswurf war eine Formalie — ein Klick auf den
        alten Link, und er war wieder drin.

        Die Einladungskarte geht mit. Ihre gebundenen Daten nennen den alten
        Code; gegen den neuen öffnet sie sich nicht mehr. Wer als Nächstes
        einen Link teilt, hinterlegt eine neue.
        """
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

        group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")

        db.delete(target_mem)
        # Im selben Commit: ein Rauswurf ohne neuen Code wäre genau der Zustand,
        # den diese Stelle beenden soll.
        group.invite_code = generate_invite_code()
        group.invite_card = None
        db.commit()
        db.refresh(group)

        SyncEventService.publish(
            {
                "type": "chat_group_member_kicked",
                "group_id": group_id,
                "user_id": target_user_id,
            },
            user_id=target_user_id,
        )
        return group

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
        # Nach dem Auflösen der Aliase prüfen, nicht davor: sonst käme ein
        # künftiger Aliasname auf ein nur-Rollen-Recht hier ungesehen durch.
        nur_rollen = sorted(
            set(p for p in clean_perms.split(",") if p) & GROUP_ROLE_ONLY_PERMISSIONS
        )
        if nur_rollen:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Diese Berechtigung gehört an eine Rolle, nicht an die "
                    f"Standardrechte aller Mitglieder: {', '.join(nur_rollen)}"
                ),
            )
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

    # --- Gruppenzustand: verschlüsselt, mit Revision ---

    @classmethod
    def darf_gruppenzustand_schreiben(cls, db: Session, group_id: int, user_id: int) -> bool:
        """Eigentümer, Administratoren und wer ``manage_roles`` trägt.

        ``manage_roles`` steht bewusst **nicht** in ``GROUP_MODERATION_PERMISSIONS``
        und fällt Eigentümern deshalb nicht automatisch zu — hier wird es
        deswegen ausdrücklich neben der Rolle geprüft.
        """
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        if member.role in ("owner", "admin"):
            return True
        return cls.has_group_permission(db, group_id, user_id, "manage_roles")

    @classmethod
    def get_group_config(cls, db: Session, group_id: int, caller: User) -> ChatGroupConfig | None:
        """Liest den verschlüsselten Zustand. Jedes Mitglied darf das.

        Lesen heißt hier nur: den Block bekommen. Öffnen kann ihn allein, wer
        den Gruppenschlüssel hat — und wer den hat, ist Mitglied. Eine engere
        Schranke am Server wäre Schein: sie würde eine Zusage vortäuschen, die
        nicht der Server, sondern die Verschlüsselung trägt.

        Für ein Nichtmitglied ist die Antwort 404 und nicht 403: ein 403 wäre
        die Auskunft „diese Gruppe gibt es".
        """
        cls.assert_social_enabled(db)
        if not cls.get_group_member(db, group_id, caller.id):
            raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
        return (
            db.query(ChatGroupConfig).filter(ChatGroupConfig.group_id == group_id).first()
        )

    @classmethod
    def write_group_config(
        cls,
        db: Session,
        group_id: int,
        blob: str,
        erwartete_revision: int,
        caller: User,
    ) -> ChatGroupConfig:
        """Schreibt den nächsten Stand — genau dann, wenn niemand dazwischenkam.

        Der Server liest den Block nie. Was er prüft, sind zwei Zahlen und eine
        Mitgliedschaft:

        * Die Revision muss **genau eins weiter** sein als der gespeicherte
          Stand. Das ist der Rückspielschutz aus dem Passwort-Tresor: ohne ihn
          könnte jemand mit Schreibzugang einen alten Block zurücklegen und
          damit einen Rechteentzug rückgängig machen, ohne je etwas
          entschlüsselt zu haben.
        * Der Aufrufer muss Mitglied sein und verwalten dürfen. Diese Prüfung
          ist das **zweite** Schloss, nicht das erste — das erste ist der
          Gruppenschlüssel, ohne den niemand einen gültigen Block herstellt.
          Sie stützt sich auf ``chat_group_members``, also auf Klartext-Metadaten,
          und fällt mit Stufe 6 weg. Bis dahin kostet sie nichts und deckt den
          Fall ab, dass ein ausgeschiedenes Mitglied einen alten Schlüssel
          behalten hat.

        Wer schreiben *durfte*, steht in der Unterschrift **innerhalb** des
        Blocks; die prüfen die Mitglieder nach dem Entschlüsseln. Der Server
        kann das nicht und soll es nicht können.
        """
        cls.assert_social_enabled(db)
        if not cls.get_group_member(db, group_id, caller.id):
            raise HTTPException(status_code=404, detail="Gruppe nicht gefunden.")
        if not cls.darf_gruppenzustand_schreiben(db, group_id, caller.id):
            raise HTTPException(
                status_code=403, detail="Keine Berechtigung, die Rollen dieser Gruppe zu ändern."
            )

        jetzt = _now()
        if erwartete_revision == 0:
            # Erster Zustand. Zwei Geräte, die gleichzeitig gründen, laufen in
            # die Primärschlüssel-Kollision — die fangen wir ab und melden sie
            # als Konflikt, nicht als Serverfehler.
            eintrag = ChatGroupConfig(
                group_id=group_id, blob=blob, revision=1, updated_at=jetzt
            )
            db.add(eintrag)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise cls._gruppenzustand_konflikt(db, group_id)
            db.refresh(eintrag)
        else:
            # Bedingtes UPDATE statt „lesen, prüfen, schreiben": zwischen dem
            # Lesen und dem Schreiben passt sonst ein zweiter Schreiber, und
            # beide meldeten Erfolg für dieselbe Revision.
            betroffen = (
                db.query(ChatGroupConfig)
                .filter(
                    ChatGroupConfig.group_id == group_id,
                    ChatGroupConfig.revision == erwartete_revision,
                )
                .update(
                    {
                        ChatGroupConfig.blob: blob,
                        ChatGroupConfig.revision: erwartete_revision + 1,
                        ChatGroupConfig.updated_at: jetzt,
                    },
                    synchronize_session=False,
                )
            )
            if betroffen == 0:
                db.rollback()
                raise cls._gruppenzustand_konflikt(db, group_id)
            db.commit()
            eintrag = (
                db.query(ChatGroupConfig)
                .filter(ChatGroupConfig.group_id == group_id)
                .first()
            )

        # Die Mitglieder erfahren, dass es etwas Neues gibt — nicht was.
        for (mitglied_id,) in (
            db.query(ChatGroupMember.user_id)
            .filter(ChatGroupMember.group_id == group_id)
            .all()
        ):
            SyncEventService.publish(
                {
                    "type": "chat_group_config_updated",
                    "group_id": group_id,
                    "revision": eintrag.revision,
                },
                user_id=mitglied_id,
            )
        return eintrag

    @classmethod
    def _gruppenzustand_konflikt(cls, db: Session, group_id: int) -> HTTPException:
        """409 mit dem Stand, den der Client als nächstes lesen muss."""
        aktuell = (
            db.query(ChatGroupConfig).filter(ChatGroupConfig.group_id == group_id).first()
        )
        return HTTPException(
            status_code=409,
            detail={
                "grund": "revision_veraltet",
                "aktuelle_revision": aktuell.revision if aktuell else 0,
                "nachricht": (
                    "Der Gruppenzustand wurde inzwischen von einem anderen Gerät "
                    "geändert. Bitte neu laden."
                ),
            },
        )

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

        aktive = (
            db.query(func.count(ChatStory.id))
            .filter(ChatStory.user_id == user.id, ChatStory.expires_at > _now())
            .scalar()
        )
        if aktive >= MAX_AKTIVE_STORIES:
            raise HTTPException(
                status_code=429,
                detail=f"Mehr als {MAX_AKTIVE_STORIES} Stories gleichzeitig gehen nicht. Ältere laufen nach 24 Stunden ab.",
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
