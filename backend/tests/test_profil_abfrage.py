"""Profile verraten Fremden weder, wer existiert, noch wie ein Konto geschützt ist.

Zwei Befunde:

* Ohne Anmeldung antworteten die Profilrouten mit 404 oder einem Profil.
  Damit ließen sich Nutzernamen und Konto-IDs durchprobieren.
* Auf einem öffentlichen Profil zeigte das Abzeichen „Sicherheitsbewusst",
  ob die Zwei-Faktor-Anmeldung aktiv ist.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
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
