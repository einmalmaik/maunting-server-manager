"""Gruppenanrufe: Rechte, Raumvergabe und wer den Raumnamen ueberhaupt erfaehrt.

Der blinde Rendezvous-Weg ist weg; die Medien laufen ueber LiveKit. Geblieben
ist die Politik: `start_group_calls` oeffnet einen Raum, `join_group_calls`
betritt ihn, und wer keins von beidem hat, bekommt den Raumnamen nicht einmal
zu sehen.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from models import ChatGroupMember, User
from services import livekit_service
from services.call_room_service import GRUPPE_MAX_TEILNEHMER, GroupCallRoomRegistry
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

API_KEY = "APItestgruppe"
API_SECRET = "gruppen-secret"


@pytest.fixture(autouse=True)
def _raeume_leeren():
    GroupCallRoomRegistry.clear_all_for_testing()
    yield
    GroupCallRoomRegistry.clear_all_for_testing()


@pytest.fixture(autouse=True)
def _livekit_lokal(monkeypatch):
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", API_KEY)
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", API_SECRET)
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://panel.test/livekit")
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"participants": []})


def _header(kekse: dict) -> dict[str, str]:
    return {"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")}


# ── Rechte ──────────────────────────────────────────────────────────────────


def test_gruppenanrufrechte_haengen_an_der_rolle(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)

    assert SocialService.has_group_permission(
        db, gruppe.id, owner_user.id, "start_group_calls"
    )
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "start_group_calls"
    )
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "join_group_calls"
    )

    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    assert SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "join_group_calls"
    )


def test_wer_starten_darf_darf_auch_beitreten(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Sonst oeffnet der Besitzer einen Raum, den er selbst nicht betreten darf."""
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "admin", "", owner_user
    )

    for wer in (owner_user, regular_user):
        assert SocialService.has_group_permission(
            db, gruppe.id, wer.id, "start_group_calls"
        )
        assert SocialService.has_group_permission(
            db, gruppe.id, wer.id, "join_group_calls"
        )


def test_gruppenliste_traegt_die_anrufrechte_des_backends(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Oberflaeche soll die Regel nicht nachbauen, sondern ablesen."""
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)

    als_besitzer = next(
        g for g in SocialService.list_user_groups(db, owner_user.id) if g["id"] == gruppe.id
    )
    assert als_besitzer["can_start_call"] is True
    assert als_besitzer["can_join_call"] is True

    als_mitglied = next(
        g for g in SocialService.list_user_groups(db, regular_user.id) if g["id"] == gruppe.id
    )
    assert als_mitglied["can_start_call"] is False
    assert als_mitglied["can_join_call"] is False

    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    danach = next(
        g for g in SocialService.list_user_groups(db, regular_user.id) if g["id"] == gruppe.id
    )
    assert danach["can_join_call"] is True
    assert danach["can_start_call"] is False


# ── Raum oeffnen ────────────────────────────────────────────────────────────


def test_start_meldet_nur_denen_die_beitreten_duerfen(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    monkeypatch,
) -> None:
    """Wer nicht beitreten darf, erfaehrt auch nicht, dass telefoniert wird."""
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)

    ereignisse: list[tuple[dict, int | None]] = []
    monkeypatch.setattr(
        SyncEventService,
        "publish",
        lambda payload, **kwargs: ereignisse.append((payload, kwargs.get("user_id"))) or 1,
    )

    ohne_recht = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert ohne_recht.status_code == 200
    raum = ohne_recht.json()["room_token"]
    assert raum.startswith("grp_")
    assert ohne_recht.json()["max_peers"] == GRUPPE_MAX_TEILNEHMER
    # `regular_user` hat kein join_group_calls, also geht nur an den Starter etwas hinaus.
    assert {user_id for _, user_id in ereignisse} == {owner_user.id}
    assert all(nutzlast["type"] == "group_call_started" for nutzlast, _ in ereignisse)

    ereignisse.clear()
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    mit_recht = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert mit_recht.status_code == 200
    assert {user_id for _, user_id in ereignisse} == {owner_user.id, regular_user.id}
    assert db.query(ChatGroupMember).filter(
        ChatGroupMember.group_id == gruppe.id
    ).count() == 2


def test_ohne_startrecht_kein_gruppenanruf(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )

    antwort = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403


# ── Zugangstoken fuer Gruppenraeume ─────────────────────────────────────────


def test_token_fuer_gruppenraum_nur_mit_beitrittsrecht(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    user_cookies: dict,
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)

    raum = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["room_token"]

    # Kennt den Raumnamen (im Test), darf aber nicht beitreten.
    abgelehnt = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": raum, "group_id": gruppe.id},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert abgelehnt.status_code == 403

    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    erlaubt = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": raum, "group_id": gruppe.id},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert erlaubt.status_code == 200
    daten = erlaubt.json()
    assert daten["identity"] == f"u{regular_user.id}"
    anspruch = jwt.decode(daten["token"], API_SECRET, algorithms=["HS256"])
    assert anspruch["video"]["room"] == raum
    assert anspruch["video"]["roomJoin"] is True


def test_token_nur_fuer_die_gruppe_der_der_raum_gehoert(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Die eigene Gruppenkennung oeffnet keinen fremden Raum."""
    eine = SocialService.create_group(db, owner_user)
    andere = SocialService.create_group(db, owner_user)

    raum = client.post(
        f"/api/social/calls/groups/{eine.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["room_token"]

    antwort = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": raum, "group_id": andere.id},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 404


def test_token_fuer_unbekannten_gruppenraum(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    antwort = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": "grp_" + "0" * 32, "group_id": gruppe.id},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 404


def test_gruppenanruf_ohne_gruppenkennung(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    antwort = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": "grp_" + "0" * 32},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 400


# ── Raum schliessen ─────────────────────────────────────────────────────────


def test_beenden_schliesst_den_raum_fuer_alle(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    raum = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["room_token"]

    beendet = client.post(
        f"/api/social/calls/groups/{gruppe.id}/end",
        json={"room_token": raum},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert beendet.status_code == 200
    assert GroupCallRoomRegistry.get(raum) is None

    nachtraeglich = client.post(
        "/api/social/calls/token",
        json={"art": "gruppe", "raum": raum, "group_id": gruppe.id},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert nachtraeglich.status_code == 404


def test_ein_gast_beendet_den_anruf_nicht_fuer_alle(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    user_cookies: dict,
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    raum = client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["room_token"]

    antwort = client.post(
        f"/api/social/calls/groups/{gruppe.id}/end",
        json={"room_token": raum},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert GroupCallRoomRegistry.get(raum) is not None


# ── Raumverwaltung ──────────────────────────────────────────────────────────


def test_registry_kennt_gruppe_und_groesse() -> None:
    token, max_peers = GroupCallRoomRegistry.create(group_id=42)
    assert max_peers == GRUPPE_MAX_TEILNEHMER
    assert GroupCallRoomRegistry.get(token) == (42, max_peers)
    assert token in GroupCallRoomRegistry.offene_raeume()

    GroupCallRoomRegistry.discard(token)
    assert GroupCallRoomRegistry.get(token) is None


def test_registry_weist_unsinnige_raumgroessen_ab() -> None:
    for groesse in (1, 0, GRUPPE_MAX_TEILNEHMER + 1):
        with pytest.raises(ValueError):
            GroupCallRoomRegistry.create(group_id=1, max_peers=groesse)


# ── Einladungskarte ─────────────────────────────────────────────────────────


def test_einladungskarte_zeigt_den_laufenden_anruf(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, monkeypatch
) -> None:
    """Wer den Code hat, darf sehen, ob sich das Beitreten gerade lohnt."""
    gruppe = SocialService.create_group(db, owner_user)

    ruhig = client.get(f"/api/social/groups/invite/{gruppe.invite_code}")
    assert ruhig.status_code == 200
    assert ruhig.json()["live_call"] is False
    assert ruhig.json()["live_participants"] == 0

    monkeypatch.setattr(
        livekit_service, "_twirp", lambda *a, **k: {"participants": [{"i": 1}, {"i": 2}]}
    )
    client.post(
        f"/api/social/calls/groups/{gruppe.id}/start",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )

    live = client.get(f"/api/social/groups/invite/{gruppe.invite_code}")
    assert live.status_code == 200
    daten = live.json()
    assert daten["live_call"] is True
    assert daten["live_participants"] == 2
    # Keine Namen, keine Kennungen: nur Ja/Nein und eine Zahl.
    assert set(daten) == {
        "group_id",
        "name",
        "description",
        "avatar_url",
        "invite_card",
        "member_count",
        "live_call",
        "live_participants",
    }
    # Die drei Klartextfelder stehen noch in der Antwort, damit ein aelterer
    # Client nicht auf einen fehlenden Schluessel laeuft -- aber sie sind seit
    # 09/2026 leer. Was ueber die Gruppe zu erfahren ist, steht im Umschlag
    # `invite_card` und geht nur mit dem Schluessel hinter der Raute auf.
    assert daten["name"] is None
    assert daten["description"] is None
    assert daten["avatar_url"] is None
