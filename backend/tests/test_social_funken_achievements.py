"""Funken-Errungenschaften: der Client meldet, der Server erfährt nur die Stufe.

Ein Funke lebt verschlüsselt auf den Geräten; der Server hat keine Tabelle
dafür und kann ihn nicht prüfen. Freischalten lässt er deshalb nur die vier
Funken-Stufen auf Zuruf — jede andere Kennung bleibt seine Entscheidung.
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, UserAchievement
from services.achievement_service import (
    ACHIEVEMENTS_BY_ID,
    ACHIEVEMENTS_CATALOG,
    SELBST_GEMELDET,
)

_STUFEN = ("social_streak_10", "social_streak_100", "social_streak_1000", "social_streak_10000")


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_katalog_fuehrt_die_vier_stufen() -> None:
    for kennung, punkte in zip(_STUFEN, (25, 50, 100, 250)):
        eintrag = ACHIEVEMENTS_BY_ID[kennung]
        assert eintrag["category"] == "social"
        assert eintrag["points"] == punkte
    assert len({a["id"] for a in ACHIEVEMENTS_CATALOG}) == len(ACHIEVEMENTS_CATALOG)


def test_selbst_gemeldet_sind_genau_die_funkenstufen() -> None:
    # Wer hier eine Kennung ergänzt, lässt Clients sie ungeprüft freischalten.
    assert SELBST_GEMELDET == frozenset(_STUFEN)


def test_meldung_schaltet_frei_und_nur_einmal(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    erste = client.post(
        "/api/social/achievements/claim",
        json={"achievement_id": "social_streak_10"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert erste.status_code == 200
    assert erste.json() == {"unlocked": True}

    zweite = client.post(
        "/api/social/achievements/claim",
        json={"achievement_id": "social_streak_10"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert zweite.status_code == 200
    assert zweite.json() == {"unlocked": False}

    zeilen = db.query(UserAchievement).filter_by(user_id=owner_user.id, achievement_id="social_streak_10").all()
    assert len(zeilen) == 1


def test_andere_kennungen_lassen_sich_nicht_selbst_melden(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    for kennung in ("social_handshake", "activity_hour_500", "gibt_es_nicht"):
        antwort = client.post(
            "/api/social/achievements/claim",
            json={"achievement_id": kennung},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert antwort.status_code == 400, kennung
    assert db.query(UserAchievement).filter_by(user_id=owner_user.id, achievement_id="activity_hour_500").count() == 0


def test_ohne_anmeldung_oder_csrf_keine_meldung(
    client: TestClient, owner_user: User, owner_cookies: dict
) -> None:
    ohne = client.post("/api/social/achievements/claim", json={"achievement_id": "social_streak_10"})
    assert ohne.status_code in (401, 403)
    ohne_csrf = client.post(
        "/api/social/achievements/claim",
        json={"achievement_id": "social_streak_10"},
        cookies=owner_cookies,
    )
    assert ohne_csrf.status_code == 403


def test_die_meldung_nennt_keinen_kontakt() -> None:
    """Die Anfrage trägt nur die Kennung — kein Feld, in dem ein Freund stehen könnte."""
    from schemas.social import AchievementClaimRequest

    assert set(AchievementClaimRequest.model_fields) == {"achievement_id"}
    # Und keine Tabelle, die einen Funken hielte.
    import models

    namen = " ".join(n.lower() for n, _ in inspect.getmembers(models, inspect.isclass))
    assert "streak" not in namen and "funke" not in namen
