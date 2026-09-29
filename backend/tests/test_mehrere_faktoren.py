"""Mehrere zweite Faktoren gleichzeitig: Authenticator-App und beliebig viele Passkeys.

Bis 29.09.2026 hatte ein Konto genau einen zweiten Faktor. Ein Passkey
loeschte die App, ein zweiter Passkey liess sich nicht anlegen, und wer den
einzigen Passkey am PC mit Windows Hello angelegt hatte, kam am neuen Handy
nur noch per Backup-Code hinein. Dazu kamen zwei Luecken: neue Backup-Codes
gab es allein mit dem Zugangstoken, und das Abschalten durch einen Admin liess
Geheimnis und Backup-Codes stehen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import BackupCode, User, UserPasskey
from services.auth_service import AuthService
from services.backup_code_service import BackupCodeService
from tests._totp import totp_now
from tests.test_passkey_2fa import (
    PASSWORT,
    Authenticator,
    _anmelden,
    _b64,
    _kopf,
    _passkey_einrichten,
    _schritt_optionen,
)

GEHEIMNIS = "JBSWY3DPEHPK3PXP"


def _totp_aktiv(db: Session, user: User) -> None:
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(GEHEIMNIS, aad=f"msm:user:{user.id}:2fa")
    user.two_factor_enabled = True
    db.commit()


def _passkey_nachweis(client: TestClient, cookies: dict, geraet: Authenticator, zweck: str = "2fa_change") -> dict:
    return {"passkey": geraet.bestaetigen(_schritt_optionen(client, cookies, zweck))}


# ── Faktoren nebeneinander ───────────────────────────────────────────────


def test_zweiter_passkey_neben_dem_ersten(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    pc = _passkey_einrichten(client, owner_cookies, name="PC")
    handy = _passkey_einrichten(client, owner_cookies, _passkey_nachweis(client, owner_cookies, pc), name="Handy")

    assert db.query(UserPasskey).filter(UserPasskey.user_id == owner_user.id).count() == 2
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    assert {c["id"] for c in optionen["allowCredentials"]} == {_b64(pc.kennung), _b64(handy.kennung)}
    assert _anmelden(client, passkey=handy.bestaetigen(optionen))["status"] == 200


def test_passkey_loescht_die_app_nicht(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _totp_aktiv(db, owner_user)

    geraet = _passkey_einrichten(client, owner_cookies, {"otp_code": totp_now(GEHEIMNIS)})

    db.refresh(owner_user)
    assert owner_user.two_factor_secret_encrypted is not None
    assert client.get("/api/auth/me", cookies=owner_cookies).json()["two_factor_methods"] == ["passkey", "totp"]
    client.cookies.clear()
    schritt1 = _anmelden(client)["body"]
    assert schritt1["two_factor_methods"] == ["passkey", "totp"]
    # Der Code aus dem Nachweis ist verbraucht; der naechste aus der App.
    assert _anmelden(client, otp_code=totp_now(GEHEIMNIS, versatz=1))["status"] == 200
    # Zuletzt per App angemeldet: beim naechsten Mal kommt sie zuerst.
    client.cookies.clear()
    assert _anmelden(client)["body"]["two_factor_methods"] == ["totp", "passkey"]
    optionen = _anmelden(client)["body"]["passkey_options"]
    assert _anmelden(client, passkey=geraet.bestaetigen(optionen))["status"] == 200


def test_app_neben_passkey_gilt_erst_nach_dem_code(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    """Ein Geheimnis in Einrichtung oeffnet nichts, weder Login noch Nachweis."""
    geraet = _passkey_einrichten(client, owner_cookies)

    ohne = client.post("/api/auth/2fa/setup", headers=_kopf(owner_cookies), cookies=owner_cookies, json=PASSWORT)
    assert ohne.status_code == 403
    setup = client.post(
        "/api/auth/2fa/setup", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, geraet),
    )
    assert setup.status_code == 200, setup.text
    neu = setup.json()["secret"]

    client.cookies.clear()
    assert _anmelden(client, otp_code=totp_now(neu))["status"] == 401

    fertig = client.post(
        f"/api/auth/2fa/enable?otp_code={totp_now(neu)}", headers=_kopf(owner_cookies), cookies=owner_cookies
    )
    assert fertig.status_code == 200, fertig.text
    # 2FA war schon an: keine neuen Backup-Codes nebenbei.
    assert "backup_codes" not in fertig.json()
    client.cookies.clear()
    assert _anmelden(client, otp_code=totp_now(neu, versatz=1))["status"] == 200


def test_erster_faktor_bringt_die_backup_codes_mit(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    geraet = Authenticator()
    optionen = client.post(
        "/api/auth/2fa/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies, json=PASSWORT
    ).json()
    res = client.post(
        "/api/auth/2fa/passkey/enable", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=geraet.anlegen(optionen),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["backup_codes"]) == 5
    assert db.query(BackupCode).filter(BackupCode.user_id == owner_user.id).count() == 5


# ── Nachweis vor jedem neuen Faktor ──────────────────────────────────────


def test_erster_faktor_verlangt_das_passwort(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Sonst traegt ein abgegriffenes Token eine eigene App ein und sperrt den Inhaber aus."""
    for body in (None, {"password": "falsch"}):
        for pfad in ("/api/auth/2fa/setup", "/api/auth/2fa/passkey/options"):
            res = client.post(pfad, headers=_kopf(owner_cookies), cookies=owner_cookies, json=body)
            assert res.status_code == 403, (pfad, body, res.text)
    db.refresh(owner_user)
    assert owner_user.two_factor_secret_pending_encrypted is None


def test_social_konto_ohne_passwort_richtet_ohne_nachweis_ein(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
):
    """Entscheidung des Betreibers vom 29.09.2026: es hat nichts, womit es sich ausweisen koennte."""
    regular_user.has_password = False
    db.commit()
    res = client.post("/api/auth/2fa/setup", headers=_kopf(user_cookies), cookies=user_cookies)
    assert res.status_code == 200, res.text


def test_weiterer_passkey_braucht_einen_faktor(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Mit aktiver 2FA reicht das Passwort nicht mehr, und ein Backup-Code auch nicht."""
    _passkey_einrichten(client, owner_cookies)
    codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
    for body in (PASSWORT, {"otp_code": "123456"}, {"password": codes[0]}):
        res = client.post(
            "/api/auth/2fa/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies, json=body
        )
        assert res.status_code in (403, 422), (body, res.text)
    assert db.query(UserPasskey).count() == 1


def test_backup_codes_nur_mit_faktor(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Bis 29.09.2026 gab es neue Backup-Codes allein mit dem Zugangstoken."""
    _totp_aktiv(db, owner_user)

    ohne = client.post("/api/auth/2fa/backup/generate", headers=_kopf(owner_cookies), cookies=owner_cookies)
    assert ohne.status_code == 403
    assert db.query(BackupCode).filter(BackupCode.user_id == owner_user.id).count() == 0

    mit = client.post(
        "/api/auth/2fa/backup/generate", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"otp_code": totp_now(GEHEIMNIS)},
    )
    assert mit.status_code == 200, mit.text


# ── Entfernen ────────────────────────────────────────────────────────────


def test_passkey_entfernen_bis_zum_letzten(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    pc = _passkey_einrichten(client, owner_cookies)
    handy = _passkey_einrichten(client, owner_cookies, _passkey_nachweis(client, owner_cookies, pc))
    BackupCodeService.generate_backup_codes(db, owner_user.id)
    ids = [p["id"] for p in client.get("/api/auth/2fa/passkeys", cookies=owner_cookies).json()]

    ohne = client.post(
        f"/api/auth/2fa/passkeys/{ids[0]}/remove", headers=_kopf(owner_cookies), cookies=owner_cookies, json={}
    )
    assert ohne.status_code == 403

    erster = client.post(
        f"/api/auth/2fa/passkeys/{ids[0]}/remove", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, pc),
    )
    assert erster.json() == {"two_factor_enabled": True}

    letzter = client.post(
        f"/api/auth/2fa/passkeys/{ids[1]}/remove", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, handy),
    )
    assert letzter.json() == {"two_factor_enabled": False}
    db.expire_all()
    assert owner_user.two_factor_enabled is False
    assert db.query(UserPasskey).count() == 0
    assert db.query(BackupCode).filter(BackupCode.user_id == owner_user.id).count() == 0


def test_fremden_passkey_entfernen_geht_nicht(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict, user_cookies: dict
):
    _passkey_einrichten(client, owner_cookies)
    fremd = db.query(UserPasskey).one()
    regular_user.two_factor_secret_encrypted = AuthService.encrypt_secret(
        GEHEIMNIS, aad=f"msm:user:{regular_user.id}:2fa"
    )
    regular_user.two_factor_enabled = True
    db.commit()

    res = client.post(
        f"/api/auth/2fa/passkeys/{fremd.id}/remove", headers=_kopf(user_cookies), cookies=user_cookies,
        json={"otp_code": totp_now(GEHEIMNIS)},
    )
    assert res.status_code == 404
    assert db.query(UserPasskey).count() == 1


def test_app_entfernen_laesst_den_passkey(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _totp_aktiv(db, owner_user)
    _passkey_einrichten(client, owner_cookies, {"otp_code": totp_now(GEHEIMNIS)})

    res = client.post(
        "/api/auth/2fa/totp/remove", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"otp_code": totp_now(GEHEIMNIS, versatz=1)},
    )
    assert res.json() == {"two_factor_enabled": True}
    db.refresh(owner_user)
    assert owner_user.two_factor_secret_encrypted is None
    assert owner_user.two_factor_methods == ["passkey"]
    # Die Sperre verbrauchter Codes zuruecksetzen: abgelehnt wird dann nur,
    # weil die App weg ist.
    owner_user.two_factor_totp_last_step = None
    db.commit()
    client.cookies.clear()
    assert _anmelden(client, otp_code=totp_now(GEHEIMNIS))["status"] == 401


def test_admin_abschalten_nimmt_alles_mit(client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict):
    """Bis 29.09.2026 blieben Geheimnis und Backup-Codes stehen."""
    regular_user.two_factor_secret_encrypted = AuthService.encrypt_secret(
        GEHEIMNIS, aad=f"msm:user:{regular_user.id}:2fa"
    )
    regular_user.two_factor_enabled = True
    db.commit()
    BackupCodeService.generate_backup_codes(db, regular_user.id)

    res = client.patch(
        f"/api/admin/users/{regular_user.id}", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"two_factor_enabled": False},
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    assert regular_user.two_factor_enabled is False
    assert regular_user.two_factor_secret_encrypted is None
    assert db.query(BackupCode).filter(BackupCode.user_id == regular_user.id).count() == 0


# ── Was nicht gehen darf ─────────────────────────────────────────────────


def test_entfernter_passkey_meldet_nicht_mehr_an(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    """Ein verlorenes Handy wird entfernt; seine Unterschrift oeffnet danach nichts mehr."""
    pc = _passkey_einrichten(client, owner_cookies, name="PC")
    handy = _passkey_einrichten(client, owner_cookies, _passkey_nachweis(client, owner_cookies, pc), name="Handy")
    handy_id = next(
        p["id"] for p in client.get("/api/auth/2fa/passkeys", cookies=owner_cookies).json() if p["name"] == "Handy"
    )
    res = client.post(
        f"/api/auth/2fa/passkeys/{handy_id}/remove", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, pc),
    )
    assert res.status_code == 200, res.text

    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    assert {c["id"] for c in optionen["allowCredentials"]} == {_b64(pc.kennung)}
    assert _anmelden(client, passkey=handy.bestaetigen(optionen))["status"] == 401
    # Auch als Nachweis im Profil zaehlt es nicht mehr.
    backup = client.post(
        "/api/auth/2fa/backup/generate", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, handy),
    )
    assert backup.status_code == 403


def test_app_entfernen_ohne_gueltigen_nachweis_scheitert(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    _totp_aktiv(db, owner_user)
    for body in ({}, PASSWORT, {"otp_code": "000000"}):
        res = client.post("/api/auth/2fa/totp/remove", headers=_kopf(owner_cookies), cookies=owner_cookies, json=body)
        assert res.status_code == 403, (body, res.text)
    db.refresh(owner_user)
    assert owner_user.two_factor_totp_aktiv is True


def test_falscher_code_bestaetigt_die_app_nicht(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    setup = client.post("/api/auth/2fa/setup", headers=_kopf(owner_cookies), cookies=owner_cookies, json=PASSWORT)
    assert setup.status_code == 200, setup.text
    richtig = totp_now(setup.json()["secret"])
    falsch = "000000" if richtig != "000000" else "111111"

    res = client.post(f"/api/auth/2fa/enable?otp_code={falsch}", headers=_kopf(owner_cookies), cookies=owner_cookies)
    assert res.status_code == 400
    db.refresh(owner_user)
    assert owner_user.two_factor_enabled is False
    assert owner_user.two_factor_secret_encrypted is None
    assert owner_user.two_factor_methods == []


def test_aktive_app_wird_nicht_ueberschrieben(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Sonst tauscht, wer einmal einen Code sieht, die App des Inhabers gegen seine."""
    _totp_aktiv(db, owner_user)
    vorher = owner_user.two_factor_secret_encrypted
    res = client.post(
        "/api/auth/2fa/setup", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"otp_code": totp_now(GEHEIMNIS)},
    )
    assert res.status_code == 400
    db.refresh(owner_user)
    assert owner_user.two_factor_secret_encrypted == vorher
    assert owner_user.two_factor_secret_pending_encrypted is None


def test_ein_passkey_nachweis_gilt_nur_einmal(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    nachweis = _passkey_nachweis(client, owner_cookies, geraet)
    erst = client.post("/api/auth/2fa/backup/generate", headers=_kopf(owner_cookies), cookies=owner_cookies, json=nachweis)
    assert erst.status_code == 200, erst.text
    noch = client.post("/api/auth/2fa/backup/generate", headers=_kopf(owner_cookies), cookies=owner_cookies, json=nachweis)
    assert noch.status_code == 403


def test_backup_code_gilt_nur_einmal(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _passkey_einrichten(client, owner_cookies)
    codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
    client.cookies.clear()
    assert _anmelden(client, otp_code=codes[0])["status"] == 200
    client.cookies.clear()
    assert _anmelden(client, otp_code=codes[0])["status"] == 401


def test_ohne_zweiten_faktor_kein_zugang(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Passwort allein gibt kein Sitzungscookie, auch nicht mit Unsinn im zweiten Feld."""
    _totp_aktiv(db, owner_user)
    _passkey_einrichten(client, owner_cookies, {"otp_code": totp_now(GEHEIMNIS)})
    client.cookies.clear()
    for extra in ({}, {"otp_code": "000000"}, {"otp_code": "ABCD-EFGH"}):
        res = _anmelden(client, **extra)
        assert res["status"] in (200, 401), res
        assert "access_token" not in res["body"] or not res["body"]["access_token"], (extra, res)
        assert not any(k.endswith("access_token") for k in res["cookies"]), (extra, res)
        client.cookies.clear()
    # Gegenprobe: mit dem richtigen Code kommt das Cookie, die Pruefung oben greift also.
    ok = _anmelden(client, otp_code=totp_now(GEHEIMNIS, versatz=1))
    assert ok["status"] == 200 and any(k.endswith("access_token") for k in ok["cookies"]), ok


def test_passkeyliste_zeigt_nur_eigene(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict, user_cookies: dict
):
    _passkey_einrichten(client, owner_cookies, name="PC")
    assert client.get("/api/auth/2fa/passkeys", cookies=user_cookies).json() == []


# ── Name, Liste, Grenze ──────────────────────────────────────────────────


def test_passkey_name_steht_nur_verschluesselt(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from services.dis_client import DisClient

    _passkey_einrichten(client, owner_cookies, name="PC mit Windows Hello")

    roh = db.execute(text("SELECT name FROM user_passkeys")).scalar()
    assert roh.startswith(DisClient.PRAEFIX)
    assert "Windows" not in roh
    liste = client.get("/api/auth/2fa/passkeys", cookies=owner_cookies).json()
    assert [p["name"] for p in liste] == ["PC mit Windows Hello"]
    assert set(liste[0]) == {"id", "name", "created_at", "last_used_at"}


def test_hoechstens_zwanzig_passkeys(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    for i in range(19):
        db.add(UserPasskey(
            user_id=owner_user.id, credential_id=f"k{i}", public_key="x", algorithm=-7, rp_id="localhost",
        ))
    db.commit()
    db.expire_all()

    res = client.post(
        "/api/auth/2fa/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=_passkey_nachweis(client, owner_cookies, geraet),
    )
    assert res.status_code == 400
    assert "20" in res.json()["detail"]


def test_oauth_nennt_alle_faktoren(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from services import oauth_service
    from tests.test_oauth_router import _create_provider

    _totp_aktiv(db, owner_user)
    _passkey_einrichten(client, owner_cookies, {"otp_code": totp_now(GEHEIMNIS)})
    provider = _create_provider(db, slug="gh-mehr", preset="github")
    token = oauth_service.create_2fa_challenge(db, owner_user, provider)

    res = client.post("/api/oauth/gh-mehr/2fa/methode", headers=_kopf({}), json={"challenge": token})

    assert res.status_code == 200, res.text
    assert res.json()["methoden"] == ["passkey", "totp"]
    assert res.json()["passkey_options"]["rpId"] == "localhost"


# ── Migration ────────────────────────────────────────────────────────────


def test_migration_raeumt_reste_auf_und_laeuft_zurueck(pg_wegwerf) -> None:
    """Hin: Geheimnis ohne 2FA und 2FA ohne Faktor verschwinden, TOTP-Konten bleiben. Zurueck: laeuft."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from config import settings
    from database import Base

    db_url = pg_wegwerf("mehrere_faktoren")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260928_03")
        with engine.begin() as con:
            for uid, name, an, geheimnis in (
                (1, "totp", True, "echt"),
                (2, "adminrest", False, "rest"),
                (3, "leer", True, None),
                (4, "passkey", True, None),
            ):
                con.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, is_owner, is_active, "
                        "email_verified, two_factor_enabled, two_factor_secret_encrypted, "
                        "email_notifications, ai_notifications, device_notifications, "
                        "location_sharing_enabled, ai_desktop_systembereich, created_at) VALUES "
                        "(:id, :n, 'x', false, true, true, :an, :g, true, true, true, false, 'lesen', '2026-09-01')"
                    ),
                    {"id": uid, "n": name, "an": an, "g": geheimnis},
                )
            con.execute(text(
                "INSERT INTO user_passkeys (user_id, credential_id, public_key, algorithm, rp_id, sign_count, created_at) "
                "VALUES (4, 'k', 'x', -7, 'localhost', 0, '2026-09-01')"
            ))

        command.upgrade(config, "head")

        with engine.connect() as con:
            stand = {
                r[0]: (bool(r[1]), r[2])
                for r in con.execute(text("SELECT username, two_factor_enabled, two_factor_secret_encrypted FROM users"))
            }
        assert stand == {
            "totp": (True, "echt"),
            "adminrest": (False, None),
            "leer": (False, None),
            "passkey": (True, None),
        }

        def spalten() -> set[str]:
            return {s["name"] for s in inspect(engine).get_columns("users")}

        assert "two_factor_totp_last_step" in spalten()
        command.downgrade(config, "20260928_03")
        assert "two_factor_totp_last_step" not in spalten()
        command.upgrade(config, "head")
        assert "two_factor_totp_last_step" in spalten()
    finally:
        engine.dispose()
        settings.database_url = vorher
