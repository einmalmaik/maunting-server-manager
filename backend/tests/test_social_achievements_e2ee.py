from __future__ import annotations

from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from uuid import uuid4
from models import User, UserAchievement, UserActivityTime, UserFriend, UserPresence, E2eeBlindEnvelope, AiConversation
from services.achievement_service import AchievementService, ACHIEVEMENTS_CATALOG
from services.social_service import SocialService
from services.panel_settings_service import PanelSettingsService
from services.ai_action_errors import AiActionValidationError
from services.ai_proposals.lifecycle import create_proposal
from services import ai_proposal_service


def test_achievement_catalog_and_dynamic_rarity(db: Session, owner_user: User):
    """Prüft den Meilenstein-Katalog und die dynamische Seltenheitsberechnung."""
    catalog = AchievementService.get_catalog()
    assert len(catalog) >= 10
    assert any(a["id"] == "starter_first_step" for a in catalog)
    assert any(a["id"] == "social_zero_knowledge" for a in catalog)

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

    prof_stranger = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger["restricted"] is True

    prof_self = SocialService.get_profile(db, owner.id, owner)
    assert prof_self["restricted"] is False
    assert prof_self["stats"] is not None

    # 2. Friends: Freund darf sehen, Fremder nicht
    SocialService.update_privacy(db, owner.id, "friends")
    prof_friend_2 = SocialService.get_profile(db, friend.id, owner)
    assert prof_friend_2["restricted"] is False
    assert prof_friend_2["stats"] is not None

    prof_stranger_2 = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger_2["restricted"] is True

    # 3. Public: Jeder darf sehen
    SocialService.update_privacy(db, owner.id, "public")
    prof_stranger_3 = SocialService.get_profile(db, stranger.id, owner)
    assert prof_stranger_3["restricted"] is False
    assert prof_stranger_3["stats"] is not None


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
    envelope = "sv-e2ee-v1:test-ciphertext-blob"

    relayed = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=blind_mailbox,
        ciphertext_envelope=envelope,
        sender_user_id=None,
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
