from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.node import Node
from models.panel_setting import PanelSetting
from models.user import User
from models.vault_entry import VaultEntry
from models.vault_hint import VaultHint
from schemas.vault import (
    VaultEntryOut,
    VaultHintStatusResponse,
    VaultMutation,
    VaultNodeAssignment,
    VaultSyncRequest,
    VaultSyncResponse,
)

PANEL_SETTING_VAULT_NODE = "vault_assigned_node_id"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sync_vault(db: Session, request: VaultSyncRequest) -> VaultSyncResponse:
    """Führt einen deterministischen Revisions-Sync für einen blinden Tresor-Bucket durch.

    Sicherheits-Invariante:
    - Der Server kennt keine Benutzernamen, Klartexte oder Passwörter.
    - `bucket_id` trennt fremde Tresore kryptographisch.
    - Die Revision entscheidet konfliktfrei über den neueren Zustand.
    """
    bucket_id = request.bucket_id.lower()

    if request.mutations:
        mutation_ids = [m.id for m in request.mutations]
        existing_stmt = select(VaultEntry).where(
            VaultEntry.bucket_id == bucket_id,
            VaultEntry.id.in_(mutation_ids),
        )
        existing_map = {row.id: row for row in db.scalars(existing_stmt).all()}

        for m in request.mutations:
            existing = existing_map.get(m.id)
            if existing:
                if m.revision > existing.revision:
                    existing.ciphertext = m.ciphertext
                    existing.revision = m.revision
                    existing.is_deleted = m.is_deleted
                    existing.updated_at = _now()
            else:
                new_entry = VaultEntry(
                    id=m.id,
                    bucket_id=bucket_id,
                    ciphertext=m.ciphertext,
                    revision=m.revision,
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


def get_vault_node_assignment(db: Session) -> VaultNodeAssignment:
    """Ermittelt den im Panel zugewiesenen Multi-Node für den Passwort-Manager."""
    setting = db.get(PanelSetting, PANEL_SETTING_VAULT_NODE)
    node_id_str = setting.value.strip() if setting and setting.value else None

    assigned_name = None
    node_id = None
    if node_id_str:
        try:
            node_int = int(node_id_str)
            node = db.get(Node, node_int)
            if node:
                assigned_name = node.name
                node_id = str(node.id)
        except (ValueError, TypeError):
            pass

    # Zählen, ob überhaupt Nodes registriert sind
    node_count = db.scalar(select(func.count(Node.id))) or 0

    return VaultNodeAssignment(
        node_id=node_id,
        assigned_node_name=assigned_name,
        is_multi_node_active=(node_count > 0),
    )


def set_vault_node_assignment(db: Session, node_id: str | None) -> VaultNodeAssignment:
    """Aktualisiert die Zuweisung des Passwort-Managers an einen dedizierten Node."""
    clean_id = str(node_id).strip() if node_id and str(node_id).strip() else ""

    if clean_id:
        try:
            node_int = int(clean_id)
            node = db.get(Node, node_int)
        except (ValueError, TypeError):
            node = None
        if not node:
            raise ValueError(f"Node with ID '{clean_id}' does not exist")
        clean_id = str(node.id)

    setting = db.get(PanelSetting, PANEL_SETTING_VAULT_NODE)
    if not setting:
        setting = PanelSetting(key=PANEL_SETTING_VAULT_NODE, value=clean_id)
        db.add(setting)
    else:
        setting.value = clean_id
        setting.updated_at = _now()

    db.commit()
    return get_vault_node_assignment(db)


HINT_RATE_LIMIT_SECONDS = 600  # 10 Minuten Cooldown


def set_vault_hint(db: Session, user_id: int, hint_text: str) -> None:
    """Hinterlegt oder aktualisiert den Passwort-Hinweis für das Master-Passwort."""
    hint_obj = db.get(VaultHint, user_id)
    if not hint_obj:
        hint_obj = VaultHint(user_id=user_id, hint=hint_text.strip())
        db.add(hint_obj)
    else:
        hint_obj.hint = hint_text.strip()
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

    from services.email_service import EmailService

    subject = "Passwort-Manager — Dein Passwort-Hinweis"
    body = f"""Hallo {user.username},

du hast deinen Passwort-Hinweis für den Maunting Service Manager Passwort-Manager angefordert.

Dein hinterlegter Hinweis lautet:
{hint_obj.hint}

Falls du diese Anforderung nicht ausgelöst hast, überprüfe bitte die Sicherheit deines Kontos.

Maunting Service Manager
"""
    html_content = EmailService._notification_email_html(
        user.username,
        "Passwort-Hinweis",
        "Hier ist deine persönliche Gedankenstütze für das Master-Passwort deines Passwort-Managers:",
        f"<strong>{hint_obj.hint}</strong>",
    )

    success = await EmailService.send_email(user.email, subject, body, html_content)
    if not success:
        return False, "E-Mail konnte nicht versendet werden. Bitte prüfe die E-Mail-Konfiguration."

    hint_obj.last_requested_at = now
    db.commit()
    return True, "Dein Passwort-Hinweis wurde erfolgreich an deine E-Mail-Adresse gesendet."

