"""Was eine Benutzerloeschung blockiert, muss sie sagen — nicht abstuerzen.

Drei Fremdschluessel des Panels tragen ``ON DELETE RESTRICT`` und zeigen alle
auf denselben Benutzer. Kein Loeschpfad hat je nachgesehen: ``db.delete(user)``
lief in den Fremdschluessel, die ``IntegrityError`` fiel ungefangen aus
``db.commit()`` und wurde zu einer nackten HTTP 500 — der Account blieb
bestehen, ohne dass irgendwo stand, warum. Wer einmal ein Team gegruendet hatte,
war dauerhaft nicht mehr loeschbar.

Die Tests haengen an der scharfen Fremdschluesselpruefung aus ``conftest.py``.
Ohne sie wuerde SQLite jeden dieser Faelle stumm durchwinken und die Datei waere
gruen, ohne etwas zu belegen — die Zusage darueber steht ausdruecklich in
``test_schema_constraints.py``.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    HosterIntegration,
    Server,
    ServerCredentialBinding,
    Team,
    User,
    UserCredential,
)
from services.auth_service import AuthService


def _csrf(cookies: dict) -> dict:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _kandidat(db: Session) -> User:
    """Ein gewoehnlicher Benutzer, den der Owner gleich zu loeschen versucht."""
    user = AuthService.create_user(
        db, "loeschkandidat", "loeschkandidat@test.de", "UserPass123!"
    )
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def test_der_gruender_eines_teams_bekommt_eine_antwort_statt_eines_absturzes(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Der belegte Kern des Befunds, auf dem Weg, den ein Admin wirklich geht."""
    kandidat = _kandidat(db)
    db.add(Team(name="Crew", owner_user_id=kandidat.id, personal_for_user_id=None))
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    # Der Name muss drinstehen: ohne ihn weiss der Betreiber nicht, was er
    # aufloesen soll, und die Meldung waere so wertlos wie die 500 davor.
    assert "Crew" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_gebundene_zugangsdaten_nennen_den_grund_ihrer_sperre(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Das CASCADE des Credentials laeuft in das RESTRICT seiner Bindung."""
    kandidat = _kandidat(db)
    server = Server(
        name="cred-server", game_type="minecraft", install_dir="/tmp/cred-server"
    )
    db.add(server)
    db.flush()
    credential = UserCredential(
        user_id=kandidat.id,
        kind="github_token",
        label="standard",
        secret_encrypted="x",
    )
    db.add(credential)
    db.flush()
    db.add(
        ServerCredentialBinding(
            server_id=server.id, kind="github_token", credential_id=credential.id
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    assert "Zugangsdaten" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_das_dienstkonto_einer_hoster_anbindung_bleibt_bestehen(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Derselbe Defekt am dritten RESTRICT — gleicher Bauplan, gleiche Wirkung."""
    kandidat = _kandidat(db)
    db.add(
        HosterIntegration(
            name="Beispielshop",
            slug="beispielshop",
            service_user_id=kandidat.id,
            api_key_hash="a" * 64,
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    assert "Beispielshop" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_das_persoenliche_team_haelt_niemanden_fest(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Gegenprobe: die Vorpruefung darf nicht jeden Benutzer unloeschbar machen.

    Dieser Fall ginge auch ohne den Fix durch (auf SQLite greift das CASCADE
    ueber ``personal_for_user_id``). Er steht hier trotzdem, weil er die andere
    Haelfte der Zusage haelt: das Ein-Mann-Team gehoert nur diesem Benutzer und
    darf ihn nicht blockieren.
    """
    kandidat = _kandidat(db)
    db.add(
        Team(
            name=kandidat.username,
            owner_user_id=kandidat.id,
            personal_for_user_id=kandidat.id,
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 200
    assert db.query(User).filter(User.id == kandidat.id).first() is None
    assert db.query(Team).filter(Team.personal_for_user_id == kandidat.id).first() is None


def test_die_selbstloeschung_nimmt_denselben_weg(
    db: Session, regular_user: User
) -> None:
    """Der zweite Loeschpfad hatte dieselbe Luecke und wurde leicht uebersehen.

    `auth_service.delete_account_atomically` raeumt JwtBlacklist,
    EmailVerification, AuditLog und ServerPermission ab — Teams kamen darin nie
    vor. Ohne den Fix fliegt hier eine `IntegrityError` statt einer
    `HTTPException`.
    """
    db.add(Team(name="Crew", owner_user_id=regular_user.id, personal_for_user_id=None))
    db.commit()

    with pytest.raises(HTTPException) as fehler:
        AuthService.delete_account_atomically(db, regular_user)

    assert fehler.value.status_code == 409
    db.rollback()
    assert db.query(User).filter(User.id == regular_user.id).first() is not None
