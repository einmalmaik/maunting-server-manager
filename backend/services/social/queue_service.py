"""Schlanker Queue-Service für den E2EE-Messenger (KISS-Prinzip).

Verwaltet die FIFO-Warteschlange für Zielgeräte:
- Enqueue: Chiffrate ablegen & Echtzeit-Signale senden
- Pending: Anstehende Chiffrate eines Zielgeräts abrufen
- Ack: Quittierte Chiffrate löschen & Zustellquittung an Absender senden
- Read: Lesequittungen an Absender senden
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import ChatGroupMember, E2eeDeviceQueue, User, UserE2eeDevice, UserFriend
from services.sync_event_service import SyncEventService

logger = logging.getLogger(__name__)


class E2eeQueueService:
    @classmethod
    def enqueue_message(
        cls,
        db: Session,
        sender: User,
        envelopes: list[dict[str, Any]],
        client_uuid: str,
        chat_type: str,
        chat_id: str,
        sender_device_id: int | None = None,
    ) -> list[int]:
        """Nimmt verschlüsselte Umschläge für Zielgeräte entgegen und reiht sie ein."""
        if not envelopes:
            raise HTTPException(status_code=400, detail="Keine Umschläge übergeben.")
        if not client_uuid:
            raise HTTPException(status_code=400, detail="client_uuid fehlt.")
        if chat_type not in ("direct", "group"):
            raise HTTPException(status_code=400, detail="Ungültiger chat_type.")

        # Berechtigungsprüfung
        if chat_type == "direct":
            try:
                recipient_id = int(chat_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Ungültige Empfänger-ID.")

            # Prüfen ob blockiert
            block_check = (
                db.query(UserFriend)
                .filter(
                    ((UserFriend.user_id == recipient_id) & (UserFriend.friend_id == sender.id))
                    | ((UserFriend.user_id == sender.id) & (UserFriend.friend_id == recipient_id)),
                    UserFriend.status == "blocked",
                )
                .first()
            )
            if block_check:
                raise HTTPException(status_code=403, detail="Kommunikation nicht möglich.")

        elif chat_type == "group":
            try:
                group_id = int(chat_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Ungültige Gruppen-ID.")

            member = (
                db.query(ChatGroupMember)
                .filter(
                    ChatGroupMember.group_id == group_id,
                    ChatGroupMember.user_id == sender.id,
                )
                .first()
            )
            if not member:
                raise HTTPException(status_code=403, detail="Kein Mitglied dieser Gruppe.")

        created_ids: list[int] = []
        target_user_ids: set[int] = set()
        has_self_sync = False

        for env in envelopes:
            target_device_id = env.get("target_device_id")
            target_device_str = env.get("target_device_str") or env.get("device_id")
            encrypted_payload = env.get("encrypted_payload")
            if not encrypted_payload or (not target_device_id and not target_device_str):
                continue

            query = db.query(UserE2eeDevice)
            if target_device_id:
                query = query.filter(UserE2eeDevice.id == int(target_device_id))
            elif target_device_str:
                query = query.filter(UserE2eeDevice.device_id == str(target_device_str))
            target_device = query.first()
            if not target_device or not target_device.is_approved:
                continue

            # Sicherstellen, dass das Zielgerät zum Chat gehört (Empfänger oder eigenes Zweitgerät)
            if chat_type == "direct":
                if target_device.user_id not in (recipient_id, sender.id):
                    continue
            elif chat_type == "group":
                # Prüfen, ob das Zielgerät zu einem Mitglied der Gruppe gehört
                is_group_member = (
                    db.query(ChatGroupMember)
                    .filter(
                        ChatGroupMember.group_id == group_id,
                        ChatGroupMember.user_id == target_device.user_id,
                    )
                    .first()
                )
                if not is_group_member:
                    continue

            queue_item = E2eeDeviceQueue(
                target_device_id=target_device.id,
                sender_user_id=sender.id,
                sender_device_id=sender_device_id,
                chat_type=chat_type,
                chat_id=str(chat_id),
                client_uuid=client_uuid,
                encrypted_payload=str(encrypted_payload),
                created_at=datetime.now(timezone.utc),
            )
            db.add(queue_item)
            db.flush()
            created_ids.append(queue_item.id)

            if target_device.user_id == sender.id:
                has_self_sync = True
            else:
                target_user_ids.add(target_device.user_id)

        db.commit()

        # Echtzeit-Signale an die Empfänger
        sender_name = getattr(sender, "display_name", None) or sender.username
        now_iso = datetime.now(timezone.utc).isoformat()

        for uid in target_user_ids:
            SyncEventService.publish(
                {
                    "type": "new_message",
                    "sender_id": sender.id,
                    "sender_name": sender_name,
                    "chat_type": chat_type,
                    "chat_id": str(chat_id),
                    "client_uuid": client_uuid,
                    "timestamp": now_iso,
                },
                user_id=uid,
            )

        # Echtzeit-Signal an eigene Zweitgeräte für sofortigen Self-Sync
        if has_self_sync:
            SyncEventService.publish(
                {
                    "type": "new_message",
                    "sender_id": sender.id,
                    "sender_name": sender_name,
                    "chat_type": chat_type,
                    "chat_id": str(chat_id),
                    "client_uuid": client_uuid,
                    "is_self_sync": True,
                    "timestamp": now_iso,
                },
                user_id=sender.id,
            )

        return created_ids

    @classmethod
    def fetch_pending_messages(cls, db: Session, device: UserE2eeDevice) -> list[dict[str, Any]]:
        """Ruft alle anstehenden Umschläge für dieses Gerät in FIFO-Reihenfolge ab."""
        items = (
            db.query(E2eeDeviceQueue)
            .filter(E2eeDeviceQueue.target_device_id == device.id)
            .order_by(E2eeDeviceQueue.id.asc())
            .limit(100)
            .all()
        )

        return [
            {
                "queue_id": item.id,
                "sender_user_id": item.sender_user_id,
                "sender_device_id": item.sender_device_id,
                "chat_type": item.chat_type,
                "chat_id": item.chat_id,
                "client_uuid": item.client_uuid,
                "encrypted_payload": item.encrypted_payload,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ]

    @classmethod
    def acknowledge_messages(
        cls, db: Session, device: UserE2eeDevice, queue_ids: list[int]
    ) -> int:
        """Quittiert erhaltene Chiffrate, löscht sie aus der Queue und sendet Quittungen."""
        if not queue_ids:
            return 0

        items = (
            db.query(E2eeDeviceQueue)
            .filter(
                E2eeDeviceQueue.id.in_(queue_ids),
                E2eeDeviceQueue.target_device_id == device.id,
            )
            .all()
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        deleted_count = 0

        for item in items:
            # Wenn die Nachricht von einem anderen Benutzer kam: Zustellquittung feuern (2 Häkchen grau)
            if item.sender_user_id != device.user_id:
                SyncEventService.publish(
                    {
                        "type": "delivery_receipt",
                        "client_uuid": item.client_uuid,
                        "chat_id": item.chat_id,
                        "recipient_id": device.user_id,
                        "timestamp": now_iso,
                    },
                    user_id=item.sender_user_id,
                )

            db.delete(item)
            deleted_count += 1

        db.commit()
        return deleted_count

    @classmethod
    def mark_chat_read(
        cls,
        db: Session,
        reader: User,
        chat_type: str,
        chat_id: str,
        client_uuids: list[str],
    ) -> None:
        """Informiert den Absender, dass Nachrichten gelesen wurden (2 blaue Häkchen)."""
        if not client_uuids:
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        if chat_type == "direct":
            try:
                partner_id = int(chat_id)
            except ValueError:
                return

            SyncEventService.publish(
                {
                    "type": "read_receipt",
                    "client_uuids": client_uuids,
                    "chat_id": str(reader.id),
                    "reader_id": reader.id,
                    "timestamp": now_iso,
                },
                user_id=partner_id,
            )
        elif chat_type == "group":
            try:
                group_id = int(chat_id)
            except ValueError:
                return

            # Alle Mitglieder der Gruppe über den Lesestatus informieren
            members = (
                db.query(ChatGroupMember.user_id)
                .filter(ChatGroupMember.group_id == group_id)
                .all()
            )
            for (mid,) in members:
                if mid != reader.id:
                    SyncEventService.publish(
                        {
                            "type": "read_receipt",
                            "client_uuids": client_uuids,
                            "chat_id": str(group_id),
                            "reader_id": reader.id,
                            "timestamp": now_iso,
                        },
                        user_id=mid,
                    )

    @classmethod
    def cleanup_expired_messages(cls, db: Session, max_age_days: int = 30) -> int:
        """Räumt alte, nicht abgeholte Queue-Einträge nach Ablauf der Frist ab."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        deleted = (
            db.query(E2eeDeviceQueue)
            .filter(E2eeDeviceQueue.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        if deleted:
            db.commit()
            logger.info("E2EE-Queue: %d abgelaufene Nachrichten bereinigt.", deleted)
        return deleted
