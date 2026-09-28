"""Passkeys als zweiter Faktor — geprueft auf dem Server.

Bis 09/2026 genuegte `passkey_verified: true` im Request: wer das Passwort
kannte, uebersprang damit die 2FA **jedes** Kontos, auch eines mit
Authenticator-App. Die ersten Tests hier schicken genau diesen Request und
muessen scheitern.

Der Authenticator unten ist ein Software-Nachbau: er legt ein P-256-Paar an
und baut `clientDataJSON` und `authenticatorData` so, wie §5.8.1 und §6.1 der
WebAuthn-Spezifikation sie beschreiben. Geprueft wird also der echte Pfad —
Challenge, Herkunft, RP-ID-Hash, Flags, Unterschrift —, nur das Geraet ist
nachgebaut.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from config import settings
from database import Base
from models import BackupCode, User, UserPasskey
from services.auth_service import AuthService


HERKUNFT = "http://localhost:3000"


def _b64(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


class Authenticator:
    """Ein Passkey-Geraet in Software (ES256)."""

    def __init__(self, rp_id: str = "localhost") -> None:
        self.rp_id = rp_id
        self.schluessel = ec.generate_private_key(ec.SECP256R1())
        self.kennung = os.urandom(32)
        self.zaehler = 0

    def _client(self, typ: str, challenge: str, herkunft: str, extra: dict | None = None) -> bytes:
        return json.dumps(
            {"type": typ, "challenge": challenge, "origin": herkunft, "crossOrigin": False, **(extra or {})}
        ).encode()

    def _flags(self, uv: bool, at: bool = False) -> bytes:
        return bytes([0x01 | (0x04 if uv else 0) | (0x40 if at else 0)])

    def anlegen(self, optionen: dict, *, herkunft: str = HERKUNFT, uv: bool = True) -> dict:
        client = self._client("webauthn.create", optionen["challenge"], herkunft)
        auth = (
            hashlib.sha256(optionen["rp"]["id"].encode()).digest()
            + self._flags(uv, at=True)
            + self.zaehler.to_bytes(4, "big")
            + bytes(16)
            + len(self.kennung).to_bytes(2, "big")
            + self.kennung
            + b"\xa0"  # COSE-Schluessel; der Server liest stattdessen publicKey
        )
        spki = self.schluessel.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return {
            "id": _b64(self.kennung),
            "rawId": _b64(self.kennung),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64(client),
                "authenticatorData": _b64(auth),
                "publicKey": _b64(spki),
                "publicKeyAlgorithm": -7,
                "transports": ["internal"],
            },
        }

    def bestaetigen(
        self, optionen: dict, *, herkunft: str = HERKUNFT, uv: bool = True,
        zaehler: int | None = None, rp_id: str | None = None, client_extra: dict | None = None,
    ) -> dict:
        if zaehler is None:
            self.zaehler += 1
            zaehler = self.zaehler
        client = self._client("webauthn.get", optionen["challenge"], herkunft, client_extra)
        auth = (
            hashlib.sha256((rp_id or optionen["rpId"]).encode()).digest()
            + self._flags(uv)
            + zaehler.to_bytes(4, "big")
        )
        signatur = self.schluessel.sign(
            auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": _b64(self.kennung),
            "rawId": _b64(self.kennung),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64(client),
                "authenticatorData": _b64(auth),
                "signature": _b64(signatur),
                "userHandle": None,
            },
        }


def _kopf(cookies: dict) -> dict:
    return {"Origin": HERKUNFT, "X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _anmelden(client: TestClient, **extra) -> dict:
    res = client.post(
        "/api/auth/login",
        headers={"Origin": HERKUNFT},
        json={"username": "owner", "password": "OwnerPass123!", **extra},
    )
    return {"status": res.status_code, "body": res.json(), "cookies": dict(res.cookies)}


def _passkey_einrichten(client: TestClient, cookies: dict) -> Authenticator:
    geraet = Authenticator()
    optionen = client.post("/api/auth/2fa/passkey/options", headers=_kopf(cookies), cookies=cookies)
    assert optionen.status_code == 200, optionen.text
    res = client.post(
        "/api/auth/2fa/passkey/enable",
        headers=_kopf(cookies), cookies=cookies,
        json=geraet.anlegen(optionen.json()),
    )
    assert res.status_code == 200, res.text
    return geraet


def _schritt_optionen(client: TestClient, cookies: dict, zweck: str) -> dict:
    res = client.post(
        "/api/auth/passkey/options", headers=_kopf(cookies), cookies=cookies, json={"zweck": zweck}
    )
    assert res.status_code == 200, res.text
    return res.json()


# ── Die Luecke ───────────────────────────────────────────────────────────


def test_passkey_verified_oeffnet_kein_totp_konto_mehr(client: TestClient, db: Session, owner_user: User):
    """Der alte Angriff: Passwort plus `passkey_verified: true`, keine 2FA."""
    geheimnis = "JBSWY3DPEHPK3PXP"
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret(
        geheimnis, aad=f"msm:user:{owner_user.id}:2fa"
    )
    owner_user.two_factor_enabled = True
    db.commit()

    antwort = _anmelden(client, passkey_verified=True)

    assert antwort["status"] == 200
    assert antwort["body"]["requires_2fa"] is True
    assert antwort["body"]["two_factor_method"] == "totp"
    assert "passkey_options" not in antwort["body"] or antwort["body"]["passkey_options"] is None
    assert not any(k.endswith("access_token") for k in antwort["cookies"])


def test_passkey_verified_oeffnet_kein_passkey_konto(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()

    antwort = _anmelden(client, passkey_verified=True)

    assert antwort["body"]["requires_2fa"] is True
    assert not any(k.endswith("access_token") for k in antwort["cookies"])


def test_ein_passkey_nachweis_oeffnet_kein_totp_konto(client: TestClient, db: Session, owner_user: User):
    """Der Passkey-Weg zaehlt nur fuer Passkey-Konten — auch mit echter Unterschrift."""
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret(
        "JBSWY3DPEHPK3PXP", aad=f"msm:user:{owner_user.id}:2fa"
    )
    owner_user.two_factor_enabled = True
    db.commit()
    fremd = Authenticator().bestaetigen({"challenge": "x" * 43, "rpId": "localhost"})

    antwort = _anmelden(client, passkey=fremd)

    assert antwort["status"] == 401


# ── Einrichten ───────────────────────────────────────────────────────────


def test_einrichten_speichert_den_schluessel_und_nennt_die_methode(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    # Liegengebliebenes Geheimnis aus `/2fa/setup` — wie im Profil vor jedem Passkey.
    client.post("/api/auth/2fa/setup", headers=_kopf(owner_cookies), cookies=owner_cookies)
    db.refresh(owner_user)
    assert owner_user.two_factor_secret_encrypted

    geraet = _passkey_einrichten(client, owner_cookies)

    db.refresh(owner_user)
    assert owner_user.two_factor_enabled is True
    assert owner_user.two_factor_secret_encrypted is None
    gespeichert = db.query(UserPasskey).filter(UserPasskey.user_id == owner_user.id).one()
    assert gespeichert.credential_id == _b64(geraet.kennung)
    assert gespeichert.rp_id == "localhost"
    me = client.get("/api/auth/me", cookies=owner_cookies)
    assert me.json()["two_factor_method"] == "passkey"


def test_einrichten_ohne_nutzerpruefung_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    optionen = client.post("/api/auth/2fa/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies).json()
    res = client.post(
        "/api/auth/2fa/passkey/enable", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=Authenticator().anlegen(optionen, uv=False),
    )
    assert res.status_code == 400
    db.refresh(owner_user)
    assert owner_user.two_factor_enabled is False
    assert db.query(UserPasskey).count() == 0


def test_einrichten_von_fremder_herkunft_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    optionen = client.post("/api/auth/2fa/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies).json()
    res = client.post(
        "/api/auth/2fa/passkey/enable", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json=Authenticator().anlegen(optionen, herkunft="https://phish.example"),
    )
    assert res.status_code == 400
    assert db.query(UserPasskey).count() == 0


# ── Login ────────────────────────────────────────────────────────────────


def test_login_mit_passkey(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()

    schritt1 = _anmelden(client)
    assert schritt1["body"]["requires_2fa"] is True
    assert schritt1["body"]["two_factor_method"] == "passkey"
    optionen = schritt1["body"]["passkey_options"]
    assert optionen["rpId"] == "localhost"
    assert optionen["allowCredentials"][0]["id"] == _b64(geraet.kennung)

    nachweis = geraet.bestaetigen(optionen)
    schritt2 = _anmelden(client, passkey=nachweis)
    assert schritt2["status"] == 200, schritt2["body"]
    assert schritt2["body"]["requires_2fa"] is False

    # Dieselbe Antwort ein zweites Mal: die Challenge ist verbraucht.
    client.cookies.clear()
    assert _anmelden(client, passkey=nachweis)["status"] == 401


def test_login_mit_fremdem_passkey_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]

    fremd = Authenticator()
    fremd.kennung = geraet.kennung  # gleiche Kennung, anderer Schluessel

    assert _anmelden(client, passkey=fremd.bestaetigen(optionen))["status"] == 401


def test_login_ohne_nutzerpruefung_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]

    assert _anmelden(client, passkey=geraet.bestaetigen(optionen, uv=False))["status"] == 401


def test_login_von_fremder_seite_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]

    nachweis = geraet.bestaetigen(optionen, herkunft="https://phish.example")
    assert _anmelden(client, passkey=nachweis)["status"] == 401


def test_login_mit_falschem_rp_hash_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]

    nachweis = geraet.bestaetigen(optionen, rp_id="phish.example")
    assert _anmelden(client, passkey=nachweis)["status"] == 401


def test_ruecklaeufiger_zaehler_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """§7.2 Schritt 22: ein Zaehler, der nicht waechst, heisst womoeglich kopiert."""
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    assert _anmelden(client, passkey=geraet.bestaetigen(optionen, zaehler=5))["status"] == 200

    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    assert _anmelden(client, passkey=geraet.bestaetigen(optionen, zaehler=5))["status"] == 401


def test_fremdes_konto_im_passkey_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """§7.2 Schritt 6: ein `userHandle`, der ein anderes Konto nennt, gilt nicht."""
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    nachweis = geraet.bestaetigen(optionen)
    nachweis["response"]["userHandle"] = _b64(b"msm-user-999")

    assert _anmelden(client, passkey=nachweis)["status"] == 401


def test_eingebettet_in_fremde_seite_scheitert(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    optionen = _anmelden(client)["body"]["passkey_options"]
    # Sauber unterschrieben — nur eben aus einem iframe auf einer fremden Seite.
    nachweis = geraet.bestaetigen(optionen, client_extra={"topOrigin": "https://phish.example"})

    assert _anmelden(client, passkey=nachweis)["status"] == 401


def test_backup_code_bleibt_der_notausgang(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Ohne Passkey fuer diese Adresse (etwa in der Desktop-App) hilft der Backup-Code."""
    from services.backup_code_service import BackupCodeService

    _passkey_einrichten(client, owner_cookies)
    codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
    client.cookies.clear()

    assert _anmelden(client, otp_code=codes[0])["status"] == 200


# ── Geschuetzte Aktionen ─────────────────────────────────────────────────


def test_koppeln_mit_passkey(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)

    ohne = client.post(
        "/api/auth/devices/pairing", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"label": "Laptop", "otp_code": "123456"},
    )
    assert ohne.status_code == 403
    assert "Passkey" in ohne.json()["detail"]

    optionen = _schritt_optionen(client, owner_cookies, "device_pairing")
    mit = client.post(
        "/api/auth/devices/pairing", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"label": "Laptop", "passkey": geraet.bestaetigen(optionen)},
    )
    assert mit.status_code == 200, mit.text
    assert mit.json()["code"]


def test_nachweis_gilt_nur_fuer_seinen_zweck(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Ein Nachweis fuers Koppeln schaltet die 2FA nicht ab."""
    geraet = _passkey_einrichten(client, owner_cookies)
    optionen = _schritt_optionen(client, owner_cookies, "device_pairing")

    res = client.post(
        "/api/auth/2fa/disable", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"passkey": geraet.bestaetigen(optionen)},
    )

    assert res.status_code == 400
    db.refresh(owner_user)
    assert owner_user.two_factor_enabled is True


def test_login_zweck_ist_nicht_anforderbar(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _passkey_einrichten(client, owner_cookies)
    res = client.post(
        "/api/auth/passkey/options", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"zweck": "login"},
    )
    assert res.status_code == 400


def test_abschalten_mit_passkey_raeumt_auf(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from services.backup_code_service import BackupCodeService

    geraet = _passkey_einrichten(client, owner_cookies)
    BackupCodeService.generate_backup_codes(db, owner_user.id)
    optionen = _schritt_optionen(client, owner_cookies, "2fa_disable")

    res = client.post(
        "/api/auth/2fa/disable", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"passkey": geraet.bestaetigen(optionen)},
    )

    assert res.status_code == 200, res.text
    db.expire_all()
    assert owner_user.two_factor_enabled is False
    assert owner_user.two_factor_method is None
    assert db.query(UserPasskey).count() == 0
    assert db.query(BackupCode).filter(BackupCode.user_id == owner_user.id).count() == 0


def test_e2ee_neustart_mit_passkey(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)

    ohne = client.post(
        "/api/social/e2ee/devices/self/reset", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"passkey_verified": True},
    )
    assert ohne.status_code == 403

    optionen = _schritt_optionen(client, owner_cookies, "e2ee_reset")
    mit = client.post(
        "/api/social/e2ee/devices/self/reset", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"passkey": geraet.bestaetigen(optionen)},
    )
    assert mit.status_code == 200, mit.text


def test_passwort_aendern_mit_passkey(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Bis 09/2026 verlangte die Seite hier einen TOTP-Code, den ein Passkey-Konto nicht hat."""
    geraet = _passkey_einrichten(client, owner_cookies)
    daten = {"current_password": "OwnerPass123!", "new_password": "NeuesPasswort123!"}

    ohne = client.post("/api/auth/change-password", headers=_kopf(owner_cookies), cookies=owner_cookies, json=daten)
    assert ohne.status_code == 401

    optionen = _schritt_optionen(client, owner_cookies, "password_change")
    mit = client.post(
        "/api/auth/change-password", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={**daten, "passkey": geraet.bestaetigen(optionen)},
    )
    assert mit.status_code == 200, mit.text


def test_admin_abschaltung_nimmt_die_passkeys_mit(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _passkey_einrichten(client, owner_cookies)
    res = client.patch(
        f"/api/admin/users/{owner_user.id}", headers=_kopf(owner_cookies), cookies=owner_cookies,
        json={"two_factor_enabled": False},
    )
    assert res.status_code == 200, res.text
    assert db.query(UserPasskey).count() == 0


# ── Zwischenschein statt zweitem Captcha ─────────────────────────────────


def _einmal_captcha(monkeypatch):
    """ALTCHA-Token gelten nur einmal — genau so wie im Sidecar."""
    from fastapi import HTTPException
    from services.captcha_service import CaptchaService

    verbraucht: set[str] = set()

    async def pruefen(token, client_ip=None):
        if not token or token in verbraucht:
            raise HTTPException(status_code=400, detail="CAPTCHA-Verifizierung fehlgeschlagen. Bitte erneut versuchen.")
        verbraucht.add(token)

    monkeypatch.setattr(CaptchaService, "verify_token", staticmethod(pruefen))


def test_zweiter_schritt_braucht_kein_zweites_captcha(client: TestClient, db: Session, owner_user: User, monkeypatch):
    """Bis 09/2026 schickte der zweite Schritt dasselbe Captcha-Token noch einmal — und scheiterte."""
    from tests._totp import totp_now

    geheimnis = "JBSWY3DPEHPK3PXP"
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret(geheimnis, aad=f"msm:user:{owner_user.id}:2fa")
    owner_user.two_factor_enabled = True
    db.commit()
    _einmal_captcha(monkeypatch)

    schritt1 = _anmelden(client, captcha_token="t1")
    assert schritt1["body"]["requires_2fa"] is True
    schein = schritt1["body"]["login_challenge"]
    assert schein

    # Das alte Verhalten: dasselbe Token noch einmal -> abgelehnt.
    assert _anmelden(client, captcha_token="t1", otp_code=totp_now(geheimnis))["status"] == 400

    schritt2 = _anmelden(client, captcha_token="t1", login_challenge=schein, otp_code=totp_now(geheimnis))
    assert schritt2["status"] == 200, schritt2["body"]
    assert schritt2["body"]["requires_2fa"] is False

    # Verbraucht: ein zweites Mal nicht — und die Seite erfaehrt, dass sie neu beginnen muss.
    client.cookies.clear()
    assert _anmelden(client, login_challenge=schein, otp_code=totp_now(geheimnis))["status"] == 403


def test_zwischenschein_haelt_nur_drei_fehlversuche(client: TestClient, db: Session, owner_user: User, monkeypatch):
    """Sonst liessen sich Codes nach einem einzigen Captcha ohne Ende durchprobieren."""
    from tests._totp import totp_now

    geheimnis = "JBSWY3DPEHPK3PXP"
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret(geheimnis, aad=f"msm:user:{owner_user.id}:2fa")
    owner_user.two_factor_enabled = True
    db.commit()
    _einmal_captcha(monkeypatch)
    schein = _anmelden(client, captcha_token="t1")["body"]["login_challenge"]

    # Nachfragen ohne Code gibt keinen neuen Schein — der Zaehler bleibt am alten.
    nachgefragt = _anmelden(client, login_challenge=schein)
    assert nachgefragt["body"]["requires_2fa"] is True
    assert nachgefragt["body"]["login_challenge"] == ""

    assert _anmelden(client, login_challenge=schein, otp_code="000000")["status"] == 401
    assert _anmelden(client, login_challenge=schein, otp_code="000001")["status"] == 401
    assert _anmelden(client, login_challenge=schein, otp_code="000002")["status"] == 403
    # Auch der richtige Code hilft mit diesem Schein nicht mehr.
    assert _anmelden(client, login_challenge=schein, otp_code=totp_now(geheimnis))["status"] == 403


def test_zwischenschein_gilt_nur_fuer_sein_konto(client: TestClient, db: Session, owner_user: User, regular_user: User, monkeypatch):
    """Mit dem Schein von A laesst sich das Passwort von B nicht ohne Captcha raten."""
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret("JBSWY3DPEHPK3PXP", aad=f"msm:user:{owner_user.id}:2fa")
    owner_user.two_factor_enabled = True
    db.commit()
    _einmal_captcha(monkeypatch)
    schein = _anmelden(client, captcha_token="t1")["body"]["login_challenge"]

    richtig = client.post(
        "/api/auth/login", headers={"Origin": HERKUNFT},
        json={"username": "user1", "password": "UserPass123!", "login_challenge": schein},
    )
    falsch = client.post(
        "/api/auth/login", headers={"Origin": HERKUNFT},
        json={"username": "user1", "password": "falsch", "login_challenge": schein},
    )
    # Beide gleich: kein Hinweis, ob das Passwort stimmt.
    assert richtig.status_code == falsch.status_code == 401
    assert richtig.json() == falsch.json()


def test_passkey_login_mit_zwischenschein(client: TestClient, db: Session, owner_user: User, owner_cookies: dict, monkeypatch):
    geraet = _passkey_einrichten(client, owner_cookies)
    client.cookies.clear()
    _einmal_captcha(monkeypatch)

    schritt1 = _anmelden(client, captcha_token="t1")
    antwort = _anmelden(
        client, captcha_token="t1", login_challenge=schritt1["body"]["login_challenge"],
        passkey=geraet.bestaetigen(schritt1["body"]["passkey_options"]),
    )
    assert antwort["status"] == 200, antwort["body"]


# ── Altbestand ───────────────────────────────────────────────────────────


def test_migration_schaltet_den_ungeprueften_passkey_ab(tmp_path: Path, pg_wegwerf) -> None:
    """Konten mit dem alten Browser-„Passkey" verlieren die 2FA, TOTP-Konten nicht.

    Das Passkey-Konto hat ein liegengebliebenes TOTP-Geheimnis (das Profil rief
    vor jeder Einrichtung `/2fa/setup` auf) — erkannt wird es am Audit-Log.
    """
    db_url = pg_wegwerf("passkey")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260926_09")
        with engine.begin() as con:
            for uid, name, geheimnis in (
                (1, "passkeyalt", "rest"),
                (2, "totp", "echt"),
                (3, "ohnegeheimnis", None),
                (4, "gewechselt", "echt2"),
            ):
                con.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, is_owner, is_active, "
                        "email_verified, two_factor_enabled, two_factor_secret_encrypted, "
                        "email_notifications, ai_notifications, device_notifications, "
                        "location_sharing_enabled, ai_desktop_systembereich, created_at) VALUES "
                        "(:id, :n, 'x', false, true, true, true, :g, true, true, true, false, 'lesen', '2026-09-01')"
                    ),
                    {"id": uid, "n": name, "g": geheimnis},
                )
            for uid, aktion, zeit in (
                (1, "auth.2fa.passkey.enable", "2026-09-10"),
                (2, "auth.2fa.enable", "2026-09-10"),
                (4, "auth.2fa.passkey.enable", "2026-09-01"),
                (4, "auth.2fa.enable", "2026-09-05"),
            ):
                con.execute(
                    text(
                        "INSERT INTO audit_logs (user_id, action, origin, created_at) "
                        "VALUES (:u, :a, 'direct', :z)"
                    ),
                    {"u": uid, "a": aktion, "z": zeit},
                )
            con.execute(text("INSERT INTO backup_codes (user_id, code_hash, created_at) VALUES (1, 'h', '2026-09-10')"))

        command.upgrade(config, "head")

        with engine.connect() as con:
            stand = dict(
                (r[0], (bool(r[1]), r[2]))
                for r in con.execute(
                    text("SELECT username, two_factor_enabled, two_factor_secret_encrypted FROM users")
                )
            )
            codes = con.execute(text("SELECT COUNT(*) FROM backup_codes WHERE user_id = 1")).scalar()
        assert stand["passkeyalt"] == (False, None)
        assert stand["ohnegeheimnis"] == (False, None)
        assert stand["totp"] == (True, "echt")
        assert stand["gewechselt"] == (True, "echt2")
        assert codes == 0
    finally:
        engine.dispose()
        settings.database_url = vorher


def test_parallele_fehlversuche_am_zwischenschein_zaehlen_alle(client: TestClient, db: Session, owner_user: User, monkeypatch):
    """Bis 27.09.2026 las jede Anfrage den Zaehler, pruefte den Code und schrieb
    den Zaehler dann zurueck. Zwei Anfragen zugleich zaehlten als eine, und der
    Schein blieb nach drei Fehlversuchen gueltig."""
    import database as db_module
    from fastapi import HTTPException
    from routers import auth as auth_router
    from schemas.auth import LoginRequest
    from services import login_challenge_service
    from tests._totp import totp_now

    geheimnis = "JBSWY3DPEHPK3PXP"
    owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret(geheimnis, aad=f"msm:user:{owner_user.id}:2fa")
    owner_user.two_factor_enabled = True
    db.commit()
    _einmal_captcha(monkeypatch)
    schein = _anmelden(client, captcha_token="t1")["body"]["login_challenge"]

    echt = AuthService.verify_current_2fa_code
    pruefungen: list[str] = []

    def pruefen(user, code):
        pruefungen.append(code)
        if len(pruefungen) == 1:
            # Eine zweite Anfrage kommt an, waehrend die erste noch prueft.
            andere = db_module.SessionLocal()
            try:
                zeile = login_challenge_service.lookup_valid(andere, schein, "login_2fa")
                konto = andere.get(User, owner_user.id)
                anfrage = LoginRequest(username="owner", password="x", otp_code="000001", login_challenge=schein)
                try:
                    auth_router._login_zweiter_faktor(andere, konto, anfrage, None, zeile)
                except HTTPException:
                    pass
            finally:
                andere.close()
        return echt(user, code)

    monkeypatch.setattr(AuthService, "verify_current_2fa_code", staticmethod(pruefen))

    assert _anmelden(client, login_challenge=schein, otp_code="000000")["status"] == 401
    assert _anmelden(client, login_challenge=schein, otp_code="000002")["status"] == 403
    assert _anmelden(client, login_challenge=schein, otp_code=totp_now(geheimnis))["status"] == 403
    # Drei Pruefungen, nicht mehr: der vierte Code wird gar nicht erst angesehen.
    assert len(pruefungen) == 3
