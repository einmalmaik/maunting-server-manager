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


# Die Schlüsselkennung im Kopf eines Gruppenumschlags: seit 09/2026 trägt
# `sv-e2ee-group-v1:` sie vor dem Chiffretext, weil ein Gerät mehrere
# Schlüsselgenerationen hält. Ein Umschlag ohne sie stammt aus der Zeit, als
# sich der Gruppenschlüssel aus der Gruppenkennung ableiten ließ, und wird
# beim Schreiben abgewiesen.
_GRUPPEN_KEY = "00112233445566ff."


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


def test_gespiegelte_freundschaft_ergibt_einen_eintrag(db: Session):
    """Eine Freundschaft in beiden Richtungen bleibt ein Freund.

    ``user_friends`` ist je Richtung eindeutig: ``(a, b)`` und ``(b, a)`` sind
    zwei erlaubte Zeilen, und beide passen auf die Abfrage der Freundesliste.
    Auf der Dev-Instanz lag am 20.09.2026 genau so ein Paar, und der Messenger
    zeigte jeden Freund zweimal.
    """
    anna = User(username="anna_spiegel", password_hash="hash", is_active=True)
    bert = User(username="bert_spiegel", password_hash="hash", is_active=True)
    db.add_all([anna, bert])
    db.commit()

    db.add_all(
        [
            UserFriend(user_id=anna.id, friend_id=bert.id, status="accepted"),
            UserFriend(user_id=bert.id, friend_id=anna.id, status="accepted"),
        ]
    )
    db.commit()

    assert [f["user_id"] for f in SocialService.get_friends(db, anna.id)] == [bert.id]
    # Aus der Gegenrichtung gilt dasselbe.
    assert [f["user_id"] for f in SocialService.get_friends(db, bert.id)] == [anna.id]


def _gespiegeltes_paar(db: Session, name_a: str, name_b: str) -> tuple[User, User]:
    """Zwei Konten mit einer Freundschaft, die in beiden Richtungen eingetragen ist."""
    a = User(username=name_a, password_hash="hash", is_active=True)
    b = User(username=name_b, password_hash="hash", is_active=True)
    db.add_all([a, b])
    db.commit()
    db.add_all(
        [
            UserFriend(user_id=a.id, friend_id=b.id, status="accepted"),
            UserFriend(user_id=b.id, friend_id=a.id, status="accepted"),
        ]
    )
    db.commit()
    return a, b


def test_entfernen_loest_auch_die_gespiegelte_freundschaft(db: Session):
    """Entfernen muss die Beziehung beenden, nicht nur eine ihrer Zeilen."""
    a, b = _gespiegeltes_paar(db, "anna_entfernt", "bert_entfernt")

    SocialService.remove_friend(db, a.id, b.id)

    assert SocialService.is_confirmed_friend(db, a.id, b.id) is False
    assert db.query(UserFriend).filter(UserFriend.user_id.in_([a.id, b.id])).count() == 0


def test_blockieren_laesst_keine_freundschaftszeile_stehen(db: Session):
    """Wer blockiert ist, darf über keine zweite Zeile Freund bleiben.

    ``social_privacy="friends"`` hängt an ``is_confirmed_friend``: bliebe die
    Spiegelzeile auf "accepted" stehen, sähe der Blockierte das Profil weiter.
    """
    a, b = _gespiegeltes_paar(db, "anna_blockiert", "bert_blockiert")

    SocialService.block_user(db, a.id, b.id)

    zeilen = db.query(UserFriend).filter(UserFriend.user_id.in_([a.id, b.id])).all()
    assert [(z.user_id, z.friend_id, z.status) for z in zeilen] == [(a.id, b.id, "blocked")]
    assert SocialService.is_confirmed_friend(db, a.id, b.id) is False


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
    envelope = f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{b64_ct}"

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


def test_die_ki_kann_keine_nachricht_mehr_senden(db: Session, owner_user: User):
    """Der Server erzeugt kein Chiffretext mehr — auch nicht im Auftrag der KI.

    Bis 09/2026 bildete `_ausfuehren_message_friend` den Kanalschlüssel selbst
    (`sha256("msm:dm:key:<a>:<b>")`), verschlüsselte den Nachrichtentext und legte
    ihn in die Mailbox. Beide Zutaten kannte der Server, er konnte also jede so
    entstandene Nachricht wieder öffnen. Das war keine
    Ende-zu-Ende-Verschlüsselung, sondern eine, die niemanden aussperrte.

    Die drei Werkzeuge sind ersatzlos entfernt. Dieser Test hält fest, dass sie
    nicht zurückkommen, ohne dass es jemand merkt.
    """
    from services.ai_proposals import lifecycle
    from services import ai_tool_registry

    entfernt = ("propose_message_friend", "propose_message_contact", "propose_message_group")

    for name in entfernt:
        assert name not in lifecycle._AUSFUEHRUNGEN, f"{name} hat wieder einen Ausführer"
        assert name not in ai_tool_registry.WERKZEUGE, f"{name} steht wieder im Katalog"

    conv = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Test Chat", kind="primary")
    db.add(conv)
    db.commit()

    with pytest.raises(AiActionValidationError):
        create_proposal(
            db,
            user=owner_user,
            conversation=conv,
            correlation_id=str(uuid4()),
            tool_name="propose_message_friend",
            arguments={"friend_username": "frank", "message_text": "Hallo", "rationale": "Test"},
        )

    # Und nichts davon hat eine Mailbox berührt.
    assert db.query(E2eeBlindEnvelope).count() == 0


def test_kein_serverseitiger_kanalschluessel_mehr_im_code():
    """Die Ableitung selbst muss aus dem Produktivcode verschwunden sein.

    Ein Test gegen die Registrierung allein genügt nicht: die Funktionen könnten
    ohne Werkzeug weiterleben und von irgendwo sonst aufgerufen werden.
    """
    import pathlib

    wurzel = pathlib.Path(__file__).resolve().parent.parent
    treffer = []
    for datei in wurzel.rglob("*.py"):
        if "tests" in datei.parts or "migrations" in datei.parts:
            continue
        text = datei.read_text(encoding="utf-8", errors="ignore")
        if 'f"msm:dm:key:' in text or 'f"msm:group:key:' in text:
            treffer.append(str(datei.relative_to(wurzel)))
    assert treffer == [], f"Serverseitige Kanalableitung noch vorhanden in: {treffer}"


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
        ciphertext_envelope=f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{b64_team}",
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


def test_die_ki_hat_keinen_zugang_zum_messenger(db: Session, owner_user: User) -> None:
    """Die KI kommt an den Messenger nicht heran — nicht per Prompt, sondern gar nicht.

    Senden fiel zuerst (die drei `propose_message_*` verschlüsselten
    serverseitig), Lesen folgte 09/2026: `search_messenger_contacts` und
    `search_messenger_groups` sind mitsamt ihrem Kontaktauflöser entfernt. Der
    Auflöser las dafür Gedächtniseinträge, um „bester Freund" auf ein Konto
    abzubilden — eine Brücke zwischen zwei Bereichen, die einander nichts
    angehen.

    Geprüft wird die Abwesenheit auf allen vier Ebenen, auf denen ein Werkzeug
    entstehen kann: Katalog, Angebot an das Modell, Handler und Recht. Ein
    Prompt ist hier ausdrücklich **kein** Nachweis: er beschreibt nur, was der
    Fall ist, und ein Modell kann sich über Text hinwegsetzen. Über einen
    fehlenden Handler nicht.
    """
    from services import ai_tool_registry
    from services.ai_action_service import _execute_global_read_tool, provider_tool_definitions
    from services.ai_tools.base import AiActionValidationError
    from services import permission_catalog
    from models import ChatGroup, ChatGroupMember

    verboten = (
        "search_messenger_contacts",
        "search_messenger_groups",
        "propose_message_friend",
        "propose_message_contact",
        "propose_message_group",
    )

    # 1. Katalog: kein Eintrag.
    for name in verboten:
        assert name not in ai_tool_registry.WERKZEUGE, f"{name} steht wieder im Katalog"

    # 2. Angebot: das Modell sieht kein Werkzeug, das nach Messenger klingt.
    angeboten = {w["function"]["name"] for w in provider_tool_definitions()}
    assert angeboten.isdisjoint(verboten)
    assert not [n for n in angeboten if "messenger" in n], sorted(angeboten)

    # 3. Handler: es gibt echte Kontakte und eine echte Gruppe, und trotzdem
    #    führt kein Aufruf irgendwohin. Das ist der Teil, der auch dann hält,
    #    wenn ein Modell den Namen frei erfindet.
    alice = User(username="alice_wonder", password_hash="hasha", is_active=True)
    db.add(alice)
    db.commit()
    db.add(UserFriend(user_id=owner_user.id, friend_id=alice.id, status="accepted"))
    gruppe = ChatGroup(name="Gamer Community", owner_user_id=owner_user.id, invite_code="testinv123")
    db.add(gruppe)
    db.commit()
    db.add_all([
        ChatGroupMember(group_id=gruppe.id, user_id=owner_user.id, role="admin"),
        ChatGroupMember(group_id=gruppe.id, user_id=alice.id, role="member"),
    ])
    db.commit()

    for name in ("search_messenger_contacts", "search_messenger_groups"):
        with pytest.raises(AiActionValidationError):
            _execute_global_read_tool(
                db, user=owner_user, tool_name=name, arguments={"query": "alice"}
            )

    # 4. Recht: `ai.social.message_friend` ist aus dem Katalog raus. Ein Recht
    #    ohne Abnehmer ist ein Versprechen, das niemand einlöst.
    rechte = set(permission_catalog.ALL_KEYS)
    assert "ai.social.message_friend" not in rechte
    assert not [k for k in rechte if k.startswith("ai.social.")], sorted(rechte)

    # 5. Der Kontaktauflöser ist weg, nicht bloß unbenutzt.
    with pytest.raises(ModuleNotFoundError):
        __import__("services.social_matching_service")

    # Und nichts davon hat einen Umschlag erzeugt.
    assert db.query(E2eeBlindEnvelope).count() == 0


def test_e2ee_security_replay_attack_prevention(db: Session, owner_user: User):
    """Prüft die Replay-Attack-Erkennung: identische Ciphertexte dürfen weder in derselben noch in fremden Mailboxen wiederholt werden."""
    import base64
    import os
    from fastapi import HTTPException

    valid_ct = base64.b64encode(os.urandom(12) + b"ciphertext-bytes-here" + os.urandom(16)).decode("ascii")
    envelope = f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{valid_ct}"
    box_a = "1" * 64
    box_b = "2" * 64

    # 1. Erster Versand ist frisch und erfolgreich
    relayed = SocialService.relay_blind_envelope(db, blind_mailbox_id=box_a, ciphertext_envelope=envelope)
    assert relayed.id is not None
    assert relayed.ciphertext_sha256 is not None

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
        validate_e2ee_envelope_format(f"sv-e2ee-group-v1:{leaked_payload}")
    assert "Sicherheitsverletzung" in str(exc_leak.value)

    # 2. Unbekanntes oder fehlendes Prefix
    with pytest.raises(ValueError) as exc_prefix:
        validate_e2ee_envelope_format("plain-message-without-dis-prefix")
    assert "Ungültiges E2EE-Umschlagformat" in str(exc_prefix.value)

    # 3. Schwacher / ungültiger Null-IV (12 Null-Bytes)
    zero_iv_raw = b"\x00" * 12 + b"ciphertext-bytes-here" + b"\x01" * 16
    zero_iv_b64 = base64.b64encode(zero_iv_raw).decode("ascii")
    with pytest.raises(ValueError) as exc_iv:
        validate_e2ee_envelope_format(f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{zero_iv_b64}")
    assert "Null-IV" in str(exc_iv.value)

    # 4. Steuerzeichen im Umschlag
    with pytest.raises(ValueError) as exc_ctrl:
        validate_e2ee_envelope_format("sv-e2ee-group-v1:payload\nwith\rnewlines")
    assert "Steuerzeichen" in str(exc_ctrl.value)

    # 5. Zu kurzer Ciphertext (< 38 Zeichen / 28 Bytes)
    short_ct = base64.b64encode(b"\x01" * 10).decode("ascii")
    with pytest.raises(ValueError) as exc_short:
        validate_e2ee_envelope_format(f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{short_ct}")
    assert "zu kurz" in str(exc_short.value).lower()

    # 6. Die abgeschafften Formate werden beim Schreiben nicht mehr angenommen.
    #    Ihr Schlüssel ließ sich aus den Benutzerkennungen ableiten, die in der
    #    Datenbank stehen — der Server konnte mitlesen. Abgelegte Altumschläge
    #    bleiben liegen, neue dürfen nicht mehr entstehen.
    valid_bytes = b"\x01" * 12 + b"ciphertext-bytes-here-ok" + b"\x02" * 16
    valid_b64 = base64.b64encode(valid_bytes).decode("ascii")
    for veraltet in ("sv-e2ee-v1:", "sv-e2ee-team-v1:"):
        with pytest.raises(ValueError) as exc_alt:
            validate_e2ee_envelope_format(f"{veraltet}{valid_b64}")
        assert "Ungültiges E2EE-Umschlagformat" in str(exc_alt.value)
    with pytest.raises(ValueError):
        validate_e2ee_envelope_format(f"sv-e2ee-ratchet-v1:0.{valid_b64}")


def test_gruppenumschlag_braucht_eine_schluesselkennung():
    """`sv-e2ee-group-v1:<keyId>.<chiffre>` — ohne Kennung ist es der Altbestand.

    Der alte Gruppenumschlag trug hier nur Base64. Sein Schlüssel war
    `sha256("msm:group:key:" + groupId)`, und die Gruppenkennung steht in der
    Datenbank — der Server konnte jede Gruppennachricht öffnen. Die Kennung im
    Kopf ist deshalb nicht bloß Formsache: sie ist das Merkmal, an dem das
    Backend neue von alten Umschlägen unterscheidet.
    """
    import base64
    from schemas.social import validate_e2ee_envelope_format

    chiffre = base64.b64encode(b"N" * 12 + b"gruppen-chiffretext" + b"T" * 16).decode("ascii")

    validate_e2ee_envelope_format(f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{chiffre}")

    for kaputt, erwartet in (
        # Der Altbestand: reines Base64 ohne Kennung.
        (f"sv-e2ee-group-v1:{chiffre}", "Schlüsselkennung"),
        # Kennungen, die keine sind: zu kurz, zu lang, keine Hexzeichen.
        (f"sv-e2ee-group-v1:00ff.{chiffre}", "Schlüsselkennung"),
        (f"sv-e2ee-group-v1:{'a' * 32}.{chiffre}", "Schlüsselkennung"),
        (f"sv-e2ee-group-v1:00112233445566FF.{chiffre}", "Schlüsselkennung"),
        (f"sv-e2ee-group-v1:00112233445566gg.{chiffre}", "Schlüsselkennung"),
        # Kennung in Ordnung, Chiffretext nicht. Lang genug, damit nicht schon
        # die Längenprüfung davor greift.
        (f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{'!' * 44}", "Base64"),
        (f"sv-e2ee-group-v1:{_GRUPPEN_KEY}kurz", "zu kurz"),
    ):
        with pytest.raises(ValueError) as exc:
            validate_e2ee_envelope_format(kaputt)
        assert erwartet in str(exc.value), kaputt


def test_double_ratchet_umschlag_wird_geprueft():
    """Der Kopf eines `sv-e2ee-dr-v1:` trägt das Routing und muss stimmen."""
    import base64
    from schemas.social import validate_e2ee_envelope_format

    rumpf = base64.b64encode(
        b'sv-dr-msg-v1:{"header":{"v":"sv-dr-msg-v1","dh":"AA","pn":0,"n":0}}'
    ).decode("ascii")
    von = "a" * 32
    fuer = "b" * 32

    # Gültig: vier Felder, beide Kennungen wohlgeformt, Rumpf ist eine DIS-Nachricht.
    validate_e2ee_envelope_format(f"sv-e2ee-dr-v1:7.{von}.{fuer}.{rumpf}")

    # Der Zweig kehrt eigenständig zurück: ein Double-Ratchet-Umschlag trägt
    # keinen IV, die gemeinsame Null-IV-Prüfung darf ihn nicht treffen. Ein
    # Rumpf, dessen erste zwölf Bytes null wären, muss trotzdem durchgehen.
    null_rumpf = base64.b64encode(b"sv-dr-msg-v1:" + b"\x00" * 40).decode("ascii")
    validate_e2ee_envelope_format(f"sv-e2ee-dr-v1:7.{von}.{fuer}.{null_rumpf}")

    for kaputt, erwartet in (
        (f"sv-e2ee-dr-v1:7.{von}.{rumpf}", "Konto"),
        (f"sv-e2ee-dr-v1:x.{von}.{fuer}.{rumpf}", "Absenderkennung"),
        (f"sv-e2ee-dr-v1:0.{von}.{fuer}.{rumpf}", "Absenderkennung"),
        (f"sv-e2ee-dr-v1:7.kurz.{fuer}.{rumpf}", "Gerätekennung"),
        (f"sv-e2ee-dr-v1:7.{von}.{fuer}.keinbase64!", "Base64"),
        (
            f"sv-e2ee-dr-v1:7.{von}.{fuer}."
            + base64.b64encode(b"irgendwas anderes").decode("ascii"),
            "DIS-Nachrichtenformat",
        ),
    ):
        with pytest.raises(ValueError) as exc:
            validate_e2ee_envelope_format(kaputt)
        assert erwartet in str(exc.value), kaputt


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

    # 7. Derselbe Riegel am Geraetedienst: ein Client, der versehentlich seinen
    #    privaten Teil veroeffentlicht, wird gestoppt statt gespeichert.
    from services import e2ee_device_service
    with pytest.raises(ValueError):
        e2ee_device_service.veroeffentlichen(
            db, owner_user, device_id="a1b2c3d4e5f60718", public_key_jwk=leak_d
        )


def test_user_device_mailbox_e2ee_key_sync_and_privacy(
    db: Session, owner_user: User, regular_user: User, client: TestClient, owner_cookies: dict, user_cookies: dict
):
    """Prüft die blinde Geräte-Mailbox für Mehrgeräte-Schlüsselsynchronisation und deren Zugriffsschutz."""
    from fastapi import HTTPException
    from services.panel_settings_service import PanelSettingsService
    PanelSettingsService.set("social_enabled", "true", db)

    device_mailbox_owner = SocialService.derive_user_device_mailbox_id(owner_user.id)
    assert device_mailbox_owner is not None
    assert len(device_mailbox_owner) == 64

    # Formal gültiger verschlüsselter Hybrid-Umschlag
    import base64
    wk = base64.b64encode(b"K" * 256).decode("ascii")
    ct = base64.b64encode(b"\x01" * 12 + b"test-secret-payload" + b"\x02" * 16).decode("ascii")
    envelope = f"sv-e2ee-hybrid-v1:{wk}.{ct}"

    # 1. Owner synchronisiert Notizenschlüssel an die eigene Geräte-Mailbox
    relayed = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=device_mailbox_owner,
        ciphertext_envelope=envelope,
        sender_user_id=owner_user.id,
        recipient_id=owner_user.id,
        client_uuid="noteskey:devA:devB:12345",
        is_control=True,
        control_type="notes_key_sync",
    )
    assert relayed.id is not None
    assert relayed.blind_mailbox_id == device_mailbox_owner

    # 2. sync_mailboxes für den Owner enthält die Geräte-Mailbox
    synced = SocialService.sync_mailboxes(db, current_user=owner_user)
    mids = [m["blind_mailbox_id"] for m in synced]
    assert device_mailbox_owner in mids

    # 3. Fremder Nutzer darf NICHT in die Geräte-Mailbox des Owners einliefern (mit recipient_id -> 400)
    ct2 = base64.b64encode(b"\x03" * 12 + b"attack-secret-payload" + b"\x04" * 16).decode("ascii")
    with pytest.raises(HTTPException) as exc:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=device_mailbox_owner,
            ciphertext_envelope=f"sv-e2ee-hybrid-v1:{wk}.{ct2}",
            sender_user_id=regular_user.id,
            recipient_id=owner_user.id,
            client_uuid="attack:123",
        )
    assert exc.value.status_code == 400

    # 3b. Fremder Nutzer darf auch OHNE recipient_id NICHT in fremde Geräte-Mailbox einliefern (403)
    with pytest.raises(HTTPException) as exc_no_recip:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=device_mailbox_owner,
            ciphertext_envelope=f"sv-e2ee-hybrid-v1:{wk}.{ct2}",
            sender_user_id=regular_user.id,
            recipient_id=None,
            client_uuid="attack:124",
        )
    assert exc_no_recip.value.status_code == 403

    # 4. Owner kann seine Geräte-Mailbox abfragen
    resp_owner = client.get(f"/api/social/e2ee/mailbox/{device_mailbox_owner}", cookies=owner_cookies)
    assert resp_owner.status_code == 200
    assert len(resp_owner.json()) >= 1
    assert resp_owner.json()[0]["blind_mailbox_id"] == device_mailbox_owner

    # 5. Fremder Nutzer wird beim Abruf der fremden Geräte-Mailbox mit 403 abgewiesen
    resp_stranger = client.get(f"/api/social/e2ee/mailbox/{device_mailbox_owner}", cookies=user_cookies)
    assert resp_stranger.status_code == 403

    # 6. Auch bei deaktiviertem Benutzer darf ein Fremder dessen Geräte-Mailbox nicht abrufen (403)
    owner_user.is_active = False
    db.commit()
    resp_stranger_inactive = client.get(f"/api/social/e2ee/mailbox/{device_mailbox_owner}", cookies=user_cookies)
    assert resp_stranger_inactive.status_code == 403
    owner_user.is_active = True
    db.commit()


def test_mailbox_participant_access_control_dm_and_group(
    db: Session, owner_user: User, regular_user: User, client: TestClient, owner_cookies: dict, user_cookies: dict
):
    """H-3: Prüft, dass Nicht-Teilnehmer (Fremde) keinen Zugriff auf DM- und Gruppen-Mailboxen erhalten (403)."""
    PanelSettingsService.set("social_enabled", "true", db)

    # 1. Gruppen-Mailbox: Owner ist Mitglied, regular_user ist Fremder
    group = ChatGroup(
        name="Geheime Runde",
        description="Nur fuer Mitglieder",
        owner_user_id=owner_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(group)
    db.flush()
    db.add(ChatGroupMember(group_id=group.id, user_id=owner_user.id, role="owner"))
    db.commit()

    group_mid = SocialService.derive_group_blind_mailbox_id(group.id)

    # Einlieferung eines Umschlags in die Gruppe
    group_ct = base64.b64encode(b"\x05" * 12 + b"geheime-nachricht-fuer-gruppe" + b"\x06" * 16).decode("ascii")
    envelope = f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{group_ct}"
    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=group_mid,
        ciphertext_envelope=envelope,
        sender_user_id=owner_user.id,
        recipient_id=None,
        client_uuid=str(uuid4()),
    )

    # Owner (Mitglied) darf abrufen -> 200
    resp_owner_group = client.get(f"/api/social/e2ee/mailbox/{group_mid}", cookies=owner_cookies)
    assert resp_owner_group.status_code == 200
    assert len(resp_owner_group.json()) >= 1

    # regular_user (Nicht-Mitglied / Fremder) wird abgewiesen -> 403
    resp_stranger_group = client.get(f"/api/social/e2ee/mailbox/{group_mid}", cookies=user_cookies)
    assert resp_stranger_group.status_code == 403

    # 2. Direktchat-Mailbox: Zwischen Owner und einem dritten Nutzer
    from services.auth_service import AuthService
    third_user = AuthService.create_user(
        db, f"third_{uuid4().hex[:6]}", f"third_{uuid4().hex[:6]}@test.de", "ThirdPass123!"
    )
    third_user.email_verified = True
    db.commit()

    dm_mid = SocialService.derive_blind_mailbox_id(owner_user.id, third_user.id)
    chat = DirectChat(
        user_a_id=min(owner_user.id, third_user.id),
        user_b_id=max(owner_user.id, third_user.id),
        blind_mailbox_id=dm_mid,
        initiated_by_user_id=owner_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(chat)
    db.add(UserFriend(user_id=owner_user.id, friend_id=third_user.id, status="accepted"))
    db.commit()

    # Einlieferung eines Umschlags in die DM-Mailbox
    wk = base64.b64encode(b"K" * 256).decode("ascii")
    ct = base64.b64encode(b"\x01" * 12 + b"dm-secret-payload" + b"\x02" * 16).decode("ascii")
    dm_envelope = f"sv-e2ee-hybrid-v1:{wk}.{ct}"
    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=dm_mid,
        ciphertext_envelope=dm_envelope,
        sender_user_id=owner_user.id,
        recipient_id=third_user.id,
        client_uuid=str(uuid4()),
    )

    # Owner (Teilnehmer A) darf abrufen -> 200
    resp_owner_dm = client.get(f"/api/social/e2ee/mailbox/{dm_mid}", cookies=owner_cookies)
    assert resp_owner_dm.status_code == 200
    assert len(resp_owner_dm.json()) >= 1

    # regular_user (Fremder) wird abgewiesen -> 403
    resp_stranger_dm = client.get(f"/api/social/e2ee/mailbox/{dm_mid}", cookies=user_cookies)
    assert resp_stranger_dm.status_code == 403


