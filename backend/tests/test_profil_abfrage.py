"""Profile verraten Fremden weder, wer existiert, noch wie ein Konto geschützt ist.

Drei Befunde:

* Ohne Anmeldung antworteten die Profilrouten mit 404 oder einem Profil.
  Damit ließen sich Nutzernamen und Konto-IDs durchprobieren.
* Auf einem öffentlichen Profil zeigte das Abzeichen „Sicherheitsbewusst",
  ob die Zwei-Faktor-Anmeldung aktiv ist.
* Ein verborgenes Profil antwortete mit Name und Bild und ``restricted``.
  Die Profilseite war damit per Link erreichbar, auch bei „Privat".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, UserActivityTime, UserFriend
from services.achievement_service import ACHIEVEMENTS_CATALOG, AchievementService
from services.social_service import SocialService


@pytest.mark.parametrize(
    "pfad",
    [
        "/api/social/profile/public/{name}",
        "/api/social/profile/public/gibt-es-nicht",
        "/api/social/profile/{id}",
        "/api/social/profile/user/{id}",
        "/api/social/profile/999999",
        "/api/social/profiles/public",
    ],
)
def test_ohne_anmeldung_gibt_es_keine_auskunft(
    client: TestClient, regular_user: User, pfad: str
) -> None:
    antwort = client.get(pfad.format(name=regular_user.username, id=regular_user.id))
    # Dieselbe Antwort für vorhandene und erfundene Namen: 401.
    assert antwort.status_code == 401


def test_angemeldet_bleibt_das_oeffentliche_profil_sichtbar(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, regular_user: User
) -> None:
    regular_user.social_privacy = "public"
    db.commit()

    antwort = client.get(
        f"/api/social/profile/public/{regular_user.username}", cookies=owner_cookies
    )

    assert antwort.status_code == 200
    assert antwort.json()["restricted"] is False


def _mit_2fa(db: Session, user: User) -> User:
    user.social_privacy = "public"
    user.two_factor_enabled = True
    db.commit()
    AchievementService.check_automatic_achievements(db, user.id)
    db.commit()
    return user


def test_fremde_sehen_nicht_ob_2fa_aktiv_ist(
    db: Session, owner_user: User, regular_user: User
) -> None:
    ziel = _mit_2fa(db, regular_user)

    fremd = SocialService.get_profile(db, owner_user.id, ziel)
    eigen = SocialService.get_profile(db, ziel.id, ziel)

    fremde_ids = {a["id"] for a in fremd["achievements"]}
    assert "starter_security_first" not in fremde_ids
    assert not any(a["category"] == "security" for a in fremd["achievements"])
    # Der Inhaber sieht sein Abzeichen weiter.
    assert any(a["id"] == "starter_security_first" and a["unlocked"] for a in eigen["achievements"])


def test_die_punkte_verraten_das_verborgene_abzeichen_nicht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    ziel = _mit_2fa(db, regular_user)

    fremd = SocialService.get_profile(db, owner_user.id, ziel)["stats"]
    sichtbar = SocialService.get_profile(db, owner_user.id, ziel)["achievements"]

    assert fremd["total_achievements"] == len(sichtbar) < len(ACHIEVEMENTS_CATALOG)
    assert fremd["earned_points"] == sum(a["points"] for a in sichtbar if a["unlocked"])
    assert fremd["total_points"] == sum(a["points"] for a in sichtbar)


VERBORGEN = {"detail": "Benutzer nicht gefunden"}


def _profil(client: TestClient, ziel: User, cookies: dict):
    return client.get(f"/api/social/profile/user/{ziel.id}", cookies=cookies)


def _freunde(db: Session, a: User, b: User, status: str = "accepted") -> None:
    db.add(UserFriend(user_id=a.id, friend_id=b.id, status=status))
    db.commit()


def test_privat_sieht_niemand_auch_kein_freund(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    regular_user: User, user_cookies: dict,
) -> None:
    regular_user.social_privacy = "private"
    db.commit()
    _freunde(db, owner_user, regular_user)

    fremd = _profil(client, regular_user, owner_cookies)
    eigen = _profil(client, regular_user, user_cookies)

    assert fremd.status_code == 404
    assert fremd.json() == VERBORGEN
    assert eigen.status_code == 200


@pytest.mark.parametrize("pfad", ["/api/social/profile/user/{id}", "/api/social/profile/public/{name}"])
def test_verborgen_klingt_wie_nie_vorhanden(
    client: TestClient, db: Session, owner_cookies: dict, regular_user: User, pfad: str
) -> None:
    regular_user.social_privacy = "friends"
    db.commit()

    verborgen = client.get(pfad.format(id=regular_user.id, name=regular_user.username), cookies=owner_cookies)
    erfunden = client.get(pfad.format(id=999999, name="gibt-es-nicht"), cookies=owner_cookies)

    assert (verborgen.status_code, verborgen.json()) == (erfunden.status_code, erfunden.json()) == (404, VERBORGEN)


def test_nur_freunde_sieht_der_freund(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, regular_user: User
) -> None:
    regular_user.social_privacy = "friends"
    db.commit()
    _freunde(db, regular_user, owner_user)

    antwort = _profil(client, regular_user, owner_cookies)

    assert antwort.status_code == 200
    assert antwort.json()["is_friend"] is True
    assert antwort.json()["achievements"] is not None


def test_oeffentlich_zeigt_zeit_und_beitritt(
    client: TestClient, db: Session, owner_cookies: dict, regular_user: User
) -> None:
    regular_user.social_privacy = "public"
    db.add(UserActivityTime(user_id=regular_user.id, category="ai_chat", seconds=5400))
    db.add(UserActivityTime(user_id=regular_user.id, category="general", seconds=600))
    db.commit()

    daten = _profil(client, regular_user, owner_cookies).json()

    assert daten["stats"]["active_time_by_category"] == {"ai_chat": 5400, "general": 600}
    assert daten["stats"]["active_time_seconds"] == 6000
    assert daten["member_since"] == regular_user.created_at.date().isoformat()
    # Nur der Tag, keine E-Mail.
    assert "T" not in daten["member_since"]
    assert "email" not in daten


@pytest.mark.parametrize("richtung", ["ich_blockiere", "ich_bin_blockiert"])
def test_blockiert_ist_verborgen_auch_wenn_oeffentlich(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    regular_user: User, richtung: str,
) -> None:
    regular_user.social_privacy = "public"
    db.commit()
    if richtung == "ich_blockiere":
        _freunde(db, owner_user, regular_user, status="blocked")
    else:
        _freunde(db, regular_user, owner_user, status="blocked")

    antwort = _profil(client, regular_user, owner_cookies)

    assert (antwort.status_code, antwort.json()) == (404, VERBORGEN)
