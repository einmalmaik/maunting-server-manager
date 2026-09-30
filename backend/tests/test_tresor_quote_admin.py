"""Tresor-Cloud: Speicher je Konto einstellen (Phase 7, 09/2026).

Die Vorgabe steht in den Panel-Einstellungen, ein Konto kann einen eigenen
Wert bekommen. Die Benutzerverwaltung sieht je Konto nur Zahlen.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, VaultBlob
from services import vault_blob_service
from tests.test_admin_router import _attach_role_with_keys, _csrf

GB = 1024**3


def _blob(db: Session, user: User, bytes_total: int, state: str = "fertig") -> None:
    import secrets

    db.add(
        VaultBlob(
            id=secrets.token_hex(16),
            user_id=user.id,
            chunk_count=1,
            bytes_total=bytes_total,
            delete_verifier="0" * 64,
            state=state,
        )
    )
    db.commit()


def test_vorgabe_aus_den_panel_einstellungen(client: TestClient, owner_cookies: dict, regular_user: User, db: Session):
    assert client.get("/api/settings", cookies=owner_cookies).json()["vault_cloud_quota_bytes"] == 10 * GB

    antwort = client.post(
        "/api/settings", cookies=owner_cookies, headers=_csrf(owner_cookies), json={"vault_cloud_quota_bytes": 2 * GB}
    )
    assert antwort.status_code == 200
    assert client.get("/api/settings", cookies=owner_cookies).json()["vault_cloud_quota_bytes"] == 2 * GB
    assert vault_blob_service.quote_fuer(regular_user) == 2 * GB

    for falsch in (-1, 1024**5 + 1):
        antwort = client.post(
            "/api/settings", cookies=owner_cookies, headers=_csrf(owner_cookies), json={"vault_cloud_quota_bytes": falsch}
        )
        assert antwort.status_code == 422
    assert vault_blob_service.standard_quote() == 2 * GB


def test_eigener_wert_je_konto_und_zurueck_auf_die_vorgabe(
    client: TestClient, owner_cookies: dict, owner_user: User, regular_user: User, db: Session
):
    _blob(db, regular_user, 3000)
    _blob(db, regular_user, 500, state="geloescht")

    antwort = client.put(
        f"/api/admin/users/{regular_user.id}/tresor-quote",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
        json={"quote_bytes": 5 * GB},
    )
    assert antwort.status_code == 200
    assert antwort.json() == {"quote": 5 * GB, "eigene_quote": 5 * GB}

    liste = client.get("/api/admin/tresor-speicher", cookies=owner_cookies).json()
    assert liste["standard"] == 10 * GB
    je = {k["user_id"]: k for k in liste["konten"]}
    # Die Loeschhaltung belegt die Platte noch und zaehlt mit.
    assert je[regular_user.id] == {"user_id": regular_user.id, "belegt": 3500, "quote": 5 * GB, "eigene_quote": 5 * GB}
    assert je[owner_user.id] == {"user_id": owner_user.id, "belegt": 0, "quote": 10 * GB, "eigene_quote": None}

    antwort = client.put(
        f"/api/admin/users/{regular_user.id}/tresor-quote",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
        json={"quote_bytes": None},
    )
    assert antwort.json() == {"quote": 10 * GB, "eigene_quote": None}
    db.refresh(regular_user)
    assert regular_user.vault_quota_bytes is None


def test_nur_mit_benutzerverwaltung_und_den_owner_nur_der_owner(
    client: TestClient, user_cookies: dict, owner_user: User, regular_user: User, db: Session
):
    assert client.get("/api/admin/tresor-speicher", cookies=user_cookies).status_code == 403
    antwort = client.put(
        f"/api/admin/users/{regular_user.id}/tresor-quote",
        cookies=user_cookies,
        headers=_csrf(user_cookies),
        json={"quote_bytes": 100 * GB},
    )
    assert antwort.status_code == 403

    _attach_role_with_keys(db, regular_user, "benutzerverwaltung", ["users.manage", "users.read"])
    assert client.get("/api/admin/tresor-speicher", cookies=user_cookies).status_code == 200
    antwort = client.put(
        f"/api/admin/users/{owner_user.id}/tresor-quote",
        cookies=user_cookies,
        headers=_csrf(user_cookies),
        json={"quote_bytes": 0},
    )
    assert antwort.status_code == 403
    db.refresh(owner_user)
    assert owner_user.vault_quota_bytes is None
