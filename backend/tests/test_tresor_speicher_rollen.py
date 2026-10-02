"""Tresor-Cloud: Speicher je Rolle (10/2026).

Ein Konto bekommt den hoechsten Speicher seiner Rollen. Hat keine seiner Rollen
Speicher, laedt es nichts hoch. Der Owner ist wie bei den Rechten frei.
Eingestellt wird unter Einstellungen -> Tresorspeicher, wie das KI-Kontingent.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, Role, User, UserRole
from services import vault_blob_service
from tests.test_admin_router import _attach_role_with_keys, _csrf

GB = 1024**3


def _rolle(db: Session, name: str, speicher: int | None = None) -> Role:
    rolle = Role(name=name, description=None, is_system=False)
    db.add(rolle)
    db.flush()
    if speicher is not None:
        vault_blob_service.rolle_speicher_setzen(db, rolle.id, speicher)
    db.commit()
    return rolle


def _geben(db: Session, user: User, rolle: Role) -> None:
    db.add(UserRole(user_id=user.id, role_id=rolle.id))
    db.commit()


def test_ohne_rolle_mit_speicher_gibt_es_keinen(db: Session, regular_user: User):
    assert vault_blob_service.quote_fuer(db, regular_user) == 0
    _geben(db, regular_user, _rolle(db, "ohne_speicher"))
    assert vault_blob_service.quote_fuer(db, regular_user) == 0


def test_der_hoechste_wert_der_rollen_gilt(db: Session, regular_user: User):
    _geben(db, regular_user, _rolle(db, "klein", 2 * GB))
    assert vault_blob_service.quote_fuer(db, regular_user) == 2 * GB
    _geben(db, regular_user, _rolle(db, "gross", 50 * GB))
    assert vault_blob_service.quote_fuer(db, regular_user) == 50 * GB
    # Die alte Spalte `users.role_id` zaehlt mit.
    regular_user.role_id = _rolle(db, "alt", 80 * GB).id
    db.commit()
    assert vault_blob_service.quote_fuer(db, regular_user) == 80 * GB


def test_der_owner_ist_frei(db: Session, owner_user: User):
    assert vault_blob_service.quote_fuer(db, owner_user) == vault_blob_service.MAX_QUOTE


def test_einstellen_je_rolle(client: TestClient, owner_cookies: dict, regular_user: User, db: Session):
    rolle = _rolle(db, "kunde")
    _geben(db, regular_user, rolle)

    liste = {r["role_name"]: r for r in client.get("/api/settings/tresor-speicher", cookies=owner_cookies).json()}
    assert liste["kunde"] == {"role_id": rolle.id, "role_name": "kunde", "quota_bytes": None}

    antwort = client.put(
        f"/api/settings/tresor-speicher/{rolle.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
        json={"quota_bytes": 5 * GB},
    )
    assert antwort.status_code == 200
    assert vault_blob_service.quote_fuer(db, regular_user) == 5 * GB
    eintrag = db.query(AuditLog).filter(AuditLog.action == "vault.role_quota.updated").one()
    assert eintrag.target_id == str(rolle.id)

    antwort = client.put(
        f"/api/settings/tresor-speicher/{rolle.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
        json={"quota_bytes": None},
    )
    assert antwort.status_code == 200
    assert vault_blob_service.rolle_speicher(db, rolle.id) is None
    assert vault_blob_service.quote_fuer(db, regular_user) == 0

    # `true` machte Pydantic bis 02.10.2026 zu 1: die Rolle bekam 1 Byte Speicher.
    for falsch in (-1, vault_blob_service.MAX_QUOTE + 1, True):
        antwort = client.put(
            f"/api/settings/tresor-speicher/{rolle.id}",
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
            json={"quota_bytes": falsch},
        )
        assert antwort.status_code == 422
    antwort = client.put(
        "/api/settings/tresor-speicher/999999",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
        json={"quota_bytes": GB},
    )
    assert antwort.status_code == 404


def test_nur_mit_den_panel_einstellungen(client: TestClient, user_cookies: dict, regular_user: User, db: Session):
    rolle = _rolle(db, "kunde")
    assert client.get("/api/settings/tresor-speicher", cookies=user_cookies).status_code == 403
    _attach_role_with_keys(db, regular_user, "lesen", ["panel.settings.read"])
    assert client.get("/api/settings/tresor-speicher", cookies=user_cookies).status_code == 200
    antwort = client.put(
        f"/api/settings/tresor-speicher/{rolle.id}",
        cookies=user_cookies,
        headers=_csrf(user_cookies),
        json={"quota_bytes": 100 * GB},
    )
    assert antwort.status_code == 403
    assert vault_blob_service.rolle_speicher(db, rolle.id) is None


def test_die_rolle_loeschen_nimmt_den_speicher_mit(db: Session):
    rolle = _rolle(db, "weg", GB)
    db.delete(rolle)
    db.commit()
    assert vault_blob_service.rolle_speicher(db, rolle.id) is None
