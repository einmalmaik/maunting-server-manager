from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.user import User
from models.vault_entry import VaultEntry
from models.vault_hint import VaultHint
from models.vault_user_setting import VaultUserSetting
from schemas.vault import (
    VaultEntryOut,
    VaultHintStatusResponse,
    VaultMutation,
    VaultSaltResponse,
    VaultSyncRequest,
    VaultSyncResponse,
)
from services.auth_service import AuthService


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBucketAccessDenied(Exception):
    """Raised when a user attempts to access a bucket not belonging to them."""


def sync_vault(db: Session, user: User, request: VaultSyncRequest) -> VaultSyncResponse:
    """Führt einen deterministischen Revisions-Sync für einen blinden Tresor-Bucket durch.

    Sicherheits-Invariante:
    - Der Server kennt keine Benutzernamen, Klartexte oder Passwörter.
    - Bucket-Autorisierung: Jeder Benutzer darf ausschließlich seinen eigenen Bucket syncen.
    - Monotone Revision: Jede serverseitige Mutation erhält eine aufsteigende Revisionsnummer.
    """
    bucket_id = request.bucket_id.lower()

    # 1. Bucket-Autorisierung (SEC-02: IDOR-Schutz)
    # Prüfe, ob dieser Bucket bereits einem ANDEREN Benutzer gehört
    other_owner_stmt = select(VaultUserSetting).where(
        VaultUserSetting.bucket_id == bucket_id,
        VaultUserSetting.user_id != user.id,
    )
    if db.scalar(other_owner_stmt) is not None:
        raise VaultBucketAccessDenied("Zugriff auf fremden Tresor-Bucket verweigert.")

    user_setting = db.get(VaultUserSetting, user.id)
    if user_setting:
        if user_setting.bucket_id and user_setting.bucket_id != bucket_id:
            raise VaultBucketAccessDenied("Nicht autorisierter Tresor-Bucket für dieses Benutzerkonto.")
        if not user_setting.bucket_id:
            user_setting.bucket_id = bucket_id
            user_setting.updated_at = _now()
            db.commit()
    else:
        new_setting = VaultUserSetting(
            user_id=user.id,
            bucket_id=bucket_id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(new_setting)
        db.commit()

    # 2. Monotone Mutation & Revisions-Zuweisung (SEC-03)
    if request.mutations:
        max_rev_db = db.scalar(
            select(func.max(VaultEntry.revision)).where(VaultEntry.bucket_id == bucket_id)
        ) or 0
        current_rev = max(int(max_rev_db), int(request.since_revision))

        mutation_ids = [m.id for m in request.mutations]
        existing_stmt = select(VaultEntry).where(
            VaultEntry.bucket_id == bucket_id,
            VaultEntry.id.in_(mutation_ids),
        )
        existing_map = {row.id: row for row in db.scalars(existing_stmt).all()}

        for m in request.mutations:
            existing = existing_map.get(m.id)
            current_rev += 1
            if existing:
                existing.ciphertext = m.ciphertext
                existing.revision = current_rev
                existing.is_deleted = m.is_deleted
                existing.updated_at = _now()
            else:
                new_entry = VaultEntry(
                    id=m.id,
                    bucket_id=bucket_id,
                    ciphertext=m.ciphertext,
                    revision=current_rev,
                    is_deleted=m.is_deleted,
                    created_at=_now(),
                    updated_at=_now(),
                )
                db.add(new_entry)

        db.commit()

    # Alle Datensätze abfragen, die neuer als der Client-Stand sind
    sync_stmt = (
        select(VaultEntry)
        .where(
            VaultEntry.bucket_id == bucket_id,
            VaultEntry.revision > request.since_revision,
        )
        .order_by(VaultEntry.revision.asc())
    )
    entries_db = db.scalars(sync_stmt).all()

    # Maximale Server-Revision für diesen Bucket ermitteln
    max_rev_stmt = select(func.max(VaultEntry.revision)).where(
        VaultEntry.bucket_id == bucket_id
    )
    max_rev = db.scalar(max_rev_stmt) or request.since_revision

    entries_out = [
        VaultEntryOut(
            id=e.id,
            ciphertext=e.ciphertext,
            revision=e.revision,
            is_deleted=e.is_deleted,
            updated_at=e.updated_at,
        )
        for e in entries_db
    ]

    return VaultSyncResponse(
        server_revision=int(max_rev),
        entries=entries_out,
    )


def get_vault_salt(db: Session, user_id: int) -> VaultSaltResponse:
    """Liest den hinterlegten KDF-Salt und Bucket-Status des Benutzers."""
    setting = db.get(VaultUserSetting, user_id)
    if not setting:
        return VaultSaltResponse(kdf_salt=None, bucket_id=None, has_vault=False)
    return VaultSaltResponse(
        kdf_salt=setting.kdf_salt,
        bucket_id=setting.bucket_id,
        has_vault=bool(setting.bucket_id or setting.kdf_salt),
    )


def set_vault_salt(db: Session, user_id: int, kdf_salt: str, bucket_id: str) -> VaultSaltResponse:
    """Hinterlegt den initialen KDF-Salt und Bucket-ID für Multi-Device Synchronisation."""
    clean_bucket = bucket_id.strip().lower()
    clean_salt = kdf_salt.strip()

    # Prüfe ob Bucket bereits fremd vergeben ist
    other_owner = db.scalar(
        select(VaultUserSetting).where(
            VaultUserSetting.bucket_id == clean_bucket,
            VaultUserSetting.user_id != user_id,
        )
    )
    if other_owner is not None:
        raise VaultBucketAccessDenied("Der angegebene Tresor-Bucket ist bereits vergeben.")

    setting = db.get(VaultUserSetting, user_id)
    if not setting:
        setting = VaultUserSetting(
            user_id=user_id,
            bucket_id=clean_bucket,
            kdf_salt=clean_salt,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(setting)
    else:
        if setting.bucket_id and setting.bucket_id != clean_bucket:
            raise VaultBucketAccessDenied("Der Tresor-Bucket kann nicht nachträglich geändert werden.")
        setting.bucket_id = clean_bucket
        setting.kdf_salt = clean_salt
        setting.updated_at = _now()

    db.commit()
    return VaultSaltResponse(
        kdf_salt=setting.kdf_salt,
        bucket_id=setting.bucket_id,
        has_vault=True,
    )


HINT_RATE_LIMIT_SECONDS = 600  # 10 Minuten Cooldown


def set_vault_hint(db: Session, user_id: int, hint_text: str) -> None:
    """Hinterlegt oder aktualisiert den Passwort-Hinweis (verschlüsselt at rest mit Server-Key und AAD)."""
    encrypted_hint = AuthService.encrypt_secret(
        hint_text.strip(), aad=f"msm:vault:hint:{user_id}"
    )
    hint_obj = db.get(VaultHint, user_id)
    if not hint_obj:
        hint_obj = VaultHint(user_id=user_id, hint=encrypted_hint)
        db.add(hint_obj)
    else:
        hint_obj.hint = encrypted_hint
        hint_obj.updated_at = _now()
    db.commit()


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_vault_hint_status(db: Session, user_id: int) -> VaultHintStatusResponse:
    """Prüft, ob ein Hinweis hinterlegt ist und ob die 10-Minuten-Sperrfrist aktiv ist."""
    hint_obj = db.get(VaultHint, user_id)
    if not hint_obj or not hint_obj.hint:
        return VaultHintStatusResponse(
            has_hint=False,
            last_requested_at=None,
            can_request=False,
            cooldown_seconds_remaining=0,
        )

    cooldown = 0
    can_request = True
    last_req = _to_utc(hint_obj.last_requested_at)
    if last_req:
        diff = (_now() - last_req).total_seconds()
        if diff < HINT_RATE_LIMIT_SECONDS:
            can_request = False
            cooldown = int(HINT_RATE_LIMIT_SECONDS - diff)

    return VaultHintStatusResponse(
        has_hint=True,
        last_requested_at=hint_obj.last_requested_at,
        can_request=can_request,
        cooldown_seconds_remaining=cooldown,
    )


async def request_vault_hint_email(db: Session, user: User) -> tuple[bool, str]:
    """Sendet den hinterlegten Hinweis an die registrierte E-Mail-Adresse des Benutzers.
    
    Verbindliche Invariante: Nur 1 Anfrage alle 10 Minuten erlaubt.
    """
    hint_obj = db.get(VaultHint, user.id)
    if not hint_obj or not hint_obj.hint:
        return False, "Für dein Konto ist kein Passwort-Hinweis hinterlegt."

    now = _now()
    last_req = _to_utc(hint_obj.last_requested_at)
    if last_req:
        diff = (now - last_req).total_seconds()
        if diff < HINT_RATE_LIMIT_SECONDS:
            wait_minutes = max(1, int((HINT_RATE_LIMIT_SECONDS - diff + 59) // 60))
            return (
                False,
                f"Der Hinweis kann nur alle 10 Minuten angefordert werden. Bitte warte noch {wait_minutes} Minute(n).",
            )

    # Entschlüsseln mit AAD (Fallback für etwaige Altdaten)
    try:
        raw_hint = AuthService.decrypt_secret(
            hint_obj.hint, aad=f"msm:vault:hint:{user.id}"
        )
    except Exception:
        raw_hint = hint_obj.hint

    from services.email_service import EmailService

    subject = "Passwort-Manager — Dein Passwort-Hinweis"
    body = f"""Hallo {user.username},

du hast deinen Passwort-Hinweis für den Maunting Service Manager Passwort-Manager angefordert.

Dein hinterlegter Hinweis lautet:
{raw_hint}

Falls du diese Anforderung nicht ausgelöst hast, überprüfe bitte die Sicherheit deines Kontos.

Maunting Service Manager
"""
    html_content = EmailService._notification_email_html(
        user.username,
        "Passwort-Hinweis",
        "Hier ist deine persönliche Gedankenstütze für das Master-Passwort deines Passwort-Managers:",
        f"<strong>{raw_hint}</strong>",
    )

    success = await EmailService.send_email(user.email, subject, body, html_content)
    if not success:
        return False, "E-Mail konnte nicht versendet werden. Bitte prüfe die E-Mail-Konfiguration."

    hint_obj.last_requested_at = now
    db.commit()
    return True, "Dein Passwort-Hinweis wurde erfolgreich an deine E-Mail-Adresse gesendet."
