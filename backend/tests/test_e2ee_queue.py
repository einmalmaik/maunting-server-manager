"""Tests für die schlanke FIFO-Zustell-Queue."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from models import E2eeDeviceQueue, User, UserE2eeDevice
from services.social.queue_service import E2eeQueueService


def test_enqueue_and_fetch_pending():
    db = MagicMock(spec=Session)

    sender = User(id=1, username="alice")
    target_device = UserE2eeDevice(
        id=10, user_id=2, device_id="bob-phone", is_approved=True
    )

    # Mock query for friend / block check & target_device
    db.query.return_value.filter.return_value.first.side_effect = [
        None,  # block check passes
        target_device,  # target device found
    ]

    envelopes = [
        {
            "target_device_id": 10,
            "encrypted_payload": "enc:payload:123",
        }
    ]

    with patch("services.sync_event_service.SyncEventService.publish") as mock_publish:
        created_ids = E2eeQueueService.enqueue_message(
            db=db,
            sender=sender,
            envelopes=envelopes,
            client_uuid="uuid-1",
            chat_type="direct",
            chat_id="2",
        )

        assert db.add.called
        assert db.commit.called
        assert mock_publish.called

        # Prüfen, dass der Event-Typ 'new_message' ist
        call_args = mock_publish.call_args[0]
        event = call_args[0]
        assert event["type"] == "new_message"
        assert event["sender_id"] == 1
        assert event["sender_name"] == "alice"
        assert event["client_uuid"] == "uuid-1"


def test_acknowledge_emits_delivery_receipt():
    db = MagicMock(spec=Session)

    device = UserE2eeDevice(id=10, user_id=2, device_id="bob-phone", is_approved=True)
    item = E2eeDeviceQueue(
        id=99,
        target_device_id=10,
        sender_user_id=1,
        chat_type="direct",
        chat_id="2",
        client_uuid="uuid-1",
        encrypted_payload="enc:123",
    )

    db.query.return_value.filter.return_value.all.return_value = [item]

    with patch("services.sync_event_service.SyncEventService.publish") as mock_publish:
        deleted = E2eeQueueService.acknowledge_messages(db, device, [99])

        assert deleted == 1
        assert db.delete.called
        assert db.commit.called

        # Prüfen, dass delivery_receipt an den Absender ging
        assert mock_publish.called
        call_kwargs = mock_publish.call_args[1]
        call_args = mock_publish.call_args[0]
        event = call_args[0]
        assert event["type"] == "delivery_receipt"
        assert event["client_uuid"] == "uuid-1"
        assert call_kwargs["user_id"] == 1  # Absender Alice
