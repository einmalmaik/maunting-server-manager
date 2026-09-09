from __future__ import annotations

import json
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session

from models import User, UserFriend, AiConversation
from services.social_service import SocialService
from services.social_matching_service import SocialMatchingService
from services.notification_service import NotificationService
from services import ai_memory_service, ai_proposal_service
from services.ai_action_service import _execute_global_read_tool
from services.ai_proposals.lifecycle import create_proposal
from services.sync_event_service import SyncEventService
from services.ai_action_errors import AiActionValidationError


def _allow_memory(db: Session, user: User) -> None:
    ai_memory_service.set_preference(db, user, True)


def _write_memory(db: Session, user: User, key: str, value: str) -> None:
    ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key=key, value=value, origin="user"
    )


def test_notification_service_echo_prevention_and_recipient_filtering():
    """Prüft die strikte Notification-Logik:
    1. Outgoing Echo Prevention: Sender darf niemals benachrichtigt werden.
    2. Empfänger-Filterung: Ausschließlich recipient_id == current_user && !is_read."""

    # Outgoing Echo Prevention: Sender ist aktueller User -> False
    assert NotificationService.should_notify(
        recipient_id=2, current_user_id=1, sender_user_id=1, is_read=False
    ) is False

    # Outgoing Echo Prevention auch mit String-IDs (Typsicherheit)
    assert NotificationService.should_notify(
        recipient_id="2", current_user_id="1", sender_user_id="1", is_read=False
    ) is False

    # Bereits gelesen -> False
    assert NotificationService.should_notify(
        recipient_id=2, current_user_id=2, sender_user_id=1, is_read=True
    ) is False

    # Falscher Empfänger -> False
    assert NotificationService.should_notify(
        recipient_id=3, current_user_id=2, sender_user_id=1, is_read=False
    ) is False

    # Nicht angemeldeter Benutzer (current_user_id is None) -> False
    assert NotificationService.should_notify(
        recipient_id=2, current_user_id=None, sender_user_id=1, is_read=False
    ) is False

    # Valider Empfänger, ungesehen -> True
    assert NotificationService.should_notify(
        recipient_id=2, current_user_id=2, sender_user_id=1, is_read=False
    ) is True

    # Valider Empfänger mit String-IDs -> True
    assert NotificationService.should_notify(
        recipient_id="2", current_user_id=2, sender_user_id="1", is_read=False
    ) is True

    # Gruppen-Nachricht: Sender ist aktueller User -> False (Echo Prevention)
    assert NotificationService.should_notify_group(
        group_member_ids=[1, 2, 3], current_user_id=1, sender_user_id=1, is_read=False
    ) is False

    # Gruppen-Nachricht: Mitglied, ungesehen -> True
    assert NotificationService.should_notify_group(
        group_member_ids=[1, 2, 3], current_user_id=2, sender_user_id=1, is_read=False
    ) is True

    # Gruppen-Nachricht: Nicht-Mitglied -> False
    assert NotificationService.should_notify_group(
        group_member_ids=[1, 3], current_user_id=2, sender_user_id=1, is_read=False
    ) is False


def test_semantic_user_matching_memory_relationship_alias(db: Session, owner_user: User):
    """Prüft, dass Beziehungs-Aliase wie 'bester Freund = Raik' aus dem Memory
    semantisch zugeordnet werden – auch wenn der DB-Username 'raik_gamer' lautet!"""
    raik = User(username="raik_gamer", password_hash="hashr", is_active=True)
    db.add(raik)
    db.commit()

    # Freundschaft bestätigen
    f_rel = UserFriend(user_id=owner_user.id, friend_id=raik.id, status="accepted")
    db.add(f_rel)
    db.commit()

    _allow_memory(db, owner_user)
    # Memory-Eintrag mit 'Raik' (nicht raik_gamer!) und Punkt am Ende
    _write_memory(db, owner_user, "beziehung_bester_freund", "Mein bester Freund ist Raik.")

    # 1. Direkter Resolver-Test mit verschiedenen Deklinationen
    resolved = SocialMatchingService.resolve_contact(db, owner_user, "bester Freund", friend_only=True)
    assert resolved is not None
    assert resolved.id == raik.id
    assert resolved.username == "raik_gamer"

    resolved_acc = SocialMatchingService.resolve_contact(db, owner_user, "meinen besten Freund", friend_only=True)
    assert resolved_acc is not None
    assert resolved_acc.id == raik.id

    resolved_prep = SocialMatchingService.resolve_contact(db, owner_user, "an meinen besten Freund", friend_only=True)
    assert resolved_prep is not None
    assert resolved_prep.id == raik.id

    # 2. search_messenger_contacts via KI Read-Tool
    search_res = _execute_global_read_tool(
        db, user=owner_user, tool_name="search_messenger_contacts", arguments={"query": "meinen besten Freund"}
    )
    assert search_res["count"] >= 1
    assert any(c["username"] == "raik_gamer" for c in search_res["contacts"])


def test_semantic_user_matching_nickname_in_memory(db: Session, owner_user: User):
    """Prüft, dass hinterlegte Spitznamen ('Ali ist der Spitzname von alice_wonderland') aufgelöst werden."""
    alice = User(username="alice_wonderland", password_hash="hasha", is_active=True)
    db.add(alice)
    db.commit()

    f_rel = UserFriend(user_id=owner_user.id, friend_id=alice.id, status="accepted")
    db.add(f_rel)
    db.commit()

    _allow_memory(db, owner_user)
    _write_memory(db, owner_user, "spitznamen", "Ali ist der Spitzname von alice_wonderland")

    resolved = SocialMatchingService.resolve_contact(db, owner_user, "Ali", friend_only=True)
    assert resolved is not None
    assert resolved.id == alice.id
    assert resolved.username == "alice_wonderland"

    # Auch Kleinschreibung / Floskeln
    resolved_cmd = SocialMatchingService.resolve_contact(db, owner_user, "ali", friend_only=True)
    assert resolved_cmd is not None
    assert resolved_cmd.id == alice.id


def test_fuzzy_contact_matching_on_typos(db: Session, owner_user: User):
    """Prüft die fehlertolerante Fuzzy-Suche bei Tippfehlern im Kontaktnamen."""
    bob = User(username="robert_speedy", password_hash="hashb", is_active=True)
    db.add(bob)
    db.commit()

    f_rel = UserFriend(user_id=owner_user.id, friend_id=bob.id, status="accepted")
    db.add(f_rel)
    db.commit()

    # Tippfehler "robert_spedy" statt "robert_speedy"
    resolved = SocialMatchingService.resolve_contact(db, owner_user, "robert_spedy", friend_only=True)
    assert resolved is not None
    assert resolved.id == bob.id

    # Suche via Tool
    search_res = _execute_global_read_tool(
        db, user=owner_user, tool_name="search_messenger_contacts", arguments={"query": "robert_spedy"}
    )
    assert search_res["count"] >= 1
    assert any(c["username"] == "robert_speedy" for c in search_res["contacts"])


def test_propose_message_contact_with_alias_and_echo_prevention(db: Session, owner_user: User):
    """Prüft das Erstellen und Ausführen von propose_message_contact mit Beziehungs-Alias
    sowie die Weitergabe von recipient_id zur Outgoing Echo Prevention."""
    charlie = User(username="charlie_chief", password_hash="hashc", is_active=True)
    db.add(charlie)
    db.commit()

    f_rel = UserFriend(user_id=owner_user.id, friend_id=charlie.id, status="accepted")
    db.add(f_rel)
    db.commit()

    _allow_memory(db, owner_user)
    _write_memory(db, owner_user, "bester_freund", "bester Freund = Charlie")

    conv = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Test Chat", kind="primary")
    db.add(conv)
    db.commit()

    # Proposal mit Alias "meinen besten Freund"
    prop = create_proposal(
        db,
        user=owner_user,
        conversation=conv,
        correlation_id=str(uuid4()),
        tool_name="propose_message_contact",
        arguments={
            "recipient_username": "meinen besten Freund",
            "message_text": "Hey wie gehts dir?",
            "rationale": "Freund kontaktieren",
        },
    )
    assert prop.tool_name == "propose_message_contact"
    # Wurde semantisch zu charlie_chief aufgelöst
    preview = json.loads(prop.preview_json)
    assert preview["recipient_username"] == "charlie_chief"

    # Bestätigen & Ausführen
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=prop.id, user=owner_user)

    published_events: list[dict] = []
    original_publish = SyncEventService.publish

    def mock_publish(event_data, **kwargs):
        published_events.append(dict(event_data))
        return original_publish(event_data, **kwargs)

    SyncEventService.publish = mock_publish
    try:
        exec_prop, _ = ai_proposal_service.execute_proposal(
            db,
            proposal_id=prop.id,
            user=owner_user,
            confirmation_token=token,
        )
        assert exec_prop.status == "succeeded"

        # Prüfe, dass das SSE Event den sender_user_id und recipient_id korrekt trägt
        assert len(published_events) >= 1
        msg_event = next(e for e in published_events if e.get("type") == "e2ee_blind_message")
        assert msg_event["sender_user_id"] == owner_user.id
        assert msg_event["recipient_id"] == charlie.id

        # NotificationService Filterung überprüfen
        # Für den Sender (owner_user) darf KEINE Benachrichtigung getriggert werden
        assert NotificationService.should_notify(
            recipient_id=msg_event["recipient_id"],
            current_user_id=owner_user.id,
            sender_user_id=msg_event["sender_user_id"],
            is_read=False,
        ) is False

        # Für den Empfänger (charlie) MUSS die Benachrichtigung getriggert werden
        assert NotificationService.should_notify(
            recipient_id=msg_event["recipient_id"],
            current_user_id=charlie.id,
            sender_user_id=msg_event["sender_user_id"],
            is_read=False,
        ) is True

        # Für unbeteiligte Dritte darf KEINE Benachrichtigung getriggert werden
        assert NotificationService.should_notify(
            recipient_id=msg_event["recipient_id"],
            current_user_id=99999,
            sender_user_id=msg_event["sender_user_id"],
            is_read=False,
        ) is False
    finally:
        SyncEventService.publish = original_publish


def test_propose_message_friend_with_alias_and_security_boundary(db: Session, owner_user: User):
    """Prüft propose_message_friend mit Alias und dass Unbefugte sicher abgewiesen werden."""
    dave = User(username="dave_dev", password_hash="hashd", is_active=True)
    db.add(dave)
    db.commit()

    # Dave ist KEIN Freund!
    _allow_memory(db, owner_user)
    _write_memory(db, owner_user, "kollegen", "Kollege = Dave")

    conv = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Test Chat 2", kind="primary")
    db.add(conv)
    db.commit()

    # Sicherheitsgrenze: Da Dave kein bestätigter Freund ist, muss propose_message_friend abweisen!
    with pytest.raises(AiActionValidationError) as exc:
        create_proposal(
            db,
            user=owner_user,
            conversation=conv,
            correlation_id=str(uuid4()),
            tool_name="propose_message_friend",
            arguments={
                "friend_username": "Kollege",
                "message_text": "Hey Kollege",
                "rationale": "Kollegen schreiben",
            },
        )
    assert "Sicherheitsblockade" in str(exc.value) or "existiert nicht" in str(exc.value)
