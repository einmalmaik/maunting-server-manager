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
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from models import (
    ChatGroup,
    ChatGroupMember,
    ChatMedia,
    DirectChat,
    User,
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
    """Holt das Signiergeheimnis aus den Einstellungen."""
    secret = getattr(settings, "secret_key", None)
    if not secret:
        raise RuntimeError("MSM_SECRET_KEY ist nicht konfiguriert.")
    return secret


def _ist_blockiert(db: Session, user_a_id: int, user_b_id: int) -> bool:
    """Prueft, ob eine Blockierung zwischen zwei Benutzern vorliegt."""
    return SocialService.is_blocked(db, user_a_id, user_b_id)


# Wie lange ein hochgeladener Blob serverseitig liegen bleibt.
#
# Die Zahl haengt an der laengsten Verfallsfrist, die ein Nutzer im Chat waehlen
# kann (`VERFALL_STUFEN` in `frontend/src/services/nachrichtVerfall.ts`, derzeit
# 90 Tage). Sie stand bis 09/2026 auf 30 und war damit kuerzer als die Frist,
# die im Chat einstellbar ist: eine 40 Tage alte Nachricht war noch da, ihr Bild
# aber nicht mehr abrufbar, und die Anlage brach mit einem 410 weg. Der
# Empfaenger sah eine kaputte Nachricht, ohne dass jemand etwas geloescht haette.
#
# Wer eine laengere Stufe in `VERFALL_STUFEN` ergaenzt, muss diese Zahl
# mitziehen. `test_medien_aufbewahrung_deckt_laengste_verfallsfrist` haelt das
# fest.
MEDIEN_AUFBEWAHRUNG_TAGE = 90

# Wie viel ein Konto gleichzeitig an Anhaengen liegen haben darf. Ohne Grenze
# fuellte ein einzelnes Konto mit 60-MB-Uploads die Platte. 2 GiB sind bei
# 90 Tagen Aufbewahrung weit mehr, als Fotos, Sprachnachrichten und Dateien im
# Alltag brauchen; abgelaufene Anhaenge raeumt der Stundenjob ab.
MEDIEN_KONTINGENT_BYTES = 2 * 1024 * 1024 * 1024


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
        #
        # Bis Stufe 6b stand in der Chatzeile, wer dazugehoert; hier wurde
        # nachgeschlagen. Jetzt steht dort nur noch die Kennung der Mailbox,
        # und die Zugehoerigkeit wird gerechnet: `gegenueber_aus_mailbox`
        # leitet fuer jedes aktive Konto die Kennung ab, die dieses Paar
        # ergaebe. Dieselbe Antwort, ohne dass der Server eine Liste fuehrt.
        clean_mailbox = media.blind_mailbox_id.strip()
        zeile = None
        if media.direct_chat_id is not None:
            zeile = db.query(DirectChat).filter(DirectChat.id == media.direct_chat_id).first()
            if zeile is None:
                raise HTTPException(
                    status_code=403,
                    detail="Keine Chat-Mitgliedschaft: Benutzer gehoert nicht zu dieser Konversation.",
                )
            clean_mailbox = (zeile.blind_mailbox_id or clean_mailbox).strip()
        else:
            zeile = db.query(DirectChat).filter_by(blind_mailbox_id=clean_mailbox).first()

        if zeile is not None:
            gegenueber = SocialService.gegenueber_aus_mailbox(db, user_id, clean_mailbox)
            if gegenueber is None:
                raise HTTPException(
                    status_code=403,
                    detail="Keine Chat-Mitgliedschaft: Benutzer gehoert nicht zu diesem Chat.",
                )
            if _ist_blockiert(db, user_id, gegenueber):
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
            if _ist_blockiert(db, user_id, media.uploader_user_id):
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
        mailbox_token: str | None = None,
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
        else:
            target_recipient_id, direct_chat = SocialService.resolve_mailbox_target(
                db,
                sender_user_id=uploader.id,
                blind_mailbox_id=clean_mailbox,
            )
            if target_recipient_id:
                can_msg, reason = SocialService.can_message_user(db, uploader.id, target_recipient_id)
                if not can_msg:
                    raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung fuer diesen Chat.")
                direct_chat_id = direct_chat.id if direct_chat else SocialService.ensure_direct_chat(db, uploader.id, target_recipient_id).id
            else:
                # Eine Mailbox, die der Server keinem Konto zuordnen kann.
                #
                # Bis 09/2026 war das hier ein 403 — und damit war jeder
                # Anhang in einer Gruppe mit geheimer Mailbox unmoeglich,
                # ausser der Client nannte die `group_id` dazu. Die Zeile
                # bleibt ohne `direct_chat_id` und ohne `group_id`: sie sagt
                # nicht mehr, zu welchem Gespraech der Anhang gehoert, und
                # `assert_chat_membership` faellt beim Lesen auf denselben
                # Mailbox-Weg zurueck.
                #
                # Die Schranke ist dieselbe wie im Relais: Besitznachweis
                # **oder** Teilnahme. Eine erfundene Kennung ohne beides ist
                # weiter 403 — sonst waere der Upload eine Ablage, die jedes
                # angemeldete Konto unter beliebigen Kennungen fuellen kann.
                SocialService.assert_mailbox_zugang(db, uploader.id, clean_mailbox, mailbox_token)

        # 2. Server-seitige Validierung des verschluesselten Blobs
        # Stellt sicher: Server akzeptiert NUR E2EE Blobs, keine Klartexte und keine Riesen-Dateien
        validate_encrypted_blob_payload(ciphertext_blob, max_bytes=MAX_MEDIA_BYTES)

        blob_bytes = ciphertext_blob.encode("utf-8")
        belegt = (
            db.query(func.coalesce(func.sum(ChatMedia.size_bytes), 0))
            .filter(ChatMedia.uploader_user_id == uploader.id)
            .scalar()
        )
        if belegt + len(blob_bytes) > MEDIEN_KONTINGENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Speicher für Anhänge voll (2 GB). Ältere Anhänge werden nach "
                    f"{MEDIEN_AUFBEWAHRUNG_TAGE} Tagen frei oder lassen sich löschen."
                ),
            )
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
            expires_at=_now() + timedelta(days=MEDIEN_AUFBEWAHRUNG_TAGE),
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
    def delete_media(cls, db: Session, user: User, media_id: str) -> bool:
        """Loescht einen Medienblob endgueltig. Nur der Hochladende darf das.

        Gehoert zum Loeschen einer Nachricht: der Anhang liegt nicht im
        Umschlag, sondern als eigener Blob daneben. Bliebe er stehen, waere die
        Nachricht weg und das Bild weiter abrufbar — jedes Chat-Mitglied kann
        sich dafuer eine signierte URL ausstellen lassen.

        Die Beschraenkung auf den Hochladenden ist die engste Regel, die hier
        passt: geloescht wird ueber den Knopf an der eigenen Nachricht, und wer
        eine Datei nicht hochgeladen hat, hat an ihr nichts zu loeschen. Ein
        bereits verschwundener Blob ist kein Fehler, sondern das Ziel.
        """
        SocialService.assert_social_enabled(db)

        media = db.query(ChatMedia).filter(ChatMedia.id == media_id).first()
        if not media:
            return False
        if media.uploader_user_id != user.id:
            raise HTTPException(
                status_code=403,
                detail="Nur der Absender kann einen Anhang loeschen.",
            )

        db.delete(media)
        db.commit()
        return True

    @classmethod
    def cleanup_expired_media(cls, db: Session) -> int:
        """Loescht Anhaenge, deren Aufbewahrung abgelaufen ist.

        Bis 09/2026 galt `expires_at` nur beim Abruf (410) — der verschluesselte
        Blob lag danach unbegrenzt weiter in der Datenbank. Die Frist steht in
        der Datenschutzerklaerung; sie muss also auch loeschen.
        """
        getroffen = (
            db.query(ChatMedia)
            .filter(ChatMedia.expires_at.isnot(None), ChatMedia.expires_at < _now())
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(getroffen)

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
