"""Ein App-Code und ein Backup-Code gelten je einmal, auch bei zwei Anfragen zugleich.

Bis 5.0.1 galt ein App-Code, solange er gueltig war (bis zu 90 s), beliebig
oft: wer ihn mitlas, konnte ihn nach dem Inhaber noch einmal benutzen. Ein
Backup-Code wurde gelesen und danach als benutzt markiert; zwei Anfragen
zugleich lasen ihn beide als unbenutzt und kamen beide durch (AGENTS.md
Punkt 43).

Die Wettlauf-Tests brauchen echte, getrennte Verbindungen: die Suite teilt je
Worker eine (`StaticPool`), darauf gaebe es keinen Nebenlauf. Eine Barriere
haelt beide Anfragen an der Stelle fest, an der die alte Pruefung gelesen und
noch nicht geschrieben hatte.
"""

from __future__ import annotations

import threading
from typing import Callable

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import database as db_module
from models import BackupCode, User
from services.auth_service import AuthService
from services.backup_code_service import BackupCodeService
from services.dis_client import DisClient
from tests._totp import totp_now
from tests.test_passkey_2fa import _anmelden, _kopf

GEHEIMNIS = "JBSWY3DPEHPK3PXP"


def _totp_aktiv(db: Session, user: User) -> None:
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(GEHEIMNIS, aad=f"msm:user:{user.id}:2fa")
    user.two_factor_enabled = True
    db.commit()


def _zugleich(arbeit: Callable[[Session], bool], barriere_an: Callable[[object], None] | None = None) -> list[bool]:
    """Fuehrt ``arbeit`` in zwei Threads mit je eigener Verbindung aus."""
    engine = create_engine(db_module.engine.url, poolclass=NullPool)
    if barriere_an is not None:
        barriere_an(engine)
    sitzung = sessionmaker(bind=engine)
    ergebnisse: list[bool | None] = [None, None]
    fehler: list[BaseException] = []

    def lauf(i: int) -> None:
        s = sitzung()
        try:
            ergebnisse[i] = arbeit(s)
        except BaseException as e:  # noqa: BLE001 - im Test sichtbar machen
            fehler.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=lauf, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    engine.dispose()
    assert not fehler, fehler
    assert all(e is not None for e in ergebnisse), "ein Thread hing"
    return ergebnisse  # type: ignore[return-value]


def _warten(barriere: threading.Barrier) -> None:
    try:
        barriere.wait(timeout=5)
    except threading.BrokenBarrierError:
        pass


# ── App-Code ─────────────────────────────────────────────────────────────


def test_app_code_meldet_nur_einmal_an(client: TestClient, db: Session, owner_user: User):
    _totp_aktiv(db, owner_user)
    code = totp_now(GEHEIMNIS)

    assert _anmelden(client, otp_code=code)["status"] == 200
    client.cookies.clear()
    assert _anmelden(client, otp_code=code)["status"] == 401
    client.cookies.clear()
    # Der naechste Code gilt sofort, gesperrt ist nur der benutzte.
    assert _anmelden(client, otp_code=totp_now(GEHEIMNIS, versatz=1))["status"] == 200


def test_app_code_aus_dem_login_ist_kein_nachweis_mehr(
    client: TestClient, db: Session, owner_user: User
):
    """Wer den Code beim Login mitliest, erzeugt damit keine eigenen Backup-Codes."""
    _totp_aktiv(db, owner_user)
    code = totp_now(GEHEIMNIS)
    login = _anmelden(client, otp_code=code)
    assert login["status"] == 200
    cookies = login["cookies"]

    res = client.post(
        "/api/auth/2fa/backup/generate", headers=_kopf(cookies), cookies=cookies, json={"otp_code": code}
    )
    assert res.status_code == 403
    assert db.query(BackupCode).filter(BackupCode.user_id == owner_user.id).count() == 0


def test_aelterer_code_nach_einem_neueren_gilt_nicht(db: Session, owner_user: User):
    _totp_aktiv(db, owner_user)
    neuer = totp_now(GEHEIMNIS, versatz=1)
    aelter = totp_now(GEHEIMNIS)

    assert AuthService.verify_current_2fa_code(owner_user, neuer) is True
    assert AuthService.verify_current_2fa_code(owner_user, aelter) is False


def test_falscher_code_belegt_nichts(db: Session, owner_user: User):
    _totp_aktiv(db, owner_user)
    richtig = totp_now(GEHEIMNIS)
    falsch = "000000" if richtig != "000000" else "111111"

    assert AuthService.verify_current_2fa_code(owner_user, falsch) is False
    assert AuthService.verify_current_2fa_code(owner_user, richtig) is True


def test_app_code_zweimal_zugleich_gilt_einmal(db: Session, owner_user: User, monkeypatch):
    _totp_aktiv(db, owner_user)
    code = totp_now(GEHEIMNIS)
    konto = owner_user.id
    barriere = threading.Barrier(2)

    # Beide Anfragen haben den Code gegen das Geheimnis geprueft, bevor eine
    # von ihnen etwas schreibt.
    echt = getattr(DisClient, "totp_schritt", None)
    if echt is not None:
        def gebremst(secret: str, c: str):
            schritt = echt(secret, c)
            _warten(barriere)
            return schritt

        monkeypatch.setattr(DisClient, "totp_schritt", staticmethod(gebremst))

    def pruefen(s: Session) -> bool:
        return AuthService.verify_current_2fa_code(s.get(User, konto), code)

    assert sorted(_zugleich(pruefen)) == [False, True]


# ── Backup-Code ──────────────────────────────────────────────────────────


def test_backup_code_zweimal_zugleich_gilt_einmal(db: Session, owner_user: User):
    codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
    konto = owner_user.id
    barriere = threading.Barrier(2)
    gewartet = threading.local()

    def an_der_tabelle(engine) -> None:
        # Die erste Anweisung jedes Threads an `backup_codes` wartet auf die des
        # anderen: die alte Pruefung hatte dann beide gelesen, keine geschrieben.
        @event.listens_for(engine, "before_cursor_execute")
        def _halt(conn, cursor, statement, parameters, context, executemany):
            if "backup_codes" in statement and not getattr(gewartet, "ja", False):
                gewartet.ja = True
                _warten(barriere)

    def pruefen(s: Session) -> bool:
        return BackupCodeService.validate_backup_code(s, konto, codes[0])

    assert sorted(_zugleich(pruefen, an_der_tabelle)) == [False, True]
    db.expire_all()
    assert BackupCodeService.get_remaining_count(db, konto) == 4


def test_backup_code_meldet_nur_einmal_an(client: TestClient, db: Session, owner_user: User):
    _totp_aktiv(db, owner_user)
    codes = BackupCodeService.generate_backup_codes(db, owner_user.id)

    assert _anmelden(client, otp_code=codes[0])["status"] == 200
    client.cookies.clear()
    assert _anmelden(client, otp_code=codes[0])["status"] == 401
    client.cookies.clear()
    assert _anmelden(client, otp_code=codes[1])["status"] == 200
