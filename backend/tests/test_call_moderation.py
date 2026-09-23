"""Moderation im Gruppenanruf: stummschalten und rauswerfen.

Beides greift am Medienserver, nicht im Browser des Betroffenen — sonst waere
es eine Bitte statt einer Schranke. Was hier gesichert wird:

* Das Recht wird beim Ausfuehren geprueft, nicht beim Anzeigen des Knopfes.
* Im Zweiergespraech gibt es keine Moderation.
* Rang schuetzt: wer den Eigentuemer nicht aus der Gruppe werfen darf, nimmt
  ihm auch nicht das Wort.
* Der Audit-Eintrag haelt fest, wer gegen wen — nie den Raum und nie den Inhalt.
* Der Stummschalter entzieht **nur** das Mikrofon. Eine laufende Praesentation
  bricht dabei nicht ab.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, User
from services import livekit_service
from services.auth_service import AuthService
from services.call_room_service import GRUPPE_MAX_TEILNEHMER, CallRoomService, GroupCallRoomRegistry
from services.social_service import SocialService

API_KEY = "APItestmoderation"
API_SECRET = "moderations-secret"


@pytest.fixture(autouse=True)
def _raeume_leeren():
    GroupCallRoomRegistry.clear_all_for_testing()
    yield
    GroupCallRoomRegistry.clear_all_for_testing()


@pytest.fixture
def twirp_aufrufe(monkeypatch) -> list[tuple[str, dict]]:
    """Faengt ab, was an LiveKit ginge — ohne Medienserver im Test."""
    aufrufe: list[tuple[str, dict]] = []

    def _fake(api_url, api_key, api_secret, methode, rumpf, raum=None, zeitlimit=5.0):
        aufrufe.append((methode, rumpf))
        return {}

    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", API_KEY)
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", API_SECRET)
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://panel.test/livekit")
    monkeypatch.setattr(livekit_service, "_twirp", _fake)
    return aufrufe


def _header(kekse: dict) -> dict[str, str]:
    return {"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")}


def _gruppe_mit_raum(db: Session, besitzer: User, mitglied: User, rechte: str) -> tuple[int, str]:
    gruppe = SocialService.create_group(db, besitzer)
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, mitglied.id, "member", rechte, besitzer
    )
    raum, _ = GroupCallRoomRegistry.create(gruppe.id, max_peers=GRUPPE_MAX_TEILNEHMER)
    return gruppe.id, raum


# ── Stummschalten ───────────────────────────────────────────────────────────


def test_mit_recht_wird_das_mikrofon_entzogen(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["stumm"] is True

    methode, rumpf = twirp_aufrufe[-1]
    assert methode == "UpdateParticipant"
    assert rumpf["identity"] == f"u{regular_user.id}"
    quellen = rumpf["permission"]["canPublishSources"]
    assert livekit_service.QUELLE_MIKROFON not in quellen
    # Kamera und Bildschirm bleiben: ein Wortentzug darf keine Praesentation
    # abschiessen.
    assert livekit_service.QUELLE_KAMERA in quellen
    assert livekit_service.QUELLE_BILDSCHIRM in quellen


def test_freigeben_gibt_alle_quellen_zurueck(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": False},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    _methode, rumpf = twirp_aufrufe[-1]
    assert livekit_service.QUELLE_MIKROFON in rumpf["permission"]["canPublishSources"]


def test_ohne_recht_kein_stummschalten(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{owner_user.id}/stumm",
        json={"stumm": True},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert twirp_aufrufe == []


def test_beitrittsrecht_allein_reicht_nicht(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    # Wer im Anruf sitzen darf, darf deshalb noch niemandem das Wort nehmen.
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "join_group_calls", owner_user
    )
    raum, _ = GroupCallRoomRegistry.create(gruppe.id, max_peers=GRUPPE_MAX_TEILNEHMER)
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{owner_user.id}/stumm",
        json={"stumm": True},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert twirp_aufrufe == []


def test_gegen_sich_selbst_geht_nicht(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{owner_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 400
    assert twirp_aufrufe == []


def test_der_eigentuemer_laesst_sich_nicht_moderieren(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "mute_in_calls", owner_user
    )
    raum, _ = GroupCallRoomRegistry.create(gruppe.id, max_peers=GRUPPE_MAX_TEILNEHMER)
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{owner_user.id}/stumm",
        json={"stumm": True},
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert twirp_aufrufe == []


def test_im_zweiergespraech_gibt_es_keine_moderation(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    # Sonst koennte jeder sein Gegenueber im eigenen Anruf stummschalten.
    raum = CallRoomService.issue(owner_user.id, regular_user.id)
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 404
    assert twirp_aufrufe == []


def test_unbekannter_raum_ist_nicht_gefunden(
    client: TestClient,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    antwort = client.post(
        f"/api/social/calls/gibtesnicht/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 404
    assert twirp_aufrufe == []


def test_nichtmitglied_wird_nicht_moderiert(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    raum, _ = GroupCallRoomRegistry.create(gruppe.id, max_peers=GRUPPE_MAX_TEILNEHMER)
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 404
    assert twirp_aufrufe == []


# ── Rauswurf ────────────────────────────────────────────────────────────────


def test_rauswurf_entfernt_den_teilnehmer(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/entfernen",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200, antwort.text
    methode, rumpf = twirp_aufrufe[-1]
    assert methode == "RemoveParticipant"
    assert rumpf == {"room": raum, "identity": f"u{regular_user.id}"}

    # Discord-Regel: die Mitgliedschaft und damit das Beitrittsrecht bleiben.
    # Wer rausgeworfen wird, kann sofort wiederkommen.
    assert SocialService.get_group_member(db, gid, regular_user.id) is not None
    assert SocialService.has_group_permission(db, gid, regular_user.id, "join_group_calls")


def test_ohne_recht_kein_rauswurf(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{owner_user.id}/entfernen",
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert twirp_aufrufe == []


def test_stummrecht_erlaubt_keinen_rauswurf(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    # Zwei Rechte, zwei Wirkungen. Sonst waere die Aufspaltung Zierde.
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    dritter = AuthService.create_user(db, "dritter", "dritter@test.de", "DritterPass123!")
    db.commit()
    SocialService.join_group_by_invite_code(db, dritter, gruppe.invite_code)
    SocialService.update_member_role_permissions(
        db, gruppe.id, regular_user.id, "member", "mute_in_calls", owner_user
    )
    raum, _ = GroupCallRoomRegistry.create(gruppe.id, max_peers=GRUPPE_MAX_TEILNEHMER)

    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{dritter.id}/entfernen",
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert twirp_aufrufe == []


# ── Audit ───────────────────────────────────────────────────────────────────


def test_audit_nennt_wer_gegen_wen_aber_nie_den_raum(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    twirp_aufrufe: list,
) -> None:
    gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")
    client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    eintrag = (
        db.query(AuditLog)
        .filter(AuditLog.action == "social.call.mute")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert eintrag is not None
    assert eintrag.user_id == owner_user.id
    assert eintrag.target_id == str(gid)
    # Der Raumname ist das Einladungstoken. Er gehoert in keinen dauerhaften
    # Datensatz — sonst waere nachtraeglich rekonstruierbar, wer wann sprach.
    assert raum not in (eintrag.details or "")
    assert str(regular_user.id) in (eintrag.details or "")


# ── Fehler des Medienservers ────────────────────────────────────────────────


def test_medienserver_fehler_wird_ehrlich_gemeldet(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    owner_cookies: dict,
    monkeypatch,
    twirp_aufrufe: list,
) -> None:
    # Wer glaubt, jemanden stummgeschaltet zu haben, waehrend der weiterspricht,
    # ist schlechter dran als jemand, der eine Fehlermeldung sieht.
    _gid, raum = _gruppe_mit_raum(db, owner_user, regular_user, "join_group_calls")

    def _kaputt(*_a, **_k):
        raise RuntimeError("LiveKit weg")

    monkeypatch.setattr(livekit_service, "_twirp", _kaputt)
    antwort = client.post(
        f"/api/social/calls/{raum}/teilnehmer/{regular_user.id}/stumm",
        json={"stumm": True},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 503
