from __future__ import annotations

import base64
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


# Die Schlüsselkennung im Kopf eines Gruppenumschlags: seit 09/2026 trägt
# `sv-e2ee-group-v1:` sie vor dem Chiffretext, weil ein Gerät mehrere
# Schlüsselgenerationen hält. Ein Umschlag ohne sie stammt aus der Zeit, als
# sich der Gruppenschlüssel aus der Gruppenkennung ableiten ließ, und wird
# beim Schreiben abgewiesen.
_GRUPPEN_KEY = "00112233445566ff."


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


def test_beziehungsalias_loest_auf_und_quittung_erreicht_nur_den_empfaenger(
    db: Session, owner_user: User
):
    """Aliasauflösung und Benachrichtigungsfilter, beide ohne KI-Sendewerkzeug.

    Die drei `propose_message_*`-Werkzeuge sind entfernt, weil sie serverseitig
    verschlüsselt haben. Was sie benutzt haben, lebt weiter und wird hier direkt
    geprüft: die semantische Kontaktauflösung und die Echo-Unterdrückung der
    Benachrichtigungen.
    """
    from services.social_matching_service import SocialMatchingService

    charlie = User(username="charlie_chief", password_hash="hashc", is_active=True)
    db.add(charlie)
    db.commit()

    db.add(UserFriend(user_id=owner_user.id, friend_id=charlie.id, status="accepted"))
    db.commit()

    _allow_memory(db, owner_user)
    _write_memory(db, owner_user, "bester_freund", "bester Freund = Charlie")

    # 1. Der Beziehungsalias führt zum richtigen Konto.
    aufgeloest = SocialMatchingService.resolve_contact(
        db, owner_user, "meinen besten Freund", friend_only=False
    )
    assert aufgeloest is not None
    assert aufgeloest.username == "charlie_chief"

    # 2. Ein Umschlag geht durch das Relais und trägt Absender und Empfänger im
    #    Ereignis — ohne die brauchte der Filter unten raten.
    mailbox = SocialService.derive_blind_mailbox_id(owner_user.id, charlie.id)
    ciphertext = "sv-e2ee-group-v1:" + _GRUPPEN_KEY + base64.b64encode(
        bytes(range(1, 13)) + b"verschluesselter-rumpf" + bytes(16)
    ).decode("ascii")

    published_events: list[dict] = []
    original_publish = SyncEventService.publish

    def mock_publish(event_data, **kwargs):
        published_events.append(dict(event_data))
        return original_publish(event_data, **kwargs)

    SyncEventService.publish = mock_publish
    try:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mailbox,
            ciphertext_envelope=ciphertext,
            sender_user_id=owner_user.id,
            recipient_id=charlie.id,
        )
    finally:
        SyncEventService.publish = original_publish

    msg_event = next(e for e in published_events if e.get("type") == "e2ee_blind_message")
    assert msg_event["sender_user_id"] == owner_user.id
    assert msg_event["recipient_id"] == charlie.id

    # 3. Der Absender darf seine eigene Nachricht nicht als Benachrichtigung
    #    zurückbekommen, der Empfänger schon, und Unbeteiligte gar nicht.
    def benachrichtigt(wer: int) -> bool:
        return NotificationService.should_notify(
            recipient_id=msg_event["recipient_id"],
            current_user_id=wer,
            sender_user_id=msg_event["sender_user_id"],
            is_read=False,
        )

    assert benachrichtigt(owner_user.id) is False
    assert benachrichtigt(charlie.id) is True
    assert benachrichtigt(99999) is False


def test_freundesbindung_der_kontaktaufloesung(db: Session, owner_user: User):
    """`friend_only` muss halten: ein Nicht-Freund darf nicht aufgelöst werden."""
    from services.social_matching_service import SocialMatchingService

    dave = User(username="dave_dev", password_hash="hashd", is_active=True)
    db.add(dave)
    db.commit()

    # Dave ist KEIN Freund, steht aber als Alias im Gedächtnis.
    _allow_memory(db, owner_user)
    _write_memory(db, owner_user, "kollegen", "Kollege = Dave")

    assert SocialMatchingService.resolve_contact(db, owner_user, "Kollege", friend_only=True) is None
