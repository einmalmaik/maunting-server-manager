from __future__ import annotations

import base64
import json
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session

from models import User, UserFriend, ChatGroup, ChatGroupMember, AiConversation
from services.notification_service import NotificationService
from services.social_service import SocialService
from services.sync_event_service import SyncEventService
from services import ai_proposal_service
from services.ai_proposals.lifecycle import create_proposal


def _create_user(db: Session, username: str, privacy: str = "friends") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="test_hash",
        is_active=True,
        social_privacy=privacy,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# 1. Outgoing Echo Bug: Sender-ID strikt von Alerts & Badges ausnehmen
# ============================================================================

def test_sender_id_strictly_excluded_from_direct_alerts():
    """Stellt sicher, dass der Sender einer Nachricht NIEMALS eine Notification
    oder einen Unread-Count für seine eigenen Aktionen erhält.
    """
    sender_id = 42
    recipient_id = 99

    # 1. Direkte Prüfung: Sender als current_user -> IMMER False
    assert NotificationService.should_notify(
        recipient_id=recipient_id,
        current_user_id=sender_id,
        sender_user_id=sender_id,
        is_read=False,
    ) is False

    # Typensicherheit (String-IDs)
    assert NotificationService.should_notify(
        recipient_id=str(recipient_id),
        current_user_id=str(sender_id),
        sender_user_id=str(sender_id),
        is_read=False,
    ) is False

    # is_outgoing_echo Helfer
    assert NotificationService.is_outgoing_echo(
        sender_user_id=sender_id, current_user_id=sender_id
    ) is True
    assert NotificationService.is_outgoing_echo(
        sender_user_id=sender_id, current_user_id=recipient_id
    ) is False

    # Push-Dispatch Plan für Sender muss None sein
    push_plan = NotificationService.prepare_push_dispatch(
        target_user_id=sender_id,
        sender_user_id=sender_id,
        title="Test",
        body="Nachricht",
    )
    assert push_plan is None

    # Für den tatsächlichen Empfänger muss es True / validierter Plan sein
    assert NotificationService.should_notify(
        recipient_id=recipient_id,
        current_user_id=recipient_id,
        sender_user_id=sender_id,
        is_read=False,
    ) is True

    recipient_push = NotificationService.prepare_push_dispatch(
        target_user_id=recipient_id,
        sender_user_id=sender_id,
        title="Neue Nachricht",
        body="Geheimer Text",
        is_e2ee=True,
    )
    assert recipient_push is not None
    assert recipient_push["target_user_id"] == recipient_id


def test_sender_id_strictly_excluded_from_group_alerts():
    """In Gruppen darf der Sender trotz Gruppenmitgliedschaft niemals benachrichtigt werden."""
    sender_id = 10
    member_ids = [10, 20, 30]

    # Sender ist Mitglied -> trotzdem False (Outgoing Echo Prevention)
    assert NotificationService.should_notify_group(
        group_member_ids=member_ids,
        current_user_id=sender_id,
        sender_user_id=sender_id,
        is_read=False,
    ) is False

    # Andere Mitglieder -> True
    assert NotificationService.should_notify_group(
        group_member_ids=member_ids,
        current_user_id=20,
        sender_user_id=sender_id,
        is_read=False,
    ) is True

    # Nicht-Mitglied -> False
    assert NotificationService.should_notify_group(
        group_member_ids=member_ids,
        current_user_id=99,
        sender_user_id=sender_id,
        is_read=False,
    ) is False


def test_sender_id_strictly_excluded_when_ai_worker_sends_message(db: Session, owner_user: User):
    """Wenn die KI / ein Worker im Namen des Benutzers eine Nachricht sendet,
    darf für diesen Benutzer kein Alert oder Unread-Badge getriggert werden.
    """
    user_sender = owner_user
    user_recipient = _create_user(db, "recipient_ai_user")

    rel = UserFriend(user_id=user_sender.id, friend_id=user_recipient.id, status="accepted")
    db.add(rel)
    db.commit()

    conv = AiConversation(id=str(uuid4()), user_id=user_sender.id, title="AI Sender Chat", kind="primary")
    db.add(conv)
    db.commit()

    prop = create_proposal(
        db,
        user=user_sender,
        conversation=conv,
        correlation_id=str(uuid4()),
        tool_name="propose_message_contact",
        arguments={
            "recipient_username": user_recipient.username,
            "message_text": "Automatische Nachricht via KI",
            "rationale": "Terminbestätigung",
        },
    )

    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=prop.id, user=user_sender)

    published: list[dict] = []
    orig_pub = SyncEventService.publish

    def mock_pub(ev, **kwargs):
        published.append(dict(ev))
        return orig_pub(ev, **kwargs)

    SyncEventService.publish = mock_pub
    try:
        exec_res, _ = ai_proposal_service.execute_proposal(
            db, proposal_id=prop.id, user=user_sender, confirmation_token=token
        )
        assert exec_res.status == "succeeded"

        msg_ev = next(e for e in published if e.get("type") == "e2ee_blind_message")
        assert msg_ev["sender_user_id"] == user_sender.id
        assert msg_ev["recipient_id"] == user_recipient.id

        # Sender (user_sender) darf KEINE Benachrichtigung / Unread-Badge erhalten!
        assert NotificationService.should_notify(
            recipient_id=msg_ev["recipient_id"],
            current_user_id=user_sender.id,
            sender_user_id=msg_ev["sender_user_id"],
            is_read=False,
        ) is False

        # Empfänger (user_recipient) MUSS benachrichtigt werden
        assert NotificationService.should_notify(
            recipient_id=msg_ev["recipient_id"],
            current_user_id=user_recipient.id,
            sender_user_id=msg_ev["sender_user_id"],
            is_read=False,
        ) is True
    finally:
        SyncEventService.publish = orig_pub


# ============================================================================
# 2. Steuersignale: Read Receipts & Delivery Receipts triggern keine Alerts
# ============================================================================

def test_control_messages_strictly_suppressed_from_notifications_and_badges():
    """Interne Steuersignale wie Lesequittungen (read_receipt) dürfen
    weder Push-Benachrichtigungen noch Unread-Badges erhöhen.
    """
    current_user_id = 20
    sender_id = 10

    # 1. Normal message -> True
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_control=False,
        control_type="message",
    ) is True

    # 2. Control message: read_receipt -> False
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_control=True,
        control_type="read_receipt",
    ) is False

    # 3. Control message: delivery_receipt -> False
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_control=True,
        control_type="delivery_receipt",
    ) is False

    # 4. Control message: edit_message / delete_message -> False
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_control=True,
        control_type="edit_message",
    ) is False

    # Push Dispatch Plan für Steuersignale muss None sein
    assert NotificationService.prepare_push_dispatch(
        target_user_id=current_user_id,
        sender_user_id=sender_id,
        title="Quittung",
        is_control=True,
        control_type="read_receipt",
    ) is None


# ============================================================================
# 3. Background vs. Foreground Push: Keine doppelten OS-Pushes bei aktiver Verbindung
# ============================================================================

def test_background_vs_foreground_push_suppression():
    """Bei aktiver WebSocket/SSE-Verbindung im Vordergrund werden OS-Pushes
    unterdrückt, um doppelte Alerts zu vermeiden.
    """
    current_user_id = 20
    sender_id = 10

    # Im Vordergrund (active foreground connection) -> OS-Push unterdrückt (False)
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_read=False,
        has_active_foreground_client=True,
    ) is False

    push_plan_foreground = NotificationService.prepare_push_dispatch(
        target_user_id=current_user_id,
        sender_user_id=sender_id,
        title="Neue Nachricht",
        has_active_foreground_connection=True,
    )
    assert push_plan_foreground is None

    # Im Hintergrund (keine aktive Foreground-Verbindung) -> OS-Push erlaubt (True)
    assert NotificationService.should_notify(
        recipient_id=current_user_id,
        current_user_id=current_user_id,
        sender_user_id=sender_id,
        is_read=False,
        has_active_foreground_client=False,
    ) is True

    push_plan_background = NotificationService.prepare_push_dispatch(
        target_user_id=current_user_id,
        sender_user_id=sender_id,
        title="Neue Nachricht",
        has_active_foreground_connection=False,
    )
    assert push_plan_background is not None


# ============================================================================
# 4. Privacy-Leaks in Payloads: Keine Klartext- oder Metadaten-Leaks bei E2EE
# ============================================================================

def test_privacy_leaks_prevented_in_push_payloads():
    """Prüft, ob in Push-Payloads Klartextnachrichten oder sensible Metadaten
    vollständig bereinigt werden, wenn E2EE oder Privatsphäre-Filter greifen.
    """
    raw_payload = {
        "title": "Alice: Top-Secret Information",
        "body": "Das Passwort lautet 123456!",
        "text": "Das Passwort lautet 123456!",
        "message_text": "Das Passwort lautet 123456!",
        "content": "Streng vertraulich",
        "preview": "Passwort lautet...",
        "ciphertext_envelope": "sv-e2ee-v1:aW52YWxpZF9kYXRh",
        "secret": "master_key",
        "token": "bearer_secret",
        "key": "aes_gcm_secret",
        "sender_name": "Alice",
        "is_group": False,
    }

    sanitized = NotificationService.sanitize_push_payload(raw_payload, is_e2ee=True, privacy_mode=True)

    # Niemals Klartextinhalte leaken
    assert "text" not in sanitized
    assert "message_text" not in sanitized
    assert "content" not in sanitized
    assert "preview" not in sanitized
    assert "ciphertext_envelope" not in sanitized
    assert "secret" not in sanitized
    assert "token" not in sanitized
    assert "key" not in sanitized

    # Der Text darf nicht mehr das Passwort enthalten
    assert "123456" not in sanitized["body"]
    assert "123456" not in sanitized["title"]
    assert "Top-Secret" not in sanitized["title"]

    # Generischer, sicherer Text
    assert sanitized["body"] == "Du hast eine neue verschlüsselte Nachricht erhalten."
    assert sanitized["title"] == "Neue Nachricht: Alice"
    assert sanitized["is_e2ee"] is True
    assert sanitized["privacy_filtered"] is True


def test_privacy_leaks_prevented_in_group_payload():
    """Gruppen-Nachrichten mit E2EE verwenden sichere, generische Texte ohne Leak."""
    raw_group_payload = {
        "title": "Gruppe Alpha",
        "text": "Geheimer Treffpunkt um 20:00 Uhr",
        "body": "Geheimer Treffpunkt um 20:00 Uhr",
        "chat_name": "Projekt Genesis",
        "is_group": True,
    }

    sanitized = NotificationService.sanitize_push_payload(raw_group_payload, is_e2ee=True)
    assert "Treffpunkt" not in sanitized["body"]
    assert sanitized["body"] == "Neue Nachricht in der Gruppe."
    assert sanitized["title"] == "Neue Nachricht in „Projekt Genesis“"


# ============================================================================
# 5. Gruppen-Zielgruppen-Routing: Nur Gruppenmitglieder erhalten Envelopes
# ============================================================================

@pytest.mark.asyncio
async def test_group_relay_targeted_to_members_only_no_leak_to_strangers(db: Session):
    """Gruppennachrichten werden zielgerichtet nur an Mitglieder der Gruppe ausgeliefert,
    während Unbeteiligte keinerlei Event oder Metadaten erhalten.
    """
    import hashlib

    alice = _create_user(db, "group_alice")
    bob = _create_user(db, "group_bob")
    charlie_stranger = _create_user(db, "group_charlie_stranger")

    group = SocialService.create_group(db, alice, name="MSS Core Devs")
    SocialService.join_group_by_invite_code(db, bob, group.invite_code)

    g_mid = hashlib.sha256(f"msm:group:{group.id}".encode("utf-8")).hexdigest()

    conn_alice, q_alice = SyncEventService.subscribe(user_id=alice.id)
    conn_bob, q_bob = SyncEventService.subscribe(user_id=bob.id)
    conn_stranger, q_stranger = SyncEventService.subscribe(user_id=charlie_stranger.id)

    valid_env = "sv-e2ee-group-v1:" + base64.b64encode(b"N" * 12 + b"group_payload_test" + b"T" * 16).decode("ascii")
    illegal_env = "sv-e2ee-group-v1:" + base64.b64encode(b"N" * 12 + b"illegal_payload_test" + b"T" * 16).decode("ascii")

    try:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=g_mid,
            ciphertext_envelope=valid_env,
            sender_user_id=alice.id,
        )

        # Alice (Sender) erhält das Event zur Multi-Device Sync
        assert not q_alice.empty()
        ev_a = q_alice.get_nowait()
        assert ev_a["sender_user_id"] == alice.id

        # Bob (Gruppenmitglied) erhält das Event
        assert not q_bob.empty()
        ev_b = q_bob.get_nowait()
        assert ev_b["blind_mailbox_id"] == g_mid

        # Charlie (Fremder, kein Mitglied) darf absolut NICHTS erhalten!
        assert q_stranger.empty()

        # Fremder darf nicht in die Gruppe senden
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            SocialService.relay_blind_envelope(
                db,
                blind_mailbox_id=g_mid,
                ciphertext_envelope=illegal_env,
                sender_user_id=charlie_stranger.id,
            )
        assert exc.value.status_code == 403
    finally:
        SyncEventService.unsubscribe(conn_alice)
        SyncEventService.unsubscribe(conn_bob)
        SyncEventService.unsubscribe(conn_stranger)


# ============================================================================
# 6. Integrationstests: Strikter Sender-Ausschluss (KI/Worker, UUIDs, Multi-Device)
# ============================================================================

def test_sender_id_strictly_excluded_with_worker_and_string_formats():
    """Stellt sicher, dass auch mit Worker/AI-Präfixen oder String-IDs der Sender
    striktestens von Alerts, Badges und Dispatch-Aufträgen ausgenommen ist.
    """
    user_id = 42

    for sender_variant in [
        user_id,
        str(user_id),
        f"worker:{user_id}",
        f"ai:{user_id}",
        f"actor:{user_id}",
        f"user:{user_id}",
    ]:
        # Echo-Erkennung muss True sein
        assert NotificationService.is_outgoing_echo(
            sender_user_id=sender_variant, current_user_id=user_id
        ) is True

        # should_notify für den Absender muss IMMER False sein
        assert NotificationService.should_notify(
            recipient_id=99,
            current_user_id=user_id,
            sender_user_id=sender_variant,
            is_read=False,
        ) is False

        # Gruppen-Notify für den Absender muss IMMER False sein
        assert NotificationService.should_notify_group(
            group_member_ids=[user_id, 99],
            current_user_id=user_id,
            sender_user_id=sender_variant,
            is_read=False,
        ) is False

        # Push-Dispatch Plan für den Absender muss None sein
        assert NotificationService.prepare_push_dispatch(
            target_user_id=user_id,
            sender_user_id=sender_variant,
            title="Neue Nachricht",
        ) is None


def test_notification_dispatcher_active_foreground_suppression():
    """Verifiziert, dass bei aktiver Foreground-Verbindung kein doppelter OS-Push gedispatcht wird."""
    SyncEventService.clear_all_for_testing()

    alice_id = 101
    bob_id = 102

    # Bob verbindet sich via SSE (aktive Verbindung im Vordergrund)
    conn_bob, _ = SyncEventService.subscribe(user_id=bob_id)
    try:
        assert SyncEventService.has_active_subscribers(bob_id) is True
        assert SyncEventService.has_active_subscribers(alice_id) is False

        # Push-Dispatch für Bob (online im Vordergrund) -> MUSS unterdrückt werden (None)
        push_bob = NotificationService.dispatch_push(
            target_user_id=bob_id,
            sender_user_id=alice_id,
            title="Nachricht an Bob",
            has_active_foreground_connection=SyncEventService.has_active_subscribers(bob_id),
        )
        assert push_bob is None

        # Push-Dispatch für Alice (offline / Hintergrund) -> MUSS gedispatcht werden
        push_alice = NotificationService.dispatch_push(
            target_user_id=alice_id,
            sender_user_id=bob_id,
            title="Nachricht an Alice",
            has_active_foreground_connection=SyncEventService.has_active_subscribers(alice_id),
        )
        assert push_alice is not None
        assert push_alice["target_user_id"] == alice_id
    finally:
        SyncEventService.unsubscribe(conn_bob)
        SyncEventService.clear_all_for_testing()


def test_multi_device_chat_focus_and_unread_counter_invariants():
    """Verifiziert das Verhalten bei Chat-Fokus vs. Hintergrund."""
    recipient_id = 55
    sender_id = 12

    # 1. Chat im Fokus (is_read=True) -> KEIN Alert / Badge
    assert NotificationService.should_notify(
        recipient_id=recipient_id,
        current_user_id=recipient_id,
        sender_user_id=sender_id,
        is_read=True,
    ) is False

    # 2. Chat im Hintergrund / nicht im Fokus (is_read=False) -> Alert / Badge erlaubt
    assert NotificationService.should_notify(
        recipient_id=recipient_id,
        current_user_id=recipient_id,
        sender_user_id=sender_id,
        is_read=False,
    ) is True


def test_strict_privacy_payload_all_leak_keys_and_title_sanitization():
    """Stellt sicher, dass alle potenziellen Klartext- und Secret-Schlüssel restlos gestrippt werden."""
    sensitive_payload = {
        "title": "Geheime Botschaft: 42 ist die Antwort",
        "body": "Vertraulicher Text",
        "plaintext": "Top Secret Cleartext",
        "msg": "Hallo Welt",
        "message": "Geheimnis",
        "private_key": "privkey_bytes",
        "secret_key": "secretkey_bytes",
        "channel_key": "channel_bytes",
        "auth_tag": "tag_bytes",
        "iv": "iv_bytes",
        "salt": "salt_bytes",
        "sender_name": "Agent007",
    }

    clean = NotificationService.sanitize_push_payload(sensitive_payload, is_e2ee=True)

    for leaked in [
        "plaintext", "msg", "message", "private_key", "secret_key",
        "channel_key", "auth_tag", "iv", "salt"
    ]:
        assert leaked not in clean

    assert "42" not in clean["title"]
    assert clean["title"] == "Neue Nachricht: Agent007"
    assert clean["body"] == "Du hast eine neue verschlüsselte Nachricht erhalten."

