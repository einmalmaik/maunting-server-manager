"""Service fuer sichere Handhabung von Chat-Medien und E2EE-Dateianhaengen.

Enforces:
- Client-seitige E2EE-Verschluesselung (Server sieht nur verschluesselte Blobs)
- Chat-Mitgliedschafts-Pruefung fuer Medienzugriffe
- Zeitlich limitierte, kryptographisch signierte Download-URLs (CDN/Signed-URLs)
- Expiration und Token-Validierung
- Schutz vor unberechtigtem Fremdzugriff
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import uuid
from typing import Any
from fastapi import HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from config import settings
from models import (
    ChatGroup,
    ChatGroupMember,
    ChatMedia,
    DirectChat,
    User,
    UserFriend,
)
from services.chat_media_validator import (
    MAX_MEDIA_BYTES,
    validate_encrypted_blob_payload,
    sanitize_attachment_filename,
)
from services.social_service import SocialService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _signing_secret() -> str:
    """Holt das Signiergeheimnis aus den Einstellungen oder generiert einen stabilen Key."""
    return getattr(settings, "secret_key", None) or "msm-chat-media-secure-signed-secret"


class ChatMediaService:
    """Zentrale Geschaeftslogik fuer Chat-Medienanhaenge und signierte URLs."""

    @classmethod
    def assert_chat_membership(cls, db: Session, user_id: int, media: ChatMedia) -> None:
        """Prueft strikt, ob der angefragte Benutzer Mitglied des Chats ist.
        
        Fremde (Nicht-Mitglieder) erhalten keine Berechtigung und duerfen
        weder signierte URLs erstellen noch Medien herunterladen.
        """
        # 1. Gruppenchat
        if media.group_id is not None:
            group = db.query(ChatGroup).filter(ChatGroup.id == media.group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Chat-Gruppe existiert nicht mehr.")
            is_member = (
                db.query(ChatGroupMember)
                .filter(
                    ChatGroupMember.group_id == media.group_id,
                    ChatGroupMember.user_id == user_id,
                )
                .first()
            ) is not None
            if not is_member:
                raise HTTPException(
                    status_code=403,
                    detail="Keine Chat-Mitgliedschaft: Benutzer ist kein Mitglied dieser Chat-Gruppe.",
                )
            return

        # 2. Direktchat (1:1)
        if media.direct_chat_id is not None:
            chat = db.query(DirectChat).filter(DirectChat.id == media.direct_chat_id).first()
            if not chat or user_id not in (chat.user_a_id, chat.user_b_id):
                raise HTTPException(
                    status_code=403,
                    detail="Keine Chat-Mitgliedschaft: Benutzer gehoert nicht zu dieser Konversation.",
                )

            # Blockierungspruefung
            other_id = chat.get_other_user_id(user_id)
            is_blocked = (
                db.query(UserFriend)
                .filter(
                    or_(
                        and_(UserFriend.user_id == user_id, UserFriend.friend_id == other_id),
                        and_(UserFriend.user_id == other_id, UserFriend.friend_id == user_id),
                    ),
                    UserFriend.status == "blocked",
                )
                .first()
            ) is not None
            if is_blocked:
                raise HTTPException(
                    status_code=403,
                    detail="Kommunikation blockiert: Zugriff auf Medien verweigert.",
                )
            return

        # 3. Blinde Mailbox Fallback
        clean_mailbox = media.blind_mailbox_id.strip()
        chat_by_box = db.query(DirectChat).filter_by(blind_mailbox_id=clean_mailbox).first()
        if chat_by_box:
            if user_id not in (chat_by_box.user_a_id, chat_by_box.user_b_id):
                raise HTTPException(
                    status_code=403,
                    detail="Keine Chat-Mitgliedschaft: Benutzer gehoert nicht zu diesem Chat.",
                )
            # Blockierungspruefung
            other_id = chat_by_box.get_other_user_id(user_id)
            is_blocked = (
                db.query(UserFriend)
                .filter(
                    or_(
                        and_(UserFriend.user_id == user_id, UserFriend.friend_id == other_id),
                        and_(UserFriend.user_id == other_id, UserFriend.friend_id == user_id),
                    ),
                    UserFriend.status == "blocked",
                )
                .first()
            ) is not None
            if is_blocked:
                raise HTTPException(
                    status_code=403,
                    detail="Kommunikation blockiert: Zugriff auf Medien verweigert.",
                )
            return

        # Uploader selbst darf immer zugreifen
        if media.uploader_user_id == user_id:
            return

        # Pruefe ob der Benutzer mit dem Uploader eine abgeleitete Mailbox teilt (O(1) statt O(N))
        if SocialService.derive_blind_mailbox_id(user_id, media.uploader_user_id) == clean_mailbox:
            is_blocked = (
                db.query(UserFriend)
                .filter(
                    or_(
                        and_(UserFriend.user_id == user_id, UserFriend.friend_id == media.uploader_user_id),
                        and_(UserFriend.user_id == media.uploader_user_id, UserFriend.friend_id == user_id),
                    ),
                    UserFriend.status == "blocked",
                )
                .first()
            ) is not None
            if is_blocked:
                raise HTTPException(
                    status_code=403,
                    detail="Kommunikation blockiert: Zugriff auf Medien verweigert.",
                )
            return

        raise HTTPException(
            status_code=403,
            detail="Keine Chat-Mitgliedschaft fuer diese Medienressource.",
        )

    @classmethod
    def upload_encrypted_media(
        cls,
        db: Session,
        uploader: User,
        blind_mailbox_id: str,
        ciphertext_blob: str,
        file_name: str,
        media_type: str = "application/octet-stream",
        group_id: int | None = None,
        recipient_id: int | None = None,
    ) -> ChatMedia:
        """Nimmt einen clientseitig verschluesselten E2EE-Medienblob entgegen."""
        SocialService.assert_social_enabled(db)

        clean_mailbox = blind_mailbox_id.strip()
        clean_name = sanitize_attachment_filename(file_name)

        # 1. Berechtigungs- und Mitgliedschaftspruefung vor Upload
        direct_chat_id = None
        target_group_id = None

        if group_id:
            group_member = (
                db.query(ChatGroupMember)
                .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == uploader.id)
                .first()
            )
            if not group_member:
                raise HTTPException(
                    status_code=403,
                    detail="Upload fehlgeschlagen: Keine Mitgliedschaft in der Chat-Gruppe.",
                )
            target_group_id = group_id
        elif recipient_id:
            can_msg, reason = SocialService.can_message_user(db, uploader.id, recipient_id)
            if not can_msg:
                raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung zum Senden.")
            chat = SocialService.ensure_direct_chat(db, uploader.id, recipient_id)
            direct_chat_id = chat.id
        else:
            chat = db.query(DirectChat).filter_by(blind_mailbox_id=clean_mailbox).first()
            if chat:
                if uploader.id not in (chat.user_a_id, chat.user_b_id):
                    raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diesen Chat.")
                other_id = chat.get_other_user_id(uploader.id)
                can_msg, reason = SocialService.can_message_user(db, uploader.id, other_id)
                if not can_msg:
                    raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung fuer diesen Chat.")
                direct_chat_id = chat.id
            else:
                candidates = db.query(User.id).filter(User.is_active == True, User.id != uploader.id).all()
                found_target = None
                for (cand_id,) in candidates:
                    if SocialService.derive_blind_mailbox_id(uploader.id, cand_id) == clean_mailbox:
                        found_target = cand_id
                        break
                    min_i, max_i = min(uploader.id, cand_id), max(uploader.id, cand_id)
                    legacy_mailbox = hashlib.sha256(f"msm-e2ee-box:{min_i}:{max_i}:".encode("utf-8")).hexdigest()
                    if clean_mailbox == legacy_mailbox:
                        found_target = cand_id
                        break
                if found_target:
                    can_msg, reason = SocialService.can_message_user(db, uploader.id, found_target)
                    if not can_msg:
                        raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung fuer diesen Chat.")
                    chat = SocialService.ensure_direct_chat(db, uploader.id, found_target)
                    direct_chat_id = chat.id
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="Upload verweigert: Keine gueltige Chat-Mitgliedschaft fuer die angegebene Mailbox-ID.",
                    )

        # 2. Server-seitige Validierung des verschluesselten Blobs
        # Stellt sicher: Server akzeptiert NUR E2EE Blobs, keine Klartexte und keine Riesen-Dateien
        validate_encrypted_blob_payload(ciphertext_blob, max_bytes=MAX_MEDIA_BYTES)

        blob_bytes = ciphertext_blob.encode("utf-8")
        blob_sha256 = hashlib.sha256(blob_bytes).hexdigest()
        media_id = str(uuid.uuid4())

        media = ChatMedia(
            id=media_id,
            blind_mailbox_id=clean_mailbox,
            direct_chat_id=direct_chat_id,
            group_id=target_group_id,
            uploader_user_id=uploader.id,
            ciphertext_blob=ciphertext_blob,
            media_type=media_type[:64],
            file_name=clean_name,
            size_bytes=len(blob_bytes),
            sha256=blob_sha256,
            created_at=_now(),
            expires_at=_now() + timedelta(days=30),  # Optionale Retention
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return media

    @classmethod
    def generate_signed_url(
        cls,
        db: Session,
        user: User,
        media_id: str,
        ttl_seconds: int = 900,  # 15 Minuten Gueltigkeit
    ) -> tuple[str, datetime]:
        """Erzeugt eine signierte Medien-URL mit Ablaufzeit und Chat-Mitgliedschaftspruefung."""
        SocialService.assert_social_enabled(db)

        media = db.query(ChatMedia).filter(ChatMedia.id == media_id).first()
        if not media:
            raise HTTPException(status_code=404, detail="Medienanhang nicht gefunden.")

        # Strikte Pruefung: Fremde duerfen keine signierten URLs fuer Chat-Medien generieren!
        cls.assert_chat_membership(db, user.id, media)

        expires_at = _now() + timedelta(seconds=max(60, min(ttl_seconds, 86400)))
        expires_ts = int(expires_at.timestamp())

        secret = _signing_secret()
        msg_to_sign = f"msm:chat-media:{media.id}:{user.id}:{expires_ts}".encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), msg_to_sign, hashlib.sha256).hexdigest()

        signed_url = f"/api/social/media/{media.id}/download?token={signature}&expires={expires_ts}&user_id={user.id}"
        return signed_url, expires_at

    @classmethod
    def get_media_by_signed_url(
        cls,
        db: Session,
        media_id: str,
        token: str,
        expires: int,
        user_id: int,
    ) -> ChatMedia:
        """Validiert Signatur, Ablaufzeit und Chat-Mitgliedschaft beim Abruf eines Medien-Blobs."""
        now_ts = int(_now().timestamp())
        if now_ts > expires:
            raise HTTPException(
                status_code=403,
                detail="Signierte Medien-URL ist abgelaufen. Bitte neue URL anfordern.",
            )

        secret = _signing_secret()
        expected_msg = f"msm:chat-media:{media_id}:{user_id}:{expires}".encode("utf-8")
        expected_token = hmac.new(secret.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_token, token):
            raise HTTPException(
                status_code=403,
                detail="Ungueltige Signatur fuer signierte Medien-URL.",
            )

        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=403, detail="Benutzerkonto inaktiv oder nicht autorisiert.")

        media = db.query(ChatMedia).filter(ChatMedia.id == media_id).first()
        if not media:
            raise HTTPException(status_code=404, detail="Medienanhang nicht gefunden.")

        if media.expires_at and _now() > _utc(media.expires_at):
            raise HTTPException(status_code=410, detail="Medienanhang ist abgelaufen.")

        # Strikte Pruefung beim Abruf: Auch bei gueltigem Token wird die aktuelle Chat-Mitgliedschaft geprueft!
        cls.assert_chat_membership(db, user.id, media)

        return media
