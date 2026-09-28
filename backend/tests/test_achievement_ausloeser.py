"""Jede Errungenschaft im Katalog braucht einen Weg, auf dem sie freigeschaltet wird.

Bis 09/2026 prüfte der Server sieben von 104 Kennungen; der Rest stand im
Katalog und war nicht zu erreichen. Drei Wege gibt es jetzt: am Datenbestand
(`PRUEFUNGEN`), im Aktionspfad (`AchievementService.melde`) und auf Zuruf des
Geräts (`SELBST_GEMELDET`).
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiRun, Team, TeamMember, User, UserAchievement
from services import achievement_pruefungen
from services.achievement_pruefungen import PRUEFUNGEN
from services.achievement_service import ACHIEVEMENTS_BY_ID, SELBST_GEMELDET, AchievementService

_BACKEND = Path(__file__).resolve().parent.parent

_STUNDEN = {f"activity_hour_{h}" for h in (1, 5, 10, 25, 50, 100, 250, 500)}

# Im September 2026 aus dem Katalog genommen: die Funktion dahinter gibt es im
# Panel nicht. Wer eine davon baut, nimmt sie hier heraus, trägt sie wieder in
# den Katalog ein und gibt ihr einen Weg.
_OHNE_FUNKTION = {
    "starter_dark_mode",
    "security_recovery_test",
    "server_sftp_connected",
    "server_player_moderator",
    "server_tag_organizer",
    "server_bulk_operator",
    "server_zero_downtime",
    "backup_lock_champion",
    "social_rich_presence",
}


def _im_aktionspfad() -> set[str]:
    kennungen: set[str] = set()
    muster = re.compile(r"AchievementService\.melde\(\s*[^,]+,\s*[^,]+,\s*\"([a-z0-9_]+)\"")
    for datei in list((_BACKEND / "routers").rglob("*.py")) + list((_BACKEND / "services").rglob("*.py")):
        kennungen |= set(muster.findall(datei.read_text(encoding="utf-8")))
    return kennungen


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _hat(db: Session, user: User, kennung: str) -> bool:
    return db.query(UserAchievement).filter_by(user_id=user.id, achievement_id=kennung).count() == 1


def test_jede_kennung_hat_einen_weg() -> None:
    zustand = {kennung for kennung, _ in PRUEFUNGEN}
    aktion = _im_aktionspfad()
    erreichbar = zustand | aktion | SELBST_GEMELDET | _STUNDEN

    assert erreichbar <= set(ACHIEVEMENTS_BY_ID), erreichbar - set(ACHIEVEMENTS_BY_ID)
    assert set(ACHIEVEMENTS_BY_ID) == erreichbar, set(ACHIEVEMENTS_BY_ID) - erreichbar


def test_nichts_unerreichbares_steht_im_katalog() -> None:
    assert not _OHNE_FUNKTION & set(ACHIEVEMENTS_BY_ID)


def test_chat_oeffnen_ist_noch_kein_erster_kontakt(db: Session, owner_user: User) -> None:
    # `GET /ai/chat` legt die Unterhaltung an, ohne dass jemand schreibt.
    gespraech = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Chat")
    db.add(gespraech)
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert not _hat(db, owner_user, "ai_first_contact")

    db.add(AiRun(id=str(uuid4()), conversation_id=gespraech.id, user_id=owner_user.id, status="completed"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert _hat(db, owner_user, "ai_first_contact")


def test_waechterlauf_zaehlt_nicht_als_erster_kontakt(db: Session, owner_user: User) -> None:
    gespraech = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Guardian", kind="guardian")
    db.add(gespraech)
    db.commit()
    db.add(AiRun(id=str(uuid4()), conversation_id=gespraech.id, user_id=owner_user.id, status="completed"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert not _hat(db, owner_user, "ai_first_contact")


def test_zustand_wird_nachtraeglich_gutgeschrieben(db: Session, owner_user: User) -> None:
    owner_user.time_zone = "Europe/Berlin"
    owner_user.avatar_url = "/api/auth/avatar/1"
    owner_user.social_privacy = "public"
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    for kennung in ("starter_timezone_set", "starter_profile_setup", "social_public_ambassador"):
        assert _hat(db, owner_user, kennung), kennung


def test_das_persoenliche_team_ist_keine_gruendung(db: Session, owner_user: User, regular_user: User) -> None:
    persoenlich = Team(name="Ich", owner_user_id=owner_user.id, personal_for_user_id=owner_user.id)
    db.add(persoenlich)
    db.commit()
    db.add(TeamMember(team_id=persoenlich.id, user_id=owner_user.id, role="owner"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert not _hat(db, owner_user, "team_created")

    geteilt = Team(name="Crew", owner_user_id=owner_user.id)
    db.add(geteilt)
    db.commit()
    db.add(TeamMember(team_id=geteilt.id, user_id=owner_user.id, role="owner"))
    db.add(TeamMember(team_id=geteilt.id, user_id=regular_user.id, role="member"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    AchievementService.check_automatic_achievements(db, regular_user.id)
    assert _hat(db, owner_user, "team_created")
    assert _hat(db, regular_user, "team_member_joined")


def test_kaputte_pruefung_kostet_nur_ihr_abzeichen(db: Session, owner_user: User, monkeypatch) -> None:
    def kaputt(_db, _user, _schon):
        raise RuntimeError("synthetischer Abfragefehler")

    monkeypatch.setattr(
        achievement_pruefungen,
        "PRUEFUNGEN",
        [("server_architect", kaputt), ("starter_first_step", lambda db, user, schon: True)],
    )
    monkeypatch.setattr("services.achievement_service.PRUEFUNGEN", achievement_pruefungen.PRUEFUNGEN)
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert _hat(db, owner_user, "starter_first_step")
    assert not _hat(db, owner_user, "server_architect")


def test_sammelabzeichen_folgt_im_selben_durchlauf(db: Session, owner_user: User) -> None:
    owner_user.two_factor_enabled = True
    db.commit()
    AchievementService.unlock_achievement(db, owner_user.id, "starter_autolock")
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert _hat(db, owner_user, "security_fortress")


def test_melde_laesst_die_aktion_nie_scheitern(db: Session, owner_user: User) -> None:
    owner_user.time_zone = "Europe/Vienna"
    AchievementService.melde(db, owner_user.id, "gibt_es_nicht")
    AchievementService.melde(db, owner_user.id, "terminal_commander")
    AchievementService.melde(db, owner_user.id, "terminal_commander")
    AchievementService.melde(db, None, "terminal_commander")
    db.commit()
    db.refresh(owner_user)
    assert owner_user.time_zone == "Europe/Vienna"
    assert _hat(db, owner_user, "terminal_commander")


def test_melde_rollt_mit_der_aktion_zurueck(db: Session, owner_user: User) -> None:
    AchievementService.melde(db, owner_user.id, "server_port_forwarder")
    db.rollback()
    assert not _hat(db, owner_user, "server_port_forwarder")


def test_geraet_meldet_neue_kennungen(client: TestClient, db: Session, owner_user: User, owner_cookies: dict) -> None:
    for kennung in ("starter_hotkeys", "social_voice_memo", "server_log_analyzer"):
        antwort = client.post(
            "/api/social/achievements/claim",
            json={"achievement_id": kennung},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert antwort.status_code == 200, kennung
        assert _hat(db, owner_user, kennung)


def test_selbst_gemeldet_kennt_nur_was_der_server_nicht_sieht() -> None:
    # Was sich am Bestand ablesen lässt, entscheidet der Server — mit einer
    # Ausnahme: eigene Gruppenrollen liegen verschlüsselt im Gruppenzustand,
    # der Server sieht davon nur Admin- und Moderatorenränge.
    zustand = {kennung for kennung, _ in PRUEFUNGEN}
    assert SELBST_GEMELDET & zustand == {"social_role_architect"}
    assert not SELBST_GEMELDET & _im_aktionspfad()


def test_fremde_sehen_keine_abzeichen_die_den_schutz_verraten(db: Session, owner_user: User) -> None:
    # „Einstieg abgeschlossen" setzt 2FA oder Tresor voraus; sichtbar verriete
    # es, was „Sicherheitsbewusst" verschweigt.
    for kennung in ("starter_security_first", "starter_onboarding_done", "starter_biometrics", "starter_autolock"):
        AchievementService.unlock_achievement(db, owner_user.id, kennung)
    fremd = {a["id"] for a in AchievementService.get_user_achievements(db, owner_user.id, fuer_fremde=True)}
    eigen = {a["id"] for a in AchievementService.get_user_achievements(db, owner_user.id)}
    for kennung in ("starter_security_first", "starter_onboarding_done", "starter_biometrics", "starter_autolock"):
        assert kennung not in fremd, kennung
        assert kennung in eigen, kennung


def test_ueber_fuenfzig_heisst_mehr_als_fuenfzig(db: Session, owner_user: User) -> None:
    gespraech = AiConversation(id=str(uuid4()), user_id=owner_user.id, title="Chat")
    db.add(gespraech)
    db.commit()
    for _ in range(50):
        db.add(AiRun(id=str(uuid4()), conversation_id=gespraech.id, user_id=owner_user.id, status="completed"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert not _hat(db, owner_user, "ai_mastermind")

    db.add(AiRun(id=str(uuid4()), conversation_id=gespraech.id, user_id=owner_user.id, status="completed"))
    db.commit()
    AchievementService.check_automatic_achievements(db, owner_user.id)
    assert _hat(db, owner_user, "ai_mastermind")
