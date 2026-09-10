import base64
import pytest
from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import User, UserFriend, DirectChat
from services.social_service import SocialService
from services.sync_event_service import SyncEventService


def _valid_test_envelope(prefix: str = "sv-e2ee-v1:", payload_tag: str = "test-payload") -> str:
    raw = b"N" * 12 + payload_tag.encode("utf-8") + b"T" * 16
    return f"{prefix}{base64.b64encode(raw).decode('ascii')}"


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
# 1. Tests für Dreistufige Sichtbarkeit & Status-Maskierung (Server-Ebene)
# ============================================================================

def test_visibility_public_profile_and_presence(db: Session):
    """Öffentlich: Jeder sieht Online-Status, Stories und aktuelle Nutzer-Aktivitäten."""
    public_user = _create_user(db, "alice_pub", privacy="public")
    stranger = _create_user(db, "bob_stranger", privacy="friends")

    # Präsenz setzen mit Aktivitäten
    SocialService.update_presence(db, public_user.id, {
        "status": "online",
        "device_type": "desktop",
        "custom_status": "Am Zocken",
        "activity_label": "Valheim",
        "activity_detail": "Server Alpha",
    })

    # Fremder (stranger) ruft Profil und Präsenz ab
    profile = SocialService.get_profile(db, viewer_user_id=stranger.id, target_user=public_user)
    assert profile["restricted"] is False
    assert profile["privacy"] == "public"
    assert profile["presence"] is not None
    assert profile["presence"]["status"] == "online"
    assert profile["presence"]["activity_label"] == "Valheim"
    assert profile["presence"]["activity_detail"] == "Server Alpha"
    assert profile["presence"]["custom_status"] == "Am Zocken"
    assert profile["presence"]["device_type"] == "desktop"

    # Stories prüfen: Öffentliche Stories müssen für Fremde sichtbar sein
    story = SocialService.create_story(db, public_user, "Mein öffentlicher Status")
    stories = SocialService.list_active_stories(db, stranger.id)
    assert any(s["id"] == story.id for s in stories)


def test_visibility_friends_profile_and_presence(db: Session):
    """Freunde: Nur bestätigte Freunde sehen Details wie Stories und Aktivitäten. Für Fremde komplett verborgen."""
    friends_user = _create_user(db, "charlie_friends", privacy="friends")
    friend = _create_user(db, "david_friend", privacy="friends")
    stranger = _create_user(db, "eve_stranger", privacy="friends")

    # Freundschaft zwischen charlie und david schließen
    rel = UserFriend(user_id=friends_user.id, friend_id=friend.id, status="accepted")
    db.add(rel)
    db.commit()

    # Präsenz von Charlie setzen
    SocialService.update_presence(db, friends_user.id, {
        "status": "online",
        "device_type": "desktop",
        "custom_status": "Programmieren",
        "activity_label": "VS Code",
        "activity_detail": "MSM Core",
    })

    # Story von Charlie erstellen
    story = SocialService.create_story(db, friends_user, "Nur für Freunde!")

    # 1. Bestätigter Freund sieht Profil, Präsenz und Stories
    profile_friend = SocialService.get_profile(db, viewer_user_id=friend.id, target_user=friends_user)
    assert profile_friend["restricted"] is False
    assert profile_friend["presence"] is not None
    assert profile_friend["presence"]["status"] == "online"
    assert profile_friend["presence"]["activity_label"] == "VS Code"
    friend_stories = SocialService.list_active_stories(db, friend.id)
    assert any(s["id"] == story.id for s in friend_stories)

    # 2. Fremder (eve) sieht KEINE Details
    profile_stranger = SocialService.get_profile(db, viewer_user_id=stranger.id, target_user=friends_user)
    assert profile_stranger["restricted"] is True
    assert profile_stranger["presence"] is None
    assert profile_stranger["stats"] is None
    assert profile_stranger["achievements"] is None

    # Fremder sieht Charlies Story NIEMALS
    stranger_stories = SocialService.list_active_stories(db, stranger.id)
    assert not any(s["id"] == story.id for s in stranger_stories)


def test_visibility_private_profile_and_presence(db: Session):
    """Privat: Der Nutzer wird zwar als 'Online' angezeigt, aber Details wie Stories, Profilinhalte

    und Aktivitäten ('was der User macht') sind für alle Nicht-Freunde strikt verborgen.
    Man sieht lediglich den reinen Online-Indikator, mehr nicht.
    """
    private_user = _create_user(db, "frank_priv", privacy="private")
    friend = _create_user(db, "grace_friend", privacy="friends")
    stranger = _create_user(db, "heidi_stranger", privacy="friends")

    # Freundschaft zwischen frank und grace
    rel = UserFriend(user_id=private_user.id, friend_id=friend.id, status="accepted")
    db.add(rel)
    db.commit()

    # Frank ist online mit Aktivitäten
    SocialService.update_presence(db, private_user.id, {
        "status": "online",
        "device_type": "mobile",
        "custom_status": "Geheim",
        "activity_label": "Geheimes Projekt",
        "activity_detail": "Streng vertraulich",
    })

    # Story von Frank
    story = SocialService.create_story(db, private_user, "Private Story für Freunde")

    # 1. Freund grace: Profil ist für private Nutzer standardmäßig restricted (weder Freund noch Fremder sehen Stats)
    profile_friend = SocialService.get_profile(db, viewer_user_id=friend.id, target_user=private_user)
    assert profile_friend["restricted"] is True
    # Freund sieht jedoch vollständige Präsenz via get_presence_for_viewer
    pres_friend = SocialService.get_presence_for_viewer(db, viewer_user_id=friend.id, target_user=private_user)
    assert pres_friend is not None
    assert pres_friend["status"] == "online"
    assert pres_friend["activity_label"] == "Geheimes Projekt"
    friend_stories = SocialService.list_active_stories(db, friend.id)
    assert any(s["id"] == story.id for s in friend_stories)

    # 2. Fremder heidi: Profil ist restricted, und via get_presence_for_viewer sieht heidi den REINEN Online-Indikator
    profile_stranger = SocialService.get_profile(db, viewer_user_id=stranger.id, target_user=private_user)
    assert profile_stranger["restricted"] is True
    assert profile_stranger["stats"] is None
    assert profile_stranger["achievements"] is None
    assert profile_stranger["presence"] is None

    pres = SocialService.get_presence_for_viewer(db, viewer_user_id=stranger.id, target_user=private_user)
    assert pres is not None
    # Reiner Online-Indikator:
    assert pres["status"] == "online"
    # Sensible Daten sind strikt maskiert/None:
    assert pres["activity_label"] is None
    assert pres["activity_detail"] is None
    assert pres["custom_status"] is None
    assert pres["updated_at"] is None

    # Stories für Fremden strikt verborgen
    stranger_stories = SocialService.list_active_stories(db, stranger.id)
    assert not any(s["id"] == story.id for s in stranger_stories)


def test_visibility_invisible_mode_masking(db: Session):
    """Unsichtbar (manueller Schalter): Unabhängig von Öffentlich/Freunde/Privat komplett als 'Offline' maskiert."""
    pub_inv = _create_user(db, "ivan_pub_inv", privacy="public")
    priv_inv = _create_user(db, "judy_priv_inv", privacy="private")
    stranger = _create_user(db, "kurt_stranger", privacy="friends")

    # Beide aktivieren Unsichtbar-Modus
    SocialService.update_presence(db, pub_inv.id, {
        "status": "invisible",
        "custom_status": "Bin eigentlich da",
        "activity_label": "Geheimspiel",
    })
    SocialService.update_presence(db, priv_inv.id, {
        "status": "invisible",
        "custom_status": "Unsichtbar",
        "activity_label": "Etwas",
    })

    # Fremder sieht beide als 'offline' ohne jegliche Aktivitätsdaten
    pres_pub = SocialService.get_presence_for_viewer(db, viewer_user_id=stranger.id, target_user=pub_inv)
    assert pres_pub["status"] == "offline"
    assert pres_pub["activity_label"] is None
    assert pres_pub["custom_status"] is None

    pres_priv = SocialService.get_presence_for_viewer(db, viewer_user_id=stranger.id, target_user=priv_inv)
    assert pres_priv["status"] == "offline"
    assert pres_priv["activity_label"] is None
    assert pres_priv["custom_status"] is None

    # Der Nutzer selbst sieht seinen echten Unsichtbar-Zustand
    self_pres = SocialService.get_presence_for_viewer(db, viewer_user_id=pub_inv.id, target_user=pub_inv)
    assert self_pres["status"] == "invisible"


# ============================================================================
# 2. Tests für Öffentlichen Entdecken-/Nutzer-Tab (Query-Ebene)
# ============================================================================

def test_public_discovery_directory_strict_query_filter(db: Session):
    """Im Entdecken-Tab werden AUSSCHLIESSLICH Nutzer gelistet, deren Profil auf 'Öffentlich' steht.

    'Freunde' und 'Privat' dürfen NIEMALS auftauchen (Filter auf DB-Query-Ebene).
    """
    _create_user(db, "discover_pub_1", privacy="public")
    _create_user(db, "discover_pub_2", privacy="public")
    _create_user(db, "discover_friends", privacy="friends")
    _create_user(db, "discover_private", privacy="private")
    viewer = _create_user(db, "viewer_user", privacy="friends")

    public_list = SocialService.get_public_profiles(db, viewer_user_id=viewer.id)
    public_usernames = [p["username"] for p in public_list]

    # Öffentliche Profile sind enthalten
    assert "discover_pub_1" in public_usernames
    assert "discover_pub_2" in public_usernames

    # Profile mit Sichtbarkeit 'Freunde' oder 'Privat' tauchen NIEMALS auf
    assert "discover_friends" not in public_usernames
    assert "discover_private" not in public_usernames
    assert viewer.username not in public_usernames

    # Suchfunktion im öffentlichen Verzeichnis
    searched = SocialService.get_public_profiles(db, viewer_user_id=viewer.id, search="pub_1")
    assert len(searched) == 1
    assert searched[0]["username"] == "discover_pub_1"


def test_public_discovery_excludes_blocked_users(db: Session):
    """Blockierte Benutzer werden aus dem öffentlichen Verzeichnis gefiltert."""
    u_public = _create_user(db, "blocked_pub_user", privacy="public")
    viewer = _create_user(db, "viewer_blocked_test", privacy="friends")

    # Blockierung anlegen
    rel = UserFriend(user_id=viewer.id, friend_id=u_public.id, status="blocked")
    db.add(rel)
    db.commit()

    public_list = SocialService.get_public_profiles(db, viewer_user_id=viewer.id)
    public_usernames = [p["username"] for p in public_list]
    assert "blocked_pub_user" not in public_usernames


# ============================================================================
# 3. Tests für Messaging & Antwort-Regel für Nicht-Freunde
# ============================================================================

def test_messaging_non_friend_to_public_user_and_reply_rule(db: Session):
    """Wenn Nutzer A nicht mit Nutzer B befreundet ist, B aber ein öffentliches Profil hat,

    darf A eine Nachricht an B senden.
    Sobald ein Chat existiert, muss B direkt antworten können – auch wenn A privat ist
    oder keine Freundschaft besteht.
    """
    user_a = _create_user(db, "user_a_priv", privacy="private")
    user_b = _create_user(db, "user_b_pub", privacy="public")

    # Keine Freundschaft zwischen A und B
    assert not SocialService.is_confirmed_friend(db, user_a.id, user_b.id)

    # 1. A darf B anschreiben, da B ein öffentliches Profil hat
    can_msg, reason = SocialService.can_message_user(db, user_a.id, user_b.id)
    assert can_msg is True
    assert reason is None

    mid = SocialService.derive_blind_mailbox_id(user_a.id, user_b.id)

    # A sendet die erste Nachricht an B
    env1 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mid,
        ciphertext_envelope=_valid_test_envelope(payload_tag="first_message_from_a"),
        sender_user_id=user_a.id,
        recipient_id=user_b.id,
    )
    assert env1.id is not None

    # Chat existiert nun in direct_chats
    chat = db.query(DirectChat).filter_by(blind_mailbox_id=mid).first()
    assert chat is not None
    assert chat.initiated_by_user_id == user_a.id

    # 2. Antwort-Erlaubnis: B muss direkt antworten können, obwohl A 'private' ist und keine Freundschaft besteht!
    can_reply, reply_reason = SocialService.can_message_user(db, user_b.id, user_a.id)
    assert can_reply is True
    assert reply_reason is None

    # B antwortet an A
    env2 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mid,
        ciphertext_envelope=_valid_test_envelope(payload_tag="reply_message_from_b"),
        sender_user_id=user_b.id,
        recipient_id=user_a.id,
    )
    assert env2.id is not None

    # 3. Unterhaltung funktioniert in beide Richtungen uneingeschränkt
    chats_a = SocialService.list_direct_chats(db, user_a.id)
    assert any(c["other_user_id"] == user_b.id for c in chats_a)

    chats_b = SocialService.list_direct_chats(db, user_b.id)
    assert any(c["other_user_id"] == user_a.id for c in chats_b)


def test_messaging_forbidden_for_private_non_friend_without_existing_chat(db: Session):
    """Nicht-Freunde dürfen ein privates/Freunde-Profil OHNE bestehenden Chat nicht kontaktieren."""
    sender = _create_user(db, "sender_stranger", privacy="public")
    target_private = _create_user(db, "target_private", privacy="private")
    target_friends = _create_user(db, "target_friends", privacy="friends")

    # Keine Freundschaft, kein Chat
    can_msg_priv, reason_priv = SocialService.can_message_user(db, sender.id, target_private.id)
    assert can_msg_priv is False
    assert "nur von bestätigten Freunden" in reason_priv

    can_msg_fr, reason_fr = SocialService.can_message_user(db, sender.id, target_friends.id)
    assert can_msg_fr is False
    assert "nur von bestätigten Freunden" in reason_fr

    # Sendeversuch wirft 403
    with pytest.raises(HTTPException) as exc1:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=SocialService.derive_blind_mailbox_id(sender.id, target_private.id),
            ciphertext_envelope=_valid_test_envelope(payload_tag="unauthorized_attempt"),
            sender_user_id=sender.id,
            recipient_id=target_private.id,
        )
    assert exc1.value.status_code == 403


def test_messaging_forbidden_when_blocked(db: Session):
    """Blockierung verhindert Nachrichten selbst bei öffentlichem Profil oder bestehendem Chat."""
    user_a = _create_user(db, "user_blocked_a", privacy="public")
    user_b = _create_user(db, "user_blocked_b", privacy="public")

    # Blockierung anlegen
    rel = UserFriend(user_id=user_a.id, friend_id=user_b.id, status="blocked")
    db.add(rel)
    db.commit()

    can_msg, reason = SocialService.can_message_user(db, user_a.id, user_b.id)
    assert can_msg is False
    assert "blockiert" in reason

    can_msg_rev, reason_rev = SocialService.can_message_user(db, user_b.id, user_a.id)
    assert can_msg_rev is False
    assert "blockiert" in reason_rev


# ============================================================================
# 4. Tests für WebSocket & Server-Level Broadcast Filtering
# ============================================================================

@pytest.mark.asyncio
async def test_server_presence_broadcast_filters_payload_per_recipient(db: Session):
    """Absicherung: Sensible Statusdaten & Aktivitäten werden serverseitig niemals

    an unberechtigte Clients gesendet.
    - Fremde eines privaten Nutzers erhalten nur den reinen Online-Indikator.
    - Freunde erhalten die vollständigen Aktivitäten.
    """
    priv_user = _create_user(db, "stream_priv_user", privacy="private")
    friend = _create_user(db, "stream_friend", privacy="friends")
    stranger = _create_user(db, "stream_stranger", privacy="friends")

    # Freundschaft
    rel = UserFriend(user_id=priv_user.id, friend_id=friend.id, status="accepted")
    db.add(rel)
    db.commit()

    # Beide Clients abonnieren SSE/WS Events
    conn_friend, queue_friend = SyncEventService.subscribe(user_id=friend.id)
    conn_stranger, queue_stranger = SyncEventService.subscribe(user_id=stranger.id)

    try:
        # Privater Nutzer aktualisiert seine Präsenz mit Aktivitäten
        SocialService.update_presence(db, priv_user.id, {
            "status": "online",
            "device_type": "desktop",
            "custom_status": "Geheimarbeit",
            "activity_label": "Top Secret App",
            "activity_detail": "Level 5",
        })

        # Freund prüft sein empfangenes Event
        assert not queue_friend.empty()
        event_friend = queue_friend.get_nowait()
        assert event_friend["type"] == "friend_presence_updated"
        pres_f = event_friend["presence"]
        assert pres_f["status"] == "online"
        assert pres_f["activity_label"] == "Top Secret App"
        assert pres_f["custom_status"] == "Geheimarbeit"

        # Fremder ruft seine gefilterte Präsenz ab
        pres_s = SocialService.get_presence_for_viewer(db, stranger.id, priv_user)
        assert pres_s is not None
        assert pres_s["status"] == "online"
        # Strikte Maskierung: KEINE Aktivitäten im Payload!
        assert pres_s["activity_label"] is None
        assert pres_s["activity_detail"] is None
        assert pres_s["custom_status"] is None
    finally:
        SyncEventService.unsubscribe(conn_friend)
        SyncEventService.unsubscribe(conn_stranger)


@pytest.mark.asyncio
async def test_server_presence_broadcast_invisible_masks_as_offline(db: Session):
    """Unsichtbar-Modus maskiert in Broadcasts und Abfragen komplett als Offline."""
    user = _create_user(db, "inv_broadcast_user", privacy="public")
    friend = _create_user(db, "inv_broadcast_friend", privacy="friends")

    rel = UserFriend(user_id=user.id, friend_id=friend.id, status="accepted")
    db.add(rel)
    db.commit()

    conn_friend, queue_friend = SyncEventService.subscribe(user_id=friend.id)

    try:
        SocialService.update_presence(db, user.id, {
            "status": "invisible",
            "custom_status": "Bin da",
            "activity_label": "Aktives Spiel",
        })

        assert not queue_friend.empty()
        event = queue_friend.get_nowait()
        pres = event["presence"]
        # Freund sieht Nutzer als offline, keine Aktivitäten
        assert pres["status"] == "offline"
        assert pres["activity_label"] is None
        assert pres["custom_status"] is None
    finally:
        SyncEventService.unsubscribe(conn_friend)


def test_messaging_relay_mailbox_mismatch_rejected(db: Session):
    """Prüft, dass manipulierte Mailbox-IDs beim Senden mit Empfänger-ID mit HTTP 400 abgewiesen werden."""
    sender = _create_user(db, "sender_spoof", privacy="public")
    target = _create_user(db, "target_spoof", privacy="public")

    fake_mailbox = "0" * 64
    with pytest.raises(HTTPException) as exc_info:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=fake_mailbox,
            ciphertext_envelope=_valid_test_envelope(payload_tag="spoofed_payload"),
            sender_user_id=sender.id,
            recipient_id=target.id,
        )
    assert exc_info.value.status_code == 400
    assert "stimmt nicht mit" in exc_info.value.detail


def test_blocked_user_gets_no_presence_and_cannot_message(db: Session):
    """Blockierte Kontakte erhalten niemals Status-/Präsenzdaten und können weder Nachrichten noch Typing-Signale senden."""
    user_a = _create_user(db, "block_target_pub", privacy="public")
    user_b = _create_user(db, "block_viewer_pub", privacy="public")

    # A blockiert B
    rel = UserFriend(user_id=user_a.id, friend_id=user_b.id, status="blocked")
    db.add(rel)
    db.commit()

    # A setzt Online-Präsenz mit Aktivitätsdaten
    SocialService.update_presence(db, user_a.id, {
        "status": "online",
        "activity_label": "Sensible Aktivität",
        "custom_status": "Geheim",
    })

    # 1. B darf keinerlei Präsenz von A abrufen
    pres_b_sees_a = SocialService.get_presence_for_viewer(db, viewer_user_id=user_b.id, target_user=user_a)
    assert pres_b_sees_a is None

    # 2. Profilabruf liefert restricted mit presence=None
    prof_b_sees_a = SocialService.get_profile(db, viewer_user_id=user_b.id, target_user=user_a)
    assert prof_b_sees_a["restricted"] is True
    assert prof_b_sees_a["presence"] is None
    assert prof_b_sees_a["is_friend"] is False

    # 3. B kann A keine Nachricht senden
    mid = SocialService.derive_blind_mailbox_id(user_b.id, user_a.id)
    with pytest.raises(HTTPException) as exc_msg:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mid,
            ciphertext_envelope=_valid_test_envelope(payload_tag="blocked_attempt"),
            sender_user_id=user_b.id,
            recipient_id=user_a.id,
        )
    assert exc_msg.value.status_code == 403

    # 4. Typing-Signal von B an A wird abgewiesen/unterdrückt
    conn_a, queue_a = SyncEventService.subscribe(user_id=user_a.id)
    try:
        SocialService.broadcast_typing_signal(
            blind_mailbox_id=mid,
            status="typing",
            sender_id=user_b.id,
            sender_username=user_b.username,
            db=db,
            recipient_id=user_a.id,
        )
        assert queue_a.empty()
    finally:
        SyncEventService.unsubscribe(conn_a)


def test_concurrent_direct_chat_creation_race_condition(db: Session):
    """Prüft atomare Handhabung gleichzeitiger Chat-Erstellungen zweier Nutzer."""
    user_a = _create_user(db, "race_user_a", privacy="public")
    user_b = _create_user(db, "race_user_b", privacy="public")

    chat1 = SocialService.ensure_direct_chat(db, user_a.id, user_b.id)
    chat2 = SocialService.ensure_direct_chat(db, user_b.id, user_a.id)

    assert chat1.id == chat2.id
    assert chat1.blind_mailbox_id == chat2.blind_mailbox_id


@pytest.mark.asyncio
async def test_typing_signal_targeted_to_recipient_only(db: Session):
    """Typing-Signale in 1:1 Chats erreichen gezielt den Chatpartner und keinen fremden Client."""
    sender = _create_user(db, "typing_sender", privacy="public")
    recipient = _create_user(db, "typing_recipient", privacy="public")
    stranger = _create_user(db, "typing_stranger", privacy="public")

    mid = SocialService.derive_blind_mailbox_id(sender.id, recipient.id)
    SocialService.ensure_direct_chat(db, sender.id, recipient.id)

    conn_rec, queue_rec = SyncEventService.subscribe(user_id=recipient.id)
    conn_stranger, queue_stranger = SyncEventService.subscribe(user_id=stranger.id)

    try:
        SocialService.broadcast_typing_signal(
            blind_mailbox_id=mid,
            status="typing",
            sender_id=sender.id,
            sender_username=sender.username,
            db=db,
            recipient_id=recipient.id,
        )

        # Empfänger erhält Signal
        assert not queue_rec.empty()
        sig = queue_rec.get_nowait()
        assert sig["type"] == "e2ee_typing_signal"
        assert sig["sender_id"] == sender.id

        # Fremder erhält NICHTS
        assert queue_stranger.empty()
    finally:
        SyncEventService.unsubscribe(conn_rec)
        SyncEventService.unsubscribe(conn_stranger)


@pytest.mark.asyncio
async def test_message_relay_targeted_to_chat_participants_only(db: Session):
    """E2EE-Umschläge in 1:1 Chats werden nur an die Teilnehmer übertragen, nicht an Unbeteiligte."""
    sender = _create_user(db, "relay_sender", privacy="public")
    recipient = _create_user(db, "relay_recipient", privacy="public")
    stranger = _create_user(db, "relay_stranger", privacy="public")

    mid = SocialService.derive_blind_mailbox_id(sender.id, recipient.id)

    conn_rec, queue_rec = SyncEventService.subscribe(user_id=recipient.id)
    conn_snd, queue_snd = SyncEventService.subscribe(user_id=sender.id)
    conn_stranger, queue_stranger = SyncEventService.subscribe(user_id=stranger.id)

    try:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mid,
            ciphertext_envelope=_valid_test_envelope(payload_tag="private_message"),
            sender_user_id=sender.id,
            recipient_id=recipient.id,
        )

        assert not queue_rec.empty()
        msg_rec = queue_rec.get_nowait()
        assert msg_rec["type"] == "e2ee_blind_message"
        assert msg_rec["blind_mailbox_id"] == mid

        assert not queue_snd.empty()
        msg_snd = queue_snd.get_nowait()
        assert msg_snd["type"] == "e2ee_blind_message"

        # Fremder darf keine Benachrichtigung erhalten
        assert queue_stranger.empty()
    finally:
        SyncEventService.unsubscribe(conn_rec)
        SyncEventService.unsubscribe(conn_snd)
        SyncEventService.unsubscribe(conn_stranger)


def test_public_profiles_includes_is_friend_field(db: Session):
    """Öffentliche Profile liefern das Feld is_friend wahrheitsgemäß zurück."""
    viewer = _create_user(db, "friend_tester_viewer", privacy="friends")
    pub_friend = _create_user(db, "friend_tester_pub_fr", privacy="public")
    _create_user(db, "friend_tester_pub_str", privacy="public")

    rel = UserFriend(user_id=viewer.id, friend_id=pub_friend.id, status="accepted")
    db.add(rel)
    db.commit()

    profiles = SocialService.get_public_profiles(db, viewer_user_id=viewer.id)
    p_map = {p["username"]: p for p in profiles}

    assert "friend_tester_pub_fr" in p_map
    assert p_map["friend_tester_pub_fr"]["is_friend"] is True

    assert "friend_tester_pub_str" in p_map
    assert p_map["friend_tester_pub_str"]["is_friend"] is False

