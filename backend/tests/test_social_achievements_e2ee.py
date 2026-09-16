from __future__ import annotations

import base64
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from uuid import uuid4
from models import User, UserAchievement, UserActivityTime, UserFriend, UserPresence, E2eeBlindEnvelope, AiConversation, DirectChat, ChatGroup, ChatGroupMember
from services.achievement_service import AchievementService, ACHIEVEMENTS_CATALOG
from services.social_service import SocialService
from services.panel_settings_service import PanelSettingsService
from services.ai_action_errors import AiActionValidationError
from services.ai_proposals.lifecycle import create_proposal
from services import ai_proposal_service


def test_achievement_catalog_and_dynamic_rarity(db: Session, owner_user: User):
    """Prüft den Meilenstein-Katalog und die dynamische Seltenheitsberechnung."""
    catalog = AchievementService.get_catalog()
    assert len(catalog) == 100
    assert any(a["id"] == "starter_first_step" for a in catalog)
    assert any(a["id"] == "social_zero_knowledge" for a in catalog)
    assert any(a["id"] == "activity_hour_500" for a in catalog)

    # Schalte Meilenstein frei
    unlocked = AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step")
    assert unlocked is True

    # Zweiter Freischaltversuch darf nicht erneut anlegen (Systemweit genau einmal)
    second_unlock = AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step")
    assert second_unlock is False

    # Seltenheitsberechnung
    rarity = AchievementService.get_rarity_stats(db)
    assert "starter_first_step" in rarity
    assert rarity["starter_first_step"]["unlocked_count"] == 1
    assert "Nur von " in rarity["starter_first_step"]["rarity_text"]

    # User Achievements
    user_achs = AchievementService.get_user_achievements(db, owner_user.id)
    first_step = next(a for a in user_achs if a["id"] == "starter_first_step")
    assert first_step["unlocked"] is True
    assert first_step["unlocked_at"] is not None


def test_activity_time_tracking_and_auto_unlock(db: Session, owner_user: User):
    """Prüft die aktive Nutzungszeiterfassung und das automatische Freischalten bei Meilensteinen."""
    # 1800 Sekunden Allgemein
    res1 = AchievementService.record_activity_time(db, owner_user.id, "general", 1800)
    assert res1["total_seconds"] == 1800

    # Weitere 1850 Sekunden KI-Chat -> über 1 Stunde (3650 Sekunden)
    res2 = AchievementService.record_activity_time(db, owner_user.id, "ai_chat", 1850)
    assert res2["total_seconds"] == 3650
    assert res2["total_hours"] >= 1.0

    # activity_hour_1 muss automatisch freigeschaltet sein
    record = db.query(UserAchievement).filter_by(
        user_id=owner_user.id, achievement_id="activity_hour_1"
    ).first()
    assert record is not None


def test_friends_workflow_and_handshake(db: Session):
    """Prüft Freundschaftsanfragen, Annahme, Ablehnung und Meilenstein 'social_handshake'."""
    u1 = User(username="alice", password_hash="hash1", is_active=True)
    u2 = User(username="bob", password_hash="hash2", is_active=True)
    db.add_all([u1, u2])
    db.commit()

    # Alice sendet Anfrage an Bob
    req_res = SocialService.send_friend_request(db, u1.id, "bob")
    assert req_res["status"] == "pending"

    # Bob sieht eingehende Anfrage
    requests = SocialService.get_requests(db, u2.id)
    assert len(requests["incoming"]) == 1
    assert requests["incoming"][0]["username"] == "alice"

    # Bob nimmt an
    acc_res = SocialService.accept_friend_request(db, u2.id, requests["incoming"][0]["id"])
    assert acc_res["status"] == "accepted"

    # Beide sind nun Freunde
    assert SocialService.is_confirmed_friend(db, u1.id, u2.id) is True
    alice_friends = SocialService.get_friends(db, u1.id)
    assert len(alice_friends) == 1
    assert alice_friends[0]["username"] == "bob"

    # Achievement 'social_handshake' muss für beide freigeschaltet sein
    assert db.query(UserAchievement).filter_by(user_id=u1.id, achievement_id="social_handshake").first() is not None
    assert db.query(UserAchievement).filter_by(user_id=u2.id, achievement_id="social_handshake").first() is not None


def test_privacy_three_tier_model(db: Session):
    """Prüft das 3-Stufen-Modell (private, friends, public)."""
    owner = User(username="charlie", password_hash="hash3", is_active=True, social_privacy="private")
    friend = User(username="dave", password_hash="hash4", is_active=True)
    stranger = User(username="eve", password_hash="hash5", is_active=True)
    db.add_all([owner, friend, stranger])
    db.commit()

    # Freundschaft zwischen charlie und dave
    SocialService.send_friend_request(db, owner.id, "dave")
    req = db.query(UserFriend).filter_by(user_id=owner.id, friend_id=friend.id).first()
    req.status = "accepted"
    db.commit()

    # 1. Privat: Weder Freund noch Fremder dürfen Details sehen
    prof_friend = SocialService.get_profile(db, friend.id, owner)
    assert prof_friend["restricted"] is True
    assert prof_friend["stats"] is None
    assert prof_friend["presence"] is None

    prof_stranger = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger["restricted"] is True
    assert prof_stranger["presence"] is None

    prof_self = SocialService.get_profile(db, owner.id, owner)
    assert prof_self["restricted"] is False
    assert prof_self["stats"] is not None
    assert prof_self["presence"] is not None

    # 2. Friends: Freund darf sehen, Fremder nicht
    SocialService.update_privacy(db, owner.id, "friends")
    prof_friend_2 = SocialService.get_profile(db, friend.id, owner)
    assert prof_friend_2["restricted"] is False
    assert prof_friend_2["stats"] is not None
    assert prof_friend_2["presence"] is not None

    prof_stranger_2 = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger_2["restricted"] is True
    assert prof_stranger_2["presence"] is None  # Kein Presence-Leak an Fremde!

    # 3. Public: Jeder darf sehen
    SocialService.update_privacy(db, owner.id, "public")
    prof_stranger_3 = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger_3["restricted"] is False
    assert prof_stranger_3["stats"] is not None
    assert prof_stranger_3["presence"] is not None


def test_operator_master_toggle_disables_social(db: Session, client: TestClient, owner_user: User):
    """Prüft den globalen Betreiber-Schalter (social_enabled)."""
    # Deaktiviere Social-System
    PanelSettingsService.set("social_enabled", "false", db)

    assert SocialService.is_social_enabled(db) is False

    with pytest.raises(Exception) as exc_info:
        SocialService.assert_social_enabled(db)
    assert "deaktiviert" in str(exc_info.value.detail)

    # Re-aktiviere
    PanelSettingsService.set("social_enabled", "true", db)
    assert SocialService.is_social_enabled(db) is True


def test_e2ee_zero_knowledge_blind_relay(db: Session):
    """Prüft, dass der Server als blinde Relais-Mailbox ohne Nutzerverknüpfung fungiert."""
    blind_mailbox = "a" * 64
    import base64
    b64_ct = base64.b64encode(b"N" * 12 + b"test-ciphertext-blob" + b"T" * 16).decode("ascii")
    envelope = f"sv-e2ee-v1:{b64_ct}"

    relayed = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=blind_mailbox,
        ciphertext_envelope=envelope,
    )
    assert relayed.id is not None
    assert relayed.blind_mailbox_id == blind_mailbox
    assert relayed.ciphertext_envelope == envelope

    # Abruf aus der blinden Mailbox
    messages = SocialService.get_blind_envelopes(db, blind_mailbox_id=blind_mailbox)
    assert len(messages) == 1
    assert messages[0].ciphertext_envelope == envelope


def test_ai_tool_hard_friend_binding_and_zero_leakage(db: Session, owner_user: User):
    """Prüft die harte Freundeslisten-Bindung des KI-Werkzeugs propose_message_friend."""
    friend = User(username="frank", password_hash="hashf", is_active=True)
    stranger = User(username="mallory", password_hash="hashm", is_active=True)
    db.add_all([friend, stranger])
    db.commit()

    # Frank als Freund annehmen
    f_rel = UserFriend(user_id=owner_user.id, friend_id=friend.id, status="accepted")
    db.add(f_rel)
    db.commit()

    conv = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Test Chat", kind="primary")
    db.add(conv)
    db.commit()

    # 1. Nachricht an Fremden (mallory) MUSS mit Sicherheitsfehler scheitern
    with pytest.raises(AiActionValidationError) as exc:
        create_proposal(
            db,
            user=owner_user,
            conversation=conv,
            correlation_id=str(uuid4()),
            tool_name="propose_message_friend",
            arguments={
                "friend_username": "mallory",
                "message_text": "Hallo Fremder!",
                "rationale": "Kontaktaufnahme",
            },
        )
    assert "kein bestätigter Freund" in str(exc.value)

    # 2. Nachricht an bestätigten Freund (frank) gelingt als Vorschlag
    proposal = create_proposal(
        db,
        user=owner_user,
        conversation=conv,
        correlation_id=str(uuid4()),
        tool_name="propose_message_friend",
        arguments={
            "friend_username": "frank",
            "message_text": "Treffen wir uns auf dem Server?",
            "rationale": "Server-Einladung",
        },
    )
    assert proposal is not None
    assert proposal.tool_name == "propose_message_friend"

    # Bestätigen und Ausführen des Vorschlags
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=owner_user)
    assert token is not None

    exec_prop, result = ai_proposal_service.execute_proposal(
        db,
        proposal_id=proposal.id,
        user=owner_user,
        confirmation_token=token,
    )
    assert exec_prop.status == "succeeded"

    # Zero-Knowledge Verifikation: Eine blinde Mailbox wurde angelegt
    envelopes = db.query(E2eeBlindEnvelope).all()
    assert len(envelopes) >= 1
    # Das Achievement 'social_zero_knowledge' wurde freigeschaltet
    ach = db.query(UserAchievement).filter_by(user_id=owner_user.id, achievement_id="social_zero_knowledge").first()
    assert ach is not None

    # Entschlüsselungsprüfung des DIS-Umschlags
    import base64
    import json
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    env = envelopes[-1]
    assert env.ciphertext_envelope.startswith("sv-e2ee-v1:")
    ct_b64 = env.ciphertext_envelope[len("sv-e2ee-v1:"):]
    raw_bytes = base64.b64decode(ct_b64)
    iv = raw_bytes[:12]
    ciphertext_and_tag = raw_bytes[12:]
    ids = sorted([owner_user.id, friend.id])
    key_bytes = hashlib.sha256(f"msm:dm:key:{ids[0]}:{ids[1]}".encode("utf-8")).digest()
    aad = f"msm:dm:aad:{ids[0]}:{ids[1]}".encode("utf-8")
    aesgcm = AESGCM(key_bytes)
    decrypted_bytes = aesgcm.decrypt(iv, ciphertext_and_tag, aad)
    msg_obj = json.loads(decrypted_bytes.decode("utf-8"))
    assert msg_obj["sender_id"] == owner_user.id
    assert msg_obj["text"] == "Treffen wir uns auf dem Server?"
    assert "timestamp" in msg_obj


def test_presence_offline_timeout(db: Session, owner_user: User):
    """Prüft, dass Präsenz nach 120s Inaktivität automatisch als offline gewertet wird."""
    from datetime import datetime, timezone, timedelta
    from models import UserPresence

    # 1. Frische Präsenz -> online
    SocialService.update_presence(db, owner_user.id, {"status": "online", "device_type": "desktop"})
    pres = SocialService.get_presence(db, owner_user.id)
    assert pres["status"] == "online"
    assert pres["device_type"] == "desktop"

    # 2. Veralteter Zeitstempel (200 Sekunden in der Vergangenheit) -> offline
    db_pres = db.query(UserPresence).filter_by(user_id=owner_user.id).first()
    db_pres.updated_at = datetime.now(timezone.utc) - timedelta(seconds=200)
    db.commit()

    pres_after = SocialService.get_presence(db, owner_user.id)
    assert pres_after["status"] == "offline"
    # device_type bleibt erhalten
    assert pres_after["device_type"] == "desktop"

    # 3. Fehlender Zeitstempel (updated_at = None) -> offline
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = UserPresence(
        user_id=owner_user.id, status="online", device_type="desktop", updated_at=None
    )
    pres_none = SocialService.get_presence(mock_db, owner_user.id)
    assert pres_none["status"] == "offline"
    assert pres_none["device_type"] == "desktop"

    # 4. Nicht existierender Benutzer -> offline
    missing_pres = SocialService.get_presence(db, user_id=999999)
    assert missing_pres["status"] == "offline"
    assert missing_pres["updated_at"] is None


def test_chat_group_create_join_invite(db: Session, owner_user: User, regular_user: User) -> None:
    # 1. Gruppe erstellen
    group = SocialService.create_group(
        db,
        user=owner_user,
        name="Singra Vault Community",
        description="Offizielle Community-Gruppe",
    )
    assert group.id is not None
    assert group.name == "Singra Vault Community"
    assert len(group.invite_code) >= 16
    assert group.owner_user_id == owner_user.id

    # 2. Öffentliche Einladung abrufen (ohne Auth)
    invite_info = SocialService.get_group_by_invite_code(db, group.invite_code)
    assert invite_info.id == group.id
    assert invite_info.name == "Singra Vault Community"

    # 3. Zweiter Nutzer tritt über Einladungslink bei
    joined_group = SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)
    assert joined_group.id == group.id

    # Gruppen auflisten
    owner_groups = SocialService.list_user_groups(db, owner_user.id)
    assert len(owner_groups) == 1
    assert owner_groups[0]["member_count"] == 2
    assert owner_groups[0]["role"] == "owner"

    regular_groups = SocialService.list_user_groups(db, regular_user.id)
    assert len(regular_groups) == 1
    assert regular_groups[0]["role"] == "member"

    # 4. Blinder E2EE Relay
    blind_mailbox = "b" * 64
    b64_team = base64.b64encode(b"N" * 12 + b"team-ciphertext-payload" + b"T" * 16).decode("ascii")
    env = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=blind_mailbox,
        ciphertext_envelope=f"sv-e2ee-team-v1:{b64_team}",
    )
    assert env.id is not None
    assert env.blind_mailbox_id == blind_mailbox

    # 5. Zweite Gruppe erstellen (unbegrenzt)
    group2 = SocialService.create_group(
        db,
        user=owner_user,
        name="Zweite Gruppe",
    )
    all_groups = SocialService.list_user_groups(db, owner_user.id)
    assert len(all_groups) == 2

    # 6. Nicht-Eigentümer darf nicht löschen
    with pytest.raises(Exception) as excinfo:
        SocialService.delete_group(db, regular_user, group.id)
    assert "403" in str(excinfo.value) or "Eigentümer" in str(excinfo.value)

    # 7. Eigentümer löscht Gruppe
    SocialService.delete_group(db, owner_user, group.id)
    after_delete = SocialService.list_user_groups(db, owner_user.id)
    assert len(after_delete) == 1
    assert after_delete[0]["id"] == group2.id


def test_chat_stories_creation_and_expiration(db: Session, owner_user: User, regular_user: User) -> None:
    # 1. Beiden Nutzern Freundschaft geben
    req = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, req["id"])

    # 2. Story anlegen
    story = SocialService.create_story(
        db,
        user=owner_user,
        content="Guten Morgen Panel!",
        media_url=None,
        background="gradient-1",
    )
    assert story.id is not None
    assert story.expires_at > story.created_at

    # 3. Freund sieht die aktive Story
    stories = SocialService.list_active_stories(db, regular_user.id)
    assert len(stories) >= 1
    my_story = next(s for s in stories if s["id"] == story.id)
    assert my_story["content"] == "Guten Morgen Panel!"
    assert my_story["username"] == owner_user.username
    assert my_story["is_self"] is False

    # 4. Ersteller sieht sie mit is_self=True
    owner_stories = SocialService.list_active_stories(db, owner_user.id)
    own = next(s for s in owner_stories if s["id"] == story.id)
    assert own["is_self"] is True

    # 5. Story löschen
    SocialService.delete_story(db, owner_user, story.id)
    assert not any(s["id"] == story.id for s in SocialService.list_active_stories(db, owner_user.id))


def test_chat_group_roles_and_permissions(db: Session, owner_user: User, regular_user: User) -> None:
    # 1. Gruppe erstellen
    group = SocialService.create_group(
        db,
        user=owner_user,
        name="Security & Privacy Guild",
        description="Gilden-Chat",
    )
    SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)

    # 2. Rolle von regular_user zu Moderator befördern mit Rechten
    updated = SocialService.update_member_role_permissions(
        db,
        group_id=group.id,
        target_user_id=regular_user.id,
        role="moderator",
        permissions="send_messages,delete_messages,kick_members",
        caller=owner_user,
    )
    assert updated["role"] == "moderator"
    assert "kick_members" in updated["permissions"]

    # 3. Standard-Gruppenrechte für @everyone anpassen
    updated_grp = SocialService.update_group_default_permissions(
        db,
        group_id=group.id,
        default_permissions="send_messages,invite_members,delete_messages",
        caller=owner_user,
    )
    assert "delete_messages" in updated_grp.default_permissions

    # 4. Nicht-Admin darf keine Rollen ändern
    with pytest.raises(Exception) as exc:
        SocialService.update_member_role_permissions(
            db,
            group_id=group.id,
            target_user_id=owner_user.id,
            role="member",
            permissions=None,
            caller=regular_user,
        )
    assert "403" in str(exc.value)

    # 5. Moderator mit kick_members darf Mitglied kicken (wir fügen einen 3. Nutzer hinzu)
    user3 = User(
        username="third_user",
        email="third@example.com",
        password_hash="hashed",
        is_active=True,
    )
    db.add(user3)
    db.commit()
    SocialService.join_group_by_invite_code(db, user3, group.invite_code)

    # Regular_user kickt user3
    SocialService.kick_group_member(
        db,
        group_id=group.id,
        target_user_id=user3.id,
        caller=regular_user,
    )

    members = SocialService.list_user_groups(db, owner_user.id)[0]["members"]
    assert not any(m["user_id"] == user3.id for m in members)


def test_public_profiles_discovery(db: Session, owner_user: User, regular_user: User) -> None:
    """Prüft die Auffindbarkeit von Benutzern mit öffentlicher Privatsphäre."""
    owner_user.social_privacy = "public"
    regular_user.social_privacy = "friends"

    user_pub = User(
        username="public_sam",
        email="sam@example.com",
        password_hash="hash_sam",
        is_active=True,
        social_privacy="public",
    )
    db.add(user_pub)
    db.commit()

    # Aus Sicht von owner_user
    profiles = SocialService.get_public_profiles(db, viewer_user_id=owner_user.id)
    usernames = [p["username"] for p in profiles]
    assert "public_sam" in usernames
    assert regular_user.username not in usernames  # Da nur friends
    assert owner_user.username not in usernames   # Da viewer_id gefiltert

    # Suche mit Filter
    filtered = SocialService.get_public_profiles(db, viewer_user_id=owner_user.id, search="sam")
    assert len(filtered) == 1
    assert filtered[0]["username"] == "public_sam"


def test_e2ee_typing_signal(owner_user: User) -> None:
    """Prueft das fluechtige Senden von Typing- und Recording-Signalen ohne DB-Persistenz."""
    mailbox_id = "test-blind-mailbox-123"
    SocialService.broadcast_typing_signal(
        blind_mailbox_id=mailbox_id,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
    )
    SocialService.broadcast_typing_signal(
        blind_mailbox_id=mailbox_id,
        status="recording",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
    )


def test_ai_messenger_search_and_e2ee(db: Session, owner_user: User) -> None:
    """Prüft die neuen KI-Werkzeuge: search_messenger_contacts, search_messenger_groups,
    propose_message_contact und propose_message_group mit Zero-Knowledge E2EE Relay."""
    from services.ai_action_service import _execute_global_read_tool
    from models import ChatGroup, ChatGroupMember

    # 1. Kontakt und Gruppe anlegen
    alice = User(username="alice_wonder", password_hash="hasha", is_active=True)
    db.add(alice)
    db.commit()

    # Alice als Freund hinzufügen
    f_rel = UserFriend(user_id=owner_user.id, friend_id=alice.id, status="accepted")
    db.add(f_rel)

    # Gruppe anlegen
    group = ChatGroup(name="Gamer Community", owner_user_id=owner_user.id, invite_code="testinv123")
    db.add(group)
    db.commit()
    member1 = ChatGroupMember(group_id=group.id, user_id=owner_user.id, role="admin")
    member2 = ChatGroupMember(group_id=group.id, user_id=alice.id, role="member")
    db.add_all([member1, member2])
    db.commit()

    # 2. search_messenger_contacts testen
    res_contacts = _execute_global_read_tool(
        db, user=owner_user, tool_name="search_messenger_contacts", arguments={"query": "alice"}
    )
    assert res_contacts["count"] >= 1
    found_names = [c["username"] for c in res_contacts["contacts"]]
    assert "alice_wonder" in found_names

    # 3. search_messenger_groups testen
    res_groups = _execute_global_read_tool(
        db, user=owner_user, tool_name="search_messenger_groups", arguments={"query": "Gamer"}
    )
    assert res_groups["count"] >= 1
    assert any(g["name"] == "Gamer Community" for g in res_groups["groups"])

    # 4. propose_message_contact vorschlagen und ausführen
    conv = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Test Chat", kind="primary")
    db.add(conv)
    db.commit()

    proposal_dm = create_proposal(
        db,
        user=owner_user,
        conversation=conv,
        correlation_id=str(uuid4()),
        tool_name="propose_message_contact",
        arguments={
            "recipient_username": "alice_wonder",
            "message_text": "Alles Gute zum Geburtstag!",
            "rationale": "Geburtstagsglückwunsch",
        },
    )
    assert proposal_dm.tool_name == "propose_message_contact"
    _, token_dm = ai_proposal_service.confirm_proposal(db, proposal_id=proposal_dm.id, user=owner_user)
    exec_prop_dm, _ = ai_proposal_service.execute_proposal(
        db,
        proposal_id=proposal_dm.id,
        user=owner_user,
        confirmation_token=token_dm,
    )
    assert exec_prop_dm.status == "succeeded"

    # 5. propose_message_group vorschlagen und ausführen
    proposal_grp = create_proposal(
        db,
        user=owner_user,
        conversation=conv,
        correlation_id=str(uuid4()),
        tool_name="propose_message_group",
        arguments={
            "group_name": "Gamer Community",
            "message_text": "Hallo zusammen in der Gruppe!",
            "rationale": "Gruppenankündigung",
        },
    )
    assert proposal_grp.tool_name == "propose_message_group"
    _, token_grp = ai_proposal_service.confirm_proposal(db, proposal_id=proposal_grp.id, user=owner_user)
    exec_prop_grp, _ = ai_proposal_service.execute_proposal(
        db,
        proposal_id=proposal_grp.id,
        user=owner_user,
        confirmation_token=token_grp,
    )
    assert exec_prop_grp.status == "succeeded"

    # 6. Verifiziere E2EE Blind Envelopes in der DB
    envelopes = db.query(E2eeBlindEnvelope).all()
    assert len(envelopes) >= 2
    wire_types = {e.ciphertext_envelope[:15] for e in envelopes}
    assert any("sv-e2ee-v1:" in wt for wt in wire_types)
    assert any("sv-e2ee-group-v" in wt for wt in wire_types)


def test_e2ee_security_replay_attack_prevention(db: Session, owner_user: User):
    """Prüft die Replay-Attack-Erkennung: identische Ciphertexte dürfen weder in derselben noch in fremden Mailboxen wiederholt werden."""
    import base64
    import os
    from fastapi import HTTPException

    valid_ct = base64.b64encode(os.urandom(12) + b"ciphertext-bytes-here" + os.urandom(16)).decode("ascii")
    envelope = f"sv-e2ee-v1:{valid_ct}"
    box_a = "1" * 64
    box_b = "2" * 64

    # 1. Erster Versand ist frisch und erfolgreich
    relayed = SocialService.relay_blind_envelope(db, blind_mailbox_id=box_a, ciphertext_envelope=envelope)
    assert relayed.id is not None

    # 2. Replay-Versuch in derselben Mailbox -> 409 Conflict
    with pytest.raises(HTTPException) as exc_same:
        SocialService.relay_blind_envelope(db, blind_mailbox_id=box_a, ciphertext_envelope=envelope)
    assert exc_same.value.status_code == 409
    assert "Replay-Angriff" in exc_same.value.detail

    # 3. Cross-Mailbox Replay-Versuch in fremder Mailbox -> ebenfalls 409 Conflict
    with pytest.raises(HTTPException) as exc_cross:
        SocialService.relay_blind_envelope(db, blind_mailbox_id=box_b, ciphertext_envelope=envelope)
    assert exc_cross.value.status_code == 409
    assert "Replay-Angriff" in exc_cross.value.detail


def test_e2ee_security_envelope_format_and_tampering(db: Session):
    """Prüft die Validierung von Umschlägen: Plaintext-Leaks, Null-IVs und manipulierte Formate werden abgewiesen."""
    import base64
    import json
    from fastapi import HTTPException
    from schemas.social import validate_e2ee_envelope_format

    # 1. Plaintext-Payload-Leak
    leaked_payload = json.dumps({"text": "Klartext-Geheimnis", "sender_id": 1})
    with pytest.raises(ValueError) as exc_leak:
        validate_e2ee_envelope_format(f"sv-e2ee-v1:{leaked_payload}")
    assert "Sicherheitsverletzung" in str(exc_leak.value)

    # 2. Unbekanntes oder fehlendes Prefix
    with pytest.raises(ValueError) as exc_prefix:
        validate_e2ee_envelope_format("plain-message-without-dis-prefix")
    assert "Ungültiges E2EE-Umschlagformat" in str(exc_prefix.value)

    # 3. Schwacher / ungültiger Null-IV (12 Null-Bytes)
    zero_iv_raw = b"\x00" * 12 + b"ciphertext-bytes-here" + b"\x01" * 16
    zero_iv_b64 = base64.b64encode(zero_iv_raw).decode("ascii")
    with pytest.raises(ValueError) as exc_iv:
        validate_e2ee_envelope_format(f"sv-e2ee-v1:{zero_iv_b64}")
    assert "Null-IV" in str(exc_iv.value)

    # 4. Steuerzeichen im Umschlag
    with pytest.raises(ValueError) as exc_ctrl:
        validate_e2ee_envelope_format("sv-e2ee-v1:payload\nwith\rnewlines")
    assert "Steuerzeichen" in str(exc_ctrl.value)

    # 5. Zu kurzer Ciphertext (< 38 Zeichen / 28 Bytes)
    short_ct = base64.b64encode(b"\x01" * 10).decode("ascii")
    with pytest.raises(ValueError) as exc_short:
        validate_e2ee_envelope_format(f"sv-e2ee-v1:{short_ct}")
    assert "zu kurz" in str(exc_short.value).lower()

    # 6. Ratchet-Umschlag (sv-e2ee-ratchet-v1:) gültig und ungültig
    valid_bytes = b"\x01" * 12 + b"ratchet-payload-ciphertext" + b"\x02" * 16
    valid_b64 = base64.b64encode(valid_bytes).decode("ascii")
    valid_ratchet = f"sv-e2ee-ratchet-v1:0.{valid_b64}"
    validate_e2ee_envelope_format(valid_ratchet)  # darf keine Exception werfen

    # Ungültige Epoche im Ratchet-Umschlag
    with pytest.raises(ValueError) as exc_ratchet_ep:
        validate_e2ee_envelope_format(f"sv-e2ee-ratchet-v1:invalid.{valid_b64}")
    assert "Epoche" in str(exc_ratchet_ep.value)


def test_e2ee_security_public_key_validation(db: Session, owner_user: User):
    """Prüft die Validierung von Public Keys: Private Parameter, zu kurze Schlüssel und falsche Typen werden abgewiesen."""
    import json
    from fastapi import HTTPException
    from schemas.social import validate_rsa_public_key_jwk

    # 1. Privater Parameter 'd' im Public Key -> Sicherheitsverletzung
    leak_d = json.dumps({
        "kty": "RSA",
        "n": "A" * 350,
        "e": "AQAB",
        "d": "private-key-exponent-leak",
    })
    with pytest.raises(ValueError) as exc_d:
        validate_rsa_public_key_jwk(leak_d)
    assert "Sicherheitsverletzung" in str(exc_d.value)
    assert "(d)" in str(exc_d.value)

    # 2. RFC 7517 private Parameter 'dmp1' / 'dmq1' -> abgewiesen
    leak_crt = json.dumps({
        "kty": "RSA",
        "n": "A" * 350,
        "e": "AQAB",
        "dmp1": "crt-exponent",
    })
    with pytest.raises(ValueError) as exc_crt:
        validate_rsa_public_key_jwk(leak_crt)
    assert "Sicherheitsverletzung" in str(exc_crt.value)

    # 3. Zu kleiner Modulus (< 2048 Bit) -> abgewiesen
    weak_key = json.dumps({
        "kty": "RSA",
        "n": "A" * 100,
        "e": "AQAB",
    })
    with pytest.raises(ValueError) as exc_weak:
        validate_rsa_public_key_jwk(weak_key)
    assert "Unsichere Schlüssellänge" in str(exc_weak.value)

    # 4. Nicht-RSA Schlüssel (z. B. oct) -> abgewiesen
    oct_key = json.dumps({
        "kty": "oct",
        "k": "shared-secret",
    })
    with pytest.raises(ValueError) as exc_oct:
        validate_rsa_public_key_jwk(oct_key)
    assert "Ungültiger Schlüsseltyp" in str(exc_oct.value)

    # 5. Algorithm Confusion: alg='none' oder Signatur-Algorithmen
    alg_none = json.dumps({
        "kty": "RSA",
        "n": "A" * 350,
        "e": "AQAB",
        "alg": "none",
    })
    with pytest.raises(ValueError) as exc_none:
        validate_rsa_public_key_jwk(alg_none)
    assert "Sicherheitsverletzung" in str(exc_none.value)
    assert "none" in str(exc_none.value)

    # 6. Unzulässiger Key-Einsatz: use='sig' oder key_ops mit signieren
    sig_use = json.dumps({
        "kty": "RSA",
        "n": "A" * 350,
        "e": "AQAB",
        "use": "sig",
    })
    with pytest.raises(ValueError) as exc_sig:
        validate_rsa_public_key_jwk(sig_use)
    assert "Schlüsselverwendung" in str(exc_sig.value)

    # 7. Speichern über SocialService blockiert fehlerhafte Keys mit HTTP 400
    with pytest.raises(HTTPException) as exc_svc:
        SocialService.save_e2ee_public_key(db, owner_user.id, leak_d)
    assert exc_svc.value.status_code == 400




# --- Kontoweiter Schlüsselbund (ersetzt den Schlüssel pro Gerät) ---


def _gueltiger_public_key(kennung: str = "A") -> str:
    """Ein formal gültiger RSA-OAEP-Public-Key im JWK-Format."""
    import json
    return json.dumps({"kty": "RSA", "n": kennung * 350, "e": "AQAB", "alg": "RSA-OAEP"})


def _gueltiger_schluesselbund(fuellung: bytes = b"verpackter-schluesselbund-inhalt") -> str:
    """Ein Umschlag mit Salt, IV und genug Bytes für den AEAD-Tag."""
    rohdaten = b"\x11" * 16 + b"\x22" * 12 + fuellung + b"\x33" * 16
    return "sv-e2ee-keyring-v1:" + base64.b64encode(rohdaten).decode("ascii")


def test_schluesselbund_speichern_und_lesen(db: Session, owner_user: User):
    """Der Bund wird zusammen mit dem Public Key abgelegt und die Version wächst."""
    vorher = SocialService.get_e2ee_keyring(db, owner_user.id)
    assert vorher["wrapped_keyring"] is None
    assert vorher["version"] == 0

    ergebnis = SocialService.save_e2ee_keyring(
        db,
        owner_user.id,
        wrapped_keyring=_gueltiger_schluesselbund(),
        public_key=_gueltiger_public_key(),
        expected_version=0,
    )
    assert ergebnis["version"] == 1

    nachher = SocialService.get_e2ee_keyring(db, owner_user.id)
    assert nachher["wrapped_keyring"] == _gueltiger_schluesselbund()
    assert nachher["public_key"] == _gueltiger_public_key()
    assert nachher["version"] == 1


def test_schluesselbund_veraltete_version_wird_abgewiesen(db: Session, owner_user: User):
    """Die Schranke gegen verlorene Rettungen: der zweite Schreibvorgang fällt auf.

    Zwei Geräte holen denselben Stand, adoptieren je ihren alten
    Gerätesschlüssel und schreiben zurück. Ohne diese Prüfung gewänne der
    letzte und der gerettete Schlüssel des anderen wäre weg.
    """
    from fastapi import HTTPException

    erster = _gueltiger_schluesselbund(b"bund-von-geraet-eins-mit-altschluessel")
    SocialService.save_e2ee_keyring(
        db,
        owner_user.id,
        wrapped_keyring=erster,
        public_key=_gueltiger_public_key(),
        expected_version=0,
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.save_e2ee_keyring(
            db,
            owner_user.id,
            wrapped_keyring=_gueltiger_schluesselbund(b"bund-von-geraet-zwei-ueberschreibt"),
            public_key=_gueltiger_public_key("B"),
            expected_version=0,
        )
    assert fehler.value.status_code == 409

    # Der abgewiesene Schreibvorgang hat nichts verändert.
    stand = SocialService.get_e2ee_keyring(db, owner_user.id)
    assert stand["wrapped_keyring"] == erster
    assert stand["public_key"] == _gueltiger_public_key()
    assert stand["version"] == 1


def test_schluesselbund_mit_klartext_schluessel_wird_abgewiesen(db: Session, owner_user: User):
    """Der Server kann nicht beweisen, dass der Bund verschlüsselt ist — aber das Naheliegende ausschließen."""
    import json
    from fastapi import HTTPException
    from schemas.social import validate_e2ee_keyring_envelope

    klartext = "sv-e2ee-keyring-v1:" + json.dumps(
        {"v": 1, "account": {"privateKeyJwk": "{\"kty\":\"RSA\",\"d\":\"geheim\"}"}}
    )
    with pytest.raises(ValueError) as fehler:
        validate_e2ee_keyring_envelope(klartext)
    assert "Sicherheitsverletzung" in str(fehler.value)

    with pytest.raises(HTTPException) as dienst_fehler:
        SocialService.save_e2ee_keyring(
            db,
            owner_user.id,
            wrapped_keyring=klartext,
            public_key=_gueltiger_public_key(),
            expected_version=0,
        )
    assert dienst_fehler.value.status_code == 400


def test_schluesselbund_umschlagpruefung(db: Session):
    """Falsches Präfix, Null-Salt und zu kurze Umschläge kommen nicht durch."""
    from schemas.social import validate_e2ee_keyring_envelope

    with pytest.raises(ValueError) as ohne_praefix:
        validate_e2ee_keyring_envelope(base64.b64encode(b"\x11" * 60).decode("ascii"))
    assert "Präfix" in str(ohne_praefix.value)

    zu_kurz = "sv-e2ee-keyring-v1:" + base64.b64encode(b"\x11" * 20).decode("ascii")
    with pytest.raises(ValueError) as kurz:
        validate_e2ee_keyring_envelope(zu_kurz)
    assert "zu kurz" in str(kurz.value)

    null_salt = "sv-e2ee-keyring-v1:" + base64.b64encode(
        b"\x00" * 16 + b"\x22" * 12 + b"inhalt" + b"\x33" * 16
    ).decode("ascii")
    with pytest.raises(ValueError) as salt:
        validate_e2ee_keyring_envelope(null_salt)
    assert "Null-Salt" in str(salt.value)

    # Ein formal korrekter Umschlag darf nicht anschlagen.
    validate_e2ee_keyring_envelope(_gueltiger_schluesselbund())


def test_alter_public_key_weg_gesperrt_sobald_bund_existiert(db: Session, owner_user: User):
    """Der Riegel gegen die Ursache des Datenverlusts.

    Ein noch nicht aktualisierter Tab, der den Bund nicht kennt, kennt auch den
    privaten Teil nicht. Dürfte er den Public Key überschreiben, wäre jede
    darauf folgende Nachricht für den Benutzer unlesbar — genau so ging der
    Verlauf verloren.
    """
    from fastapi import HTTPException

    # Ohne Bund ist der alte Weg erlaubt (Erstkontakt, Altclients).
    SocialService.save_e2ee_public_key(db, owner_user.id, _gueltiger_public_key())
    assert SocialService.get_e2ee_public_key(db, owner_user.id) == _gueltiger_public_key()

    SocialService.save_e2ee_keyring(
        db,
        owner_user.id,
        wrapped_keyring=_gueltiger_schluesselbund(),
        public_key=_gueltiger_public_key(),
        expected_version=0,
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.save_e2ee_public_key(db, owner_user.id, _gueltiger_public_key("B"))
    assert fehler.value.status_code == 409

    # Der Kontoschlüssel steht unverändert.
    assert SocialService.get_e2ee_public_key(db, owner_user.id) == _gueltiger_public_key()


def test_schluesselbund_route_liefert_nur_den_eigenen(client: TestClient, db: Session, owner_user: User):
    """Es gibt keinen Parameter, über den ein fremder Bund anzufordern wäre."""
    from main import app
    from routers.auth import get_current_user

    fremder = User(
        username="bund-fremder",
        email="bund-fremder@test.de",
        password_hash="x",
        is_active=True,
    )
    db.add(fremder)
    db.commit()
    db.refresh(fremder)

    fremder_bund = _gueltiger_schluesselbund(b"gehoert-dem-fremden-konto-allein")
    SocialService.save_e2ee_keyring(
        db,
        fremder.id,
        wrapped_keyring=fremder_bund,
        public_key=_gueltiger_public_key("C"),
        expected_version=0,
    )

    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        antwort = client.get("/api/social/e2ee/keyring")
        assert antwort.status_code == 200
        # Der Eigentümer hat keinen Bund; der des Fremden taucht nirgends auf.
        assert antwort.json()["wrapped_keyring"] is None
        assert fremder_bund not in antwort.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Milestone 1: E2EE Mailbox Sync & Automated Key Provisioning Tests ──


def test_e2ee_sync_empty_state(client: TestClient, db: Session, owner_user: User):
    """GET /api/social/e2ee/sync returns empty list for user without messages."""
    from main import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        res = client.get("/api/social/e2ee/sync")
        assert res.status_code == 200
        data = res.json()
        assert "mailboxes" in data
        assert data["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_sync_unauthenticated_and_disabled(client: TestClient, db: Session, owner_user: User):
    """Sync endpoint enforces authentication and respects operator toggle."""
    from main import app
    from dependencies import get_current_user

    # 1. Unauthenticated request
    res_unauth = client.get("/api/social/e2ee/sync")
    assert res_unauth.status_code in (401, 403)

    # 2. Authenticated but social system disabled globally
    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        PanelSettingsService.set("social_enabled", "false", db)
        res_disabled = client.get("/api/social/e2ee/sync")
        assert res_disabled.status_code == 403
        assert "deaktiviert" in res_disabled.text
    finally:
        PanelSettingsService.set("social_enabled", "true", db)
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_sync_direct_chat_mailbox_filtering_and_delta(
    client: TestClient, db: Session, owner_user: User, regular_user: User
):
    """Sync filters by since_id and returns accurate max_envelope_id and unread_count."""
    import os
    from main import app
    from dependencies import get_current_user

    def _make_env(payload_bytes: bytes = b"test-secret-payload-bytes-12345678") -> str:
        raw = os.urandom(12) + payload_bytes + os.urandom(16)
        return "sv-e2ee-v1:" + base64.b64encode(raw).decode("ascii")

    # Establish DirectChat between owner and regular
    min_id, max_id = min(owner_user.id, regular_user.id), max(owner_user.id, regular_user.id)
    mailbox_id = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    chat = DirectChat(
        user_a_id=min_id,
        user_b_id=max_id,
        blind_mailbox_id=mailbox_id,
        initiated_by_user_id=owner_user.id,
    )
    db.add(chat)
    db.commit()

    # Seed 3 envelopes into this mailbox
    env1 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_make_env(b"m1"), client_uuid=str(uuid4()))
    env2 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_make_env(b"m2"), client_uuid=str(uuid4()))
    env3 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_make_env(b"m3"), client_uuid=str(uuid4()))
    db.add_all([env1, env2, env3])
    db.commit()

    max_id_val = env3.id

    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        # Full sync (since_id=0)
        res0 = client.get("/api/social/e2ee/sync?since_id=0")
        assert res0.status_code == 200
        boxes0 = res0.json()["mailboxes"]
        assert len(boxes0) == 1
        assert boxes0[0]["blind_mailbox_id"] == mailbox_id
        assert boxes0[0]["max_envelope_id"] == max_id_val
        assert boxes0[0]["unread_count"] == 3

        # Delta sync (since_id=env2.id) -> only env3 is unread
        res_delta = client.get(f"/api/social/e2ee/sync?since_id={env2.id}")
        assert res_delta.status_code == 200
        boxes_delta = res_delta.json()["mailboxes"]
        assert len(boxes_delta) == 1
        assert boxes_delta[0]["unread_count"] == 1
        assert boxes_delta[0]["max_envelope_id"] == max_id_val

        # Up-to-date sync (since_id=env3.id) -> 0 unread messages
        res_current = client.get(f"/api/social/e2ee/sync?since_id={env3.id}")
        assert res_current.status_code == 200
        assert res_current.json()["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_sync_participant_security_boundary_and_zero_knowledge(client: TestClient, db: Session):
    """Users only see mailboxes where they are authorized participants; third parties see nothing."""
    import os
    from main import app
    from dependencies import get_current_user
    from services.auth_service import AuthService

    def _make_env():
        raw = os.urandom(12) + b"test-secret-payload" + os.urandom(16)
        return "sv-e2ee-v1:" + base64.b64encode(raw).decode("ascii")

    alice = AuthService.create_user(db, "alice_sec_4", "alice_sec_4@test.de", "Pass1234!")
    bob = AuthService.create_user(db, "bob_sec_4", "bob_sec_4@test.de", "Pass1234!")
    charlie = AuthService.create_user(db, "charlie_sec_4", "charlie_sec_4@test.de", "Pass1234!")
    dave = AuthService.create_user(db, "dave_sec_4", "dave_sec_4@test.de", "Pass1234!")

    # Chat AB
    box_ab = SocialService.derive_blind_mailbox_id(alice.id, bob.id)
    chat_ab = DirectChat(user_a_id=min(alice.id, bob.id), user_b_id=max(alice.id, bob.id), blind_mailbox_id=box_ab, initiated_by_user_id=alice.id)
    # Chat BC
    box_bc = SocialService.derive_blind_mailbox_id(bob.id, charlie.id)
    chat_bc = DirectChat(user_a_id=min(bob.id, charlie.id), user_b_id=max(bob.id, charlie.id), blind_mailbox_id=box_bc, initiated_by_user_id=bob.id)
    db.add_all([chat_ab, chat_bc])
    db.commit()

    # Envelopes in both
    db.add(E2eeBlindEnvelope(blind_mailbox_id=box_ab, ciphertext_envelope=_make_env(), client_uuid=str(uuid4())))
    db.add(E2eeBlindEnvelope(blind_mailbox_id=box_bc, ciphertext_envelope=_make_env(), client_uuid=str(uuid4())))
    db.commit()

    # 1. Alice must see box_ab, but NEVER box_bc
    app.dependency_overrides[get_current_user] = lambda: alice
    try:
        res_alice = client.get("/api/social/e2ee/sync")
        mids_alice = [m["blind_mailbox_id"] for m in res_alice.json()["mailboxes"]]
        assert box_ab in mids_alice
        assert box_bc not in mids_alice
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 2. Charlie must see box_bc, but NEVER box_ab
    app.dependency_overrides[get_current_user] = lambda: charlie
    try:
        res_charlie = client.get("/api/social/e2ee/sync")
        mids_charlie = [m["blind_mailbox_id"] for m in res_charlie.json()["mailboxes"]]
        assert box_bc in mids_charlie
        assert box_ab not in mids_charlie
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 3. Bob is in both, so he must see BOTH
    app.dependency_overrides[get_current_user] = lambda: bob
    try:
        res_bob = client.get("/api/social/e2ee/sync")
        mids_bob = [m["blind_mailbox_id"] for m in res_bob.json()["mailboxes"]]
        assert box_ab in mids_bob
        assert box_bc in mids_bob
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 4. Dave is an outsider and must see NOTHING
    app.dependency_overrides[get_current_user] = lambda: dave
    try:
        res_dave = client.get("/api/social/e2ee/sync")
        assert res_dave.json()["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_sync_group_chat_mailbox(client: TestClient, db: Session, owner_user: User, regular_user: User):
    """Group chat mailboxes are returned to active members and excluded upon kick/leave."""
    import hashlib
    import os
    from main import app
    from dependencies import get_current_user

    def _make_env(payload_bytes: bytes = b"test-secret-payload-bytes-12345678") -> str:
        raw = os.urandom(12) + payload_bytes + os.urandom(16)
        return "sv-e2ee-v1:" + base64.b64encode(raw).decode("ascii")

    group = SocialService.create_group(db, user=owner_user, name="Sync Test Guild")
    SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)

    group_box = hashlib.sha256(f"msm:group:{group.id}".encode("utf-8")).hexdigest()
    db.add(E2eeBlindEnvelope(blind_mailbox_id=group_box, ciphertext_envelope=_make_env(), client_uuid=str(uuid4())))
    db.commit()

    # Regular user is member -> sees group mailbox
    app.dependency_overrides[get_current_user] = lambda: regular_user
    try:
        res1 = client.get("/api/social/e2ee/sync")
        mids = [m["blind_mailbox_id"] for m in res1.json()["mailboxes"]]
        assert group_box in mids
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # Owner kicks regular user
    SocialService.kick_group_member(db, group_id=group.id, target_user_id=regular_user.id, caller=owner_user)

    # Regular user no longer member -> group mailbox MUST disappear from sync
    app.dependency_overrides[get_current_user] = lambda: regular_user
    try:
        res2 = client.get("/api/social/e2ee/sync")
        mids2 = [m["blind_mailbox_id"] for m in res2.json()["mailboxes"]]
        assert group_box not in mids2
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_automated_key_provisioning_on_user_creation(db: Session):
    """Neu erstellte Benutzer erhalten automatisch einen gültigen RSA-2048 E2EE Public Key."""
    from services.auth_service import AuthService
    from schemas.social import validate_rsa_public_key_jwk

    user = AuthService.create_user(db, "auto_key_usr_6", "auto_key_6@test.de", "Secret123!")
    db.refresh(user)

    assert user.social_e2ee_public_key is not None
    jwk = validate_rsa_public_key_jwk(user.social_e2ee_public_key)
    assert jwk["kty"] == "RSA"
    assert len(jwk["n"]) >= 300
    assert jwk["e"] == "AQAB"
    assert "d" not in jwk


def test_e2ee_automated_key_provisioning_on_login_lazy(client: TestClient, db: Session):
    """Bestandskonten ohne Public Key erhalten bei Login automatisch einen gültigen Schlüssel."""
    from services.auth_service import AuthService
    from schemas.social import validate_rsa_public_key_jwk
    from dependencies import get_current_user
    from main import app

    legacy_user = User(
        username="legacy_user_sync_7",
        email="legacy_sync_7@test.de",
        password_hash=AuthService.hash_password("LegacyPass123!"),
        is_active=True,
        email_verified=True,
        social_e2ee_public_key=None,
    )
    db.add(legacy_user)
    db.commit()
    db.refresh(legacy_user)
    assert legacy_user.social_e2ee_public_key is None

    res = client.post("/api/auth/login", json={
        "username": "legacy_user_sync_7",
        "password": "LegacyPass123!",
        "otp_code": None,
    })
    assert res.status_code == 200

    db.refresh(legacy_user)
    assert legacy_user.social_e2ee_public_key is not None
    validate_rsa_public_key_jwk(legacy_user.social_e2ee_public_key)

    app.dependency_overrides[get_current_user] = lambda: legacy_user
    try:
        key_res = client.get(f"/api/social/e2ee/public-key/{legacy_user.id}")
        assert key_res.status_code == 200
        assert key_res.json()["public_key"] == legacy_user.social_e2ee_public_key
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_e2ee_key_provisioning_non_regression_existing_keys_preserved(client: TestClient, db: Session):
    """Bereits vorhandene Public Keys und Keyrings werden durch Login NIEMALS überschrieben."""
    from services.auth_service import AuthService

    existing_pub = _gueltiger_public_key("Z")
    existing_user = AuthService.create_user(db, "custom_key_usr_8", "custom_8@test.de", "CustomPass123!")
    existing_user.social_e2ee_public_key = existing_pub
    existing_user.social_e2ee_wrapped_keyring = "sv-e2ee-keyring-v1:" + base64.b64encode(b"\x11" * 16 + b"\x22" * 12 + b"keyring" + b"\x33" * 16).decode("ascii")
    existing_user.social_e2ee_keyring_version = 2
    db.commit()

    for _ in range(2):
        res = client.post("/api/auth/login", json={
            "username": "custom_key_usr_8",
            "password": "CustomPass123!",
            "otp_code": None,
        })
        assert res.status_code == 200

    db.refresh(existing_user)
    assert existing_user.social_e2ee_public_key == existing_pub
    assert existing_user.social_e2ee_keyring_version == 2
    assert "keyring" in existing_user.social_e2ee_wrapped_keyring
