"""Wer eine Messenger-Benachrichtigung bekommt — und wer nicht.

Bis 09/2026 stand hier zusätzlich die semantische Kontaktauflösung
(`social_matching_service.py`). Sie gehörte zu den KI-Werkzeugen, die den
Messenger durchsuchen durften; beide sind entfernt, und der Auflöser hatte
danach keinen Aufrufer mehr.
"""
from __future__ import annotations

import base64
from sqlalchemy.orm import Session

from models import User, UserFriend
from services.social_service import SocialService
from services.notification_service import NotificationService
from services.sync_event_service import SyncEventService


# Die Schlüsselkennung im Kopf eines Gruppenumschlags: seit 09/2026 trägt
# `sv-e2ee-group-v1:` sie vor dem Chiffretext, weil ein Gerät mehrere
# Schlüsselgenerationen hält. Ein Umschlag ohne sie stammt aus der Zeit, als
# sich der Gruppenschlüssel aus der Gruppenkennung ableiten ließ, und wird
# beim Schreiben abgewiesen.
_GRUPPEN_KEY = "00112233445566ff."


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


def test_quittung_erreicht_nur_den_empfaenger(db: Session, owner_user: User):
    """Der Benachrichtigungsfilter am echten Relais, ohne jedes KI-Werkzeug.

    Hier stand bis 09/2026 zusätzlich die semantische Kontaktauflösung. Sie ist
    mit ihrem Dienst entfernt: die KI hat kein Werkzeug mehr, das den Messenger
    anfasst, und der Auflöser hatte danach keinen Aufrufer. Was bleibt, ist der
    Teil, der wirklich läuft — das Relais trägt Absender und Empfänger im
    Ereignis, und der Filter entscheidet daraus.
    """
    charlie = User(username="charlie_chief", password_hash="hashc", is_active=True)
    db.add(charlie)
    db.commit()

    db.add(UserFriend(user_id=owner_user.id, friend_id=charlie.id, status="accepted"))
    db.commit()

    # Ein Umschlag geht durch das Relais und trägt Absender und Empfänger im
    # Ereignis — ohne die müsste der Filter unten raten.
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
        )
    finally:
        SyncEventService.publish = original_publish

    msg_event = next(e for e in published_events if e.get("type") == "e2ee_blind_message")
    assert msg_event["sender_user_id"] == owner_user.id
    assert msg_event["recipient_id"] == charlie.id

    # Der Absender darf seine eigene Nachricht nicht als Benachrichtigung
    # zurückbekommen, der Empfänger schon, und Unbeteiligte gar nicht.
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


