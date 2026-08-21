"""Security- und Idempotenz-Invarianten der Hoster-Anbindung (Phase 6).

Geprueft werden vor allem die Zusagen aus dem Zielbild:
- Wiederholte Auftraege erzeugen keine doppelten Server.
- Der Shop kann nie mehr als der ihm zugewiesene Dienstbenutzer.
- Kunden sehen ausschliesslich ihren eigenen Server.
- Secrets erscheinen nur einmal und nie in Leseantworten oder im Audit.
- Eine Kuendigung vernichtet nicht sofort alle Daten.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AuditLog,
    HosterHandoff,
    HosterIdentity,
    HosterIntegration,
    HosterProduct,
    HosterService,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from models.hoster import hash_token
from services import hoster_integration_service, permission_service
from services.auth_service import AuthService
from services.permission_catalog import SYSTEM_ROLE_USER
from services.role_service import effective_user_role_ids, set_user_roles


API_KEY_HEADER = hoster_integration_service.API_KEY_HEADER


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _service_user(db: Session, *, with_create: bool = True) -> User:
    """Ein Panel-Benutzer, in dessen Namen die Integration handelt."""
    user = AuthService.create_user(db, "shop-service", "shop-service@test.de", "ShopPass123!")
    role = Role(name="hoster-service", is_system=False)
    db.add(role)
    db.flush()
    if with_create:
        db.add(RolePermission(role_id=role.id, permission_key="servers.create"))
    db.add(RolePermission(role_id=role.id, permission_key="servers.delete"))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.refresh(user)
    return user


def _integration(db: Session, service_user: User) -> tuple[HosterIntegration, str]:
    integration, api_key = hoster_integration_service.create_integration(
        db,
        name="Testshop",
        slug="testshop",
        enabled=True,
        service_user_id=service_user.id,
        webhook_url=None,
        terminate_grace_days=7,
    )
    db.commit()
    db.refresh(integration)
    return integration, api_key


def _role(db: Session, name: str, *, keys: tuple[str, ...] = ()) -> Role:
    """Eine globale Rolle, wie ein Betreiber sie fuer einen Tarif anlegen wuerde.

    Ohne Keys ist es genau der Regelfall des Produktfelds: eine Rolle, an der
    nur ein KI-Kontingent haengt und kein einziges Panelrecht.
    """
    role = Role(name=name, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    db.refresh(role)
    return role


def _product(
    db: Session,
    integration: HosterIntegration,
    *,
    role_id: int | None = None,
    key: str = "mc-8gb",
) -> None:
    hoster_integration_service.upsert_product(
        db,
        integration=integration,
        external_product_key=key,
        game_type="dayz",
        ram_limit_mb=8192,
        cpu_limit_percent=200,
        disk_limit_gb=50,
        node_id=None,
        backup_interval_hours=None,
        role_id=role_id,
        enabled=True,
    )
    db.commit()


def _customer_roles(db: Session, external_service_id: str) -> set[int]:
    """Die globalen Rollen des Kunden hinter einem Vertrag, frisch gelesen.

    Der Vertragspfad schreibt ueber dieselbe Session, die der Testclient
    benutzt; ohne das Verwerfen der Bezeichner sieht der Test den Stand von
    vorher und waere gruen, ohne etwas gemessen zu haben.
    """
    db.expire_all()
    service = (
        db.query(HosterService)
        .filter(HosterService.external_service_id == external_service_id)
        .one()
    )
    customer = db.query(User).filter(User.id == service.identity.user_id).one()
    return set(effective_user_role_ids(db, customer))


def _fake_provision(db: Session):
    """Ersetzt die echte Provisionierung durch eine minimale Serverzeile.

    Die Provisionierung selbst ist bereits durch test_operation_tasks und
    test_servers_router abgedeckt. Hier geht es um die Hoster-Schicht darum
    herum — ein echter Docker-/SteamCMD-Lauf waere im Test weder moeglich noch
    aussagekraeftig.
    """
    from services.server_provisioning_service import ProvisioningResult
    from services.operation_task_service import TASK_SERVER_PROVISION, create_or_reuse_task

    def _provision(session, request, actor, *, idempotency_key=None, retry_of_id=None):
        if not permission_service.has_global_permission(session, actor.user, "servers.create"):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        import hashlib
        import json

        task, created = create_or_reuse_task(
            session,
            actor=actor,
            task_type=TASK_SERVER_PROVISION,
            request_hash=hashlib.sha256(
                json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest(),
            idempotency_key=idempotency_key,
        )
        if not created and task.server_id is not None:
            server = session.query(Server).filter(Server.id == task.server_id).first()
            return ProvisioningResult(
                server=server, task=task, postgres_credentials=[], reused=True
            )
        server = Server(
            name=request.name,
            game_type=request.game_type,
            install_dir=f"/tmp/hoster-test-{request.name}",
            status="stopped",
            ram_limit_mb=request.ram_limit_mb,
            cpu_limit_percent=request.cpu_limit_percent,
            disk_limit_gb=request.disk_limit_gb,
        )
        session.add(server)
        session.flush()
        task.server_id = server.id
        session.commit()
        return ProvisioningResult(server=server, task=task, postgres_credentials=[])

    return _provision


# ── Externe API ────────────────────────────────────────────────────────────


def test_repeated_order_never_creates_a_second_server(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Zielpunkt 15.2: derselbe Auftrag darf keinen zweiten Server erzeugen."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    payload = {
        "desired_state": "active",
        "external_subject": "kunde-4711",
        "product_key": "mc-8gb",
    }

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        first = client.put(
            "/api/hoster/v1/services/svc-1",
            json=payload,
            headers={API_KEY_HEADER: api_key},
        )
        second = client.put(
            "/api/hoster/v1/services/svc-1",
            json=payload,
            headers={API_KEY_HEADER: api_key},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["server_id"] == second.json()["server_id"]
    assert db.query(HosterService).count() == 1
    assert db.query(Server).count() == 1


def test_api_rejects_unknown_disabled_and_missing_key(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Unbekannter, deaktivierter und fehlender Key sind ununterscheidbar."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)

    missing = client.get("/api/hoster/v1/services/svc-1")
    wrong = client.get(
        "/api/hoster/v1/services/svc-1", headers={API_KEY_HEADER: "vollkommen-falsch"}
    )
    integration.enabled = False
    db.commit()
    disabled = client.get("/api/hoster/v1/services/svc-1", headers={API_KEY_HEADER: api_key})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert disabled.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"] == disabled.json()["detail"]


def test_shop_cannot_provision_beyond_its_service_user(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Verliert der Dienstbenutzer sein Recht, scheitert auch der Shop."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    # Recht nachtraeglich entziehen — genau der Fall, den eine Sonderlogik
    # ausserhalb von RBAC uebersehen wuerde.
    set_user_roles(db, service_user, [])
    db.commit()

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        response = client.put(
            "/api/hoster/v1/services/svc-2",
            json={
                "desired_state": "active",
                "external_subject": "kunde-1",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    assert response.status_code == 403
    assert db.query(Server).count() == 0
    failed = db.query(HosterService).one()
    assert failed.status == "failed"


def test_unknown_product_and_missing_product_are_rejected(
    client: TestClient, db: Session, owner_user: User
) -> None:
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)

    unknown = client.put(
        "/api/hoster/v1/services/svc-3",
        json={
            "desired_state": "active",
            "external_subject": "kunde-1",
            "product_key": "gibt-es-nicht",
        },
        headers={API_KEY_HEADER: api_key},
    )
    without = client.put(
        "/api/hoster/v1/services/svc-4",
        json={"desired_state": "active", "external_subject": "kunde-1"},
        headers={API_KEY_HEADER: api_key},
    )

    assert unknown.status_code == 422
    assert without.status_code == 422
    assert db.query(HosterService).count() == 0


@pytest.mark.parametrize(
    "external_id",
    ["", " ", "a" * 200, "hat leerzeichen", "semi;kolon", "neu\nzeile"],
)
def test_external_identifiers_are_validated(
    client: TestClient, db: Session, owner_user: User, external_id: str
) -> None:
    """Leere, ueberlange und zeichenwidrige Kennungen werden abgewiesen."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)

    response = client.put(
        "/api/hoster/v1/services/svc-5",
        json={
            "desired_state": "active",
            "external_subject": external_id,
            "product_key": "mc-8gb",
        },
        headers={API_KEY_HEADER: api_key},
    )

    assert response.status_code == 422
    assert db.query(HosterIdentity).count() == 0


def test_suspend_stops_server_revokes_access_and_keeps_the_account(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Zielpunkt 12.1: sperren heisst Server aus, Account bleibt."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-6",
            json={
                "desired_state": "active",
                "external_subject": "kunde-9",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    service = db.query(HosterService).one()
    customer_id = service.identity.user_id
    assert db.query(ServerPermission).filter(
        ServerPermission.user_id == customer_id
    ).count() > 0

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        response = client.put(
            "/api/hoster/v1/services/svc-6",
            json={"desired_state": "suspended", "external_subject": "kunde-9"},
            headers={API_KEY_HEADER: api_key},
        )

    assert response.status_code == 200
    db.expire_all()
    assert db.query(HosterService).one().status == "suspended"
    assert db.query(ServerPermission).filter(
        ServerPermission.user_id == customer_id
    ).count() == 0
    # Der Panelaccount bleibt bestehen.
    assert db.query(User).filter(User.id == customer_id).one().is_active is True


def test_termination_sets_a_grace_period_and_deletes_nothing_yet(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Zielpunkt 12.2: eine Kuendigung vernichtet nicht sofort alle Daten."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-7",
            json={
                "desired_state": "active",
                "external_subject": "kunde-3",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        response = client.put(
            "/api/hoster/v1/services/svc-7",
            json={"desired_state": "terminated", "external_subject": "kunde-3"},
            headers={API_KEY_HEADER: api_key},
        )

    assert response.status_code == 200
    db.expire_all()
    service = db.query(HosterService).one()
    assert service.status == "terminating"
    assert service.terminate_after is not None
    # SQLite liefert naive Zeitstempel zurueck; fuer den Vergleich normalisieren.
    deadline = service.terminate_after
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    assert deadline > datetime.now(timezone.utc) + timedelta(days=6)
    # Der Server existiert weiterhin.
    assert db.query(Server).count() == 1


def test_purge_runs_only_after_the_grace_period(
    client: TestClient, db: Session, owner_user: User
) -> None:
    from services.hoster_service_lifecycle import purge_terminated_services

    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-8",
            json={
                "desired_state": "active",
                "external_subject": "kunde-4",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        client.put(
            "/api/hoster/v1/services/svc-8",
            json={"desired_state": "terminated", "external_subject": "kunde-4"},
            headers={API_KEY_HEADER: api_key},
        )

    # Vor Fristablauf passiert nichts.
    assert purge_terminated_services(db) == 0
    assert db.query(Server).count() == 1

    service = db.query(HosterService).one()
    service.terminate_after = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    with patch(
        "services.server_deletion_service.delete_server_completely",
        lambda db_, *, server_id, actor: {"message": "geloescht"},
    ):
        assert purge_terminated_services(db) == 1

    db.expire_all()
    assert db.query(HosterService).one().status == "terminated"


# ── Rolle bei Buchung ──────────────────────────────────────────────────────


def test_an_active_contract_grants_the_product_role(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Ein aktiver Vertrag bringt die Produktrolle mit — auch beim zweiten Mal.

    Ueber globale Rollen laufen unter anderem die KI-Kontingente. Wer einen
    groesseren Tarif bucht, muss sein Kontingent sofort haben, und zwar auch
    dann, wenn Panelaccount und Server aus einer frueheren Buchung schon stehen.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        gebucht = client.put(
            "/api/hoster/v1/services/svc-20",
            json={
                "desired_state": "active",
                "external_subject": "kunde-20",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    assert gebucht.status_code == 200, gebucht.text
    assert tarif.id in _customer_roles(db, "svc-20")

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        client.put(
            "/api/hoster/v1/services/svc-20",
            json={"desired_state": "suspended", "external_subject": "kunde-20"},
            headers={API_KEY_HEADER: api_key},
        )
    assert tarif.id not in _customer_roles(db, "svc-20")

    # Zweite Aktivierung: Account und Server bestehen bereits, es wird nichts
    # mehr provisioniert. Genau dieser Zweig darf die Rolle nicht vergessen.
    erneut = client.put(
        "/api/hoster/v1/services/svc-20",
        json={"desired_state": "active", "external_subject": "kunde-20"},
        headers={API_KEY_HEADER: api_key},
    )

    assert erneut.status_code == 200, erneut.text
    assert db.query(Server).count() == 1
    assert tarif.id in _customer_roles(db, "svc-20")


@pytest.mark.parametrize("zielzustand", ["suspended", "terminated"])
def test_suspending_and_terminating_revoke_the_product_role(
    client: TestClient, db: Session, owner_user: User, zielzustand: str
) -> None:
    """Gesperrt oder gekuendigt heisst: das Kontingent ist wieder weg.

    Ein Vertrag, der nicht mehr laeuft, darf kein KI-Budget mehr tragen — ob er
    nur ruht oder ganz endet, macht dabei keinen Unterschied.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-21",
            json={
                "desired_state": "active",
                "external_subject": "kunde-21",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    assert tarif.id in _customer_roles(db, "svc-21")

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        beendet = client.put(
            "/api/hoster/v1/services/svc-21",
            json={"desired_state": zielzustand, "external_subject": "kunde-21"},
            headers={API_KEY_HEADER: api_key},
        )

    assert beendet.status_code == 200, beendet.text
    assert tarif.id not in _customer_roles(db, "svc-21")


def test_a_second_active_contract_keeps_the_role(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Entzogen wird nur, was kein anderer aktiver Vertrag noch fordert.

    Wer zwei Server desselben Tarifs mietet und einen kuendigt, behaelt sein
    Kontingent bis zum letzten Vertrag.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        for external_id in ("svc-22a", "svc-22b"):
            gebucht = client.put(
                f"/api/hoster/v1/services/{external_id}",
                json={
                    "desired_state": "active",
                    "external_subject": "kunde-22",
                    "product_key": "mc-8gb",
                },
                headers={API_KEY_HEADER: api_key},
            )
            assert gebucht.status_code == 200, gebucht.text

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        client.put(
            "/api/hoster/v1/services/svc-22a",
            json={"desired_state": "terminated", "external_subject": "kunde-22"},
            headers={API_KEY_HEADER: api_key},
        )
        assert tarif.id in _customer_roles(db, "svc-22b")

        client.put(
            "/api/hoster/v1/services/svc-22b",
            json={"desired_state": "terminated", "external_subject": "kunde-22"},
            headers={API_KEY_HEADER: api_key},
        )

    assert tarif.id not in _customer_roles(db, "svc-22b")


def test_a_manually_granted_role_survives_grant_and_revoke(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Die Rollenvergabe eines Vertrags ist additiv, nicht ueberschreibend.

    Eine von Hand vergebene Rolle — etwa fuer einen Supporter, der nebenbei
    Kunde ist — darf weder beim Buchen noch beim Kuendigen verschwinden. Sonst
    haette ein Shop-Aufruf die Rechtevergabe des Betreibers ueberschrieben.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    manuell = _role(db, "supporter")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-23",
            json={
                "desired_state": "active",
                "external_subject": "kunde-23",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        client.put(
            "/api/hoster/v1/services/svc-23",
            json={"desired_state": "suspended", "external_subject": "kunde-23"},
            headers={API_KEY_HEADER: api_key},
        )

    # Der Betreiber vergibt die vertragsfremde Rolle von Hand.
    db.expire_all()
    vertrag = db.query(HosterService).one()
    kunde = db.query(User).filter(User.id == vertrag.identity.user_id).one()
    set_user_roles(db, kunde, sorted(set(effective_user_role_ids(db, kunde)) | {manuell.id}))

    erneut = client.put(
        "/api/hoster/v1/services/svc-23",
        json={"desired_state": "active", "external_subject": "kunde-23"},
        headers={API_KEY_HEADER: api_key},
    )
    assert erneut.status_code == 200, erneut.text
    rollen = _customer_roles(db, "svc-23")
    assert manuell.id in rollen and tarif.id in rollen

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        client.put(
            "/api/hoster/v1/services/svc-23",
            json={"desired_state": "terminated", "external_subject": "kunde-23"},
            headers={API_KEY_HEADER: api_key},
        )

    rollen = _customer_roles(db, "svc-23")
    assert manuell.id in rollen
    assert tarif.id not in rollen


def test_a_system_role_as_product_role_is_never_revoked(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Eine Systemrolle bleibt stehen, auch wenn ein Produkt sie mitbringt.

    Waehlt ein Betreiber "user" als Produktrolle, waere der Kunde nach der
    Kuendigung ein Account ohne jede Rolle — und damit unbenutzbar, obwohl der
    Betreiber nur ein Produkt konfiguriert hat.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    systemrolle = db.query(Role).filter(Role.name == SYSTEM_ROLE_USER).one()
    _product(db, integration, role_id=systemrolle.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-24",
            json={
                "desired_state": "active",
                "external_subject": "kunde-24",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        gekuendigt = client.put(
            "/api/hoster/v1/services/svc-24",
            json={"desired_state": "terminated", "external_subject": "kunde-24"},
            headers={API_KEY_HEADER: api_key},
        )

    assert gekuendigt.status_code == 200, gekuendigt.text
    assert _customer_roles(db, "svc-24") == {systemrolle.id}


def test_a_product_role_above_the_service_user_cannot_be_saved(
    db: Session, owner_user: User
) -> None:
    """Ein Shop darf ueber ein Produkt nicht mehr vergeben, als er selbst haelt.

    Ohne diese Schranke waere das Feld ein Weg, sich per Shop-Kauf zum
    Rollenverwalter zu machen. Die Zusage gilt schon beim Speichern: ein
    Produkt, das erst bei der ersten Bestellung auffliegt, ist eine Falle.
    """
    service_user = _service_user(db)
    integration, _ = _integration(db, service_user)
    zu_maechtig = _role(db, "tarif-allmacht", keys=("servers.create", "roles.manage"))

    with pytest.raises(hoster_integration_service.HosterRoleEscalation) as fehler:
        _product(db, integration, role_id=zu_maechtig.id)

    # Benannt wird nur, was tatsaechlich fehlt — sonst waere der Text fuer den
    # Betreiber keine Anleitung, sondern nur eine Ablehnung.
    assert "roles.manage" in str(fehler.value)
    assert "servers.create" not in str(fehler.value)
    assert db.query(HosterProduct).count() == 0


def test_a_shrunken_service_user_makes_the_order_fail_loudly(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Schrumpfen die Rechte nach der Konfiguration, scheitert der Kauf laut.

    Die Rolle still auszulassen waere der schlimmere Ausgang: der Kunde haette
    einen laufenden Vertrag ohne das Kontingent, fuer das er bezahlt, und
    niemand einen Anhaltspunkt, warum. Stattdessen bleibt ein abfragbarer
    Vertrag mit eigenem Fehlercode zurueck — und kein halber Server.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-loeschen", keys=("servers.delete",))
    _product(db, integration, role_id=tarif.id)

    # Das Recht wird dem Dienstbenutzer NACH der Konfiguration entzogen — genau
    # der Fall, den eine Pruefung nur beim Speichern uebersehen wuerde.
    dienstrolle = db.query(Role).filter(Role.name == "hoster-service").one()
    db.query(RolePermission).filter(
        RolePermission.role_id == dienstrolle.id,
        RolePermission.permission_key == "servers.delete",
    ).delete(synchronize_session=False)
    db.commit()

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        response = client.put(
            "/api/hoster/v1/services/svc-25",
            json={
                "desired_state": "active",
                "external_subject": "kunde-25",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    assert response.status_code == 422
    db.expire_all()
    vertrag = db.query(HosterService).one()
    assert vertrag.status == "failed"
    assert vertrag.status_code == "hoster_role_escalation"
    assert db.query(Server).count() == 0


def test_a_rejected_contract_does_not_entitle_to_its_role_later(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Ein abgelehnter Vertrag darf seine Rolle auch spaeter nicht mitbringen.

    Ein neuer Vertrag wird mit `desired_state="active"` festgeschrieben, bevor
    die Aktivierung ueberhaupt versucht wird — der Rollback eines Fehlschlags
    nimmt das nicht zurueck. Wer die Anspruchsmenge nur aus dem *Wunsch* bildet,
    haelt den abgelehnten Vertrag fuer laufend und vergibt genau die Rolle, die
    er eben verweigert hat, beim naechsten harmlosen Kauf desselben Kunden nach.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    zu_maechtig = _role(db, "tarif-loeschen", keys=("servers.delete",))
    _product(db, integration, role_id=zu_maechtig.id, key="mc-gross")
    _product(db, integration, role_id=None, key="mc-klein")

    dienstrolle = db.query(Role).filter(Role.name == "hoster-service").one()
    db.query(RolePermission).filter(
        RolePermission.role_id == dienstrolle.id,
        RolePermission.permission_key == "servers.delete",
    ).delete(synchronize_session=False)
    db.commit()

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        abgelehnt = client.put(
            "/api/hoster/v1/services/svc-26a",
            json={
                "desired_state": "active",
                "external_subject": "kunde-26",
                "product_key": "mc-gross",
            },
            headers={API_KEY_HEADER: api_key},
        )
        assert abgelehnt.status_code == 422

        # Derselbe Kunde kauft danach ein voellig harmloses Produkt ohne Rolle.
        harmlos = client.put(
            "/api/hoster/v1/services/svc-26b",
            json={
                "desired_state": "active",
                "external_subject": "kunde-26",
                "product_key": "mc-klein",
            },
            headers={API_KEY_HEADER: api_key},
        )

    assert harmlos.status_code == 200, harmlos.text
    assert zu_maechtig.id not in _customer_roles(db, "svc-26b")


def test_changing_the_tariff_swaps_the_role(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Ein Tarifwechsel tauscht die Rolle, er stapelt sie nicht.

    Die alte Rolle haengt nach dem Wechsel an keinem Produkt des Kunden mehr.
    Wer nur nachsieht, was heute an den Produkten steht, findet sie nie wieder
    und entzieht sie nie — der Kunde behielte das Kontingent des grossen Tarifs,
    waehrend er den kleinen zahlt.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    gross = _role(db, "tarif-gross")
    klein = _role(db, "tarif-klein")
    _product(db, integration, role_id=gross.id, key="mc-gross")
    _product(db, integration, role_id=klein.id, key="mc-klein")

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-27",
            json={
                "desired_state": "active",
                "external_subject": "kunde-27",
                "product_key": "mc-gross",
            },
            headers={API_KEY_HEADER: api_key},
        )
    assert gross.id in _customer_roles(db, "svc-27")

    gewechselt = client.put(
        "/api/hoster/v1/services/svc-27",
        json={
            "desired_state": "active",
            "external_subject": "kunde-27",
            "product_key": "mc-klein",
        },
        headers={API_KEY_HEADER: api_key},
    )

    assert gewechselt.status_code == 200, gewechselt.text
    rollen = _customer_roles(db, "svc-27")
    assert klein.id in rollen
    assert gross.id not in rollen


def test_the_tariff_change_checks_the_role_of_the_new_product(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Geprueft wird die Rolle, die auch vergeben wird.

    `HosterService.product` ist `lazy="joined"` und die Session laeuft ohne
    Autoflush: wer beim Wechsel nur den Fremdschluessel setzt, prueft die Rolle
    des alten Tarifs und vergibt die des neuen. Die Eskalationsschranke stuende
    dann neben der Vergabe statt davor.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    harmlos = _role(db, "tarif-harmlos")
    maechtig = _role(db, "tarif-loeschen", keys=("servers.delete",))
    _product(db, integration, role_id=harmlos.id, key="mc-harmlos")
    _product(db, integration, role_id=maechtig.id, key="mc-maechtig")

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-28",
            json={
                "desired_state": "active",
                "external_subject": "kunde-28",
                "product_key": "mc-harmlos",
            },
            headers={API_KEY_HEADER: api_key},
        )

    dienstrolle = db.query(Role).filter(Role.name == "hoster-service").one()
    db.query(RolePermission).filter(
        RolePermission.role_id == dienstrolle.id,
        RolePermission.permission_key == "servers.delete",
    ).delete(synchronize_session=False)
    db.commit()

    gewechselt = client.put(
        "/api/hoster/v1/services/svc-28",
        json={
            "desired_state": "active",
            "external_subject": "kunde-28",
            "product_key": "mc-maechtig",
        },
        headers={API_KEY_HEADER: api_key},
    )

    assert gewechselt.status_code == 422
    assert maechtig.id not in _customer_roles(db, "svc-28")
    db.expire_all()
    assert db.query(HosterService).one().status_code == "hoster_role_escalation"


def test_a_role_taken_off_the_product_is_still_revoked(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Zurueckgenommen wird, was vergeben wurde — nicht, was heute im Tarif steht.

    Nimmt der Betreiber die Rolle aus dem Produkt, waehrend ein Vertrag laeuft,
    findet eine Ableitung ueber das Produkt sie nicht mehr. Die Kuendigung
    liesse den Kunden mit einem Kontingent zurueck, das zu keinem Vertrag
    gehoert, und keine spaetere Aktion holte es ein.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-29",
            json={
                "desired_state": "active",
                "external_subject": "kunde-29",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    assert tarif.id in _customer_roles(db, "svc-29")

    # Der Betreiber nimmt die Rolle aus dem Tarif.
    _product(db, integration, role_id=None)

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        gekuendigt = client.put(
            "/api/hoster/v1/services/svc-29",
            json={"desired_state": "terminated", "external_subject": "kunde-29"},
            headers={API_KEY_HEADER: api_key},
        )

    assert gekuendigt.status_code == 200, gekuendigt.text
    assert tarif.id not in _customer_roles(db, "svc-29")


def test_a_deactivated_customer_still_loses_the_role(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Auch ein gesperrtes Konto verliert die Rolle seines gekuendigten Vertrags.

    Sonst haelt der Kunde nach einer Reaktivierung ein Kontingent ohne jeden
    Vertrag — und weil sein letzter Vertrag beendet ist, laeuft nie wieder ein
    Abgleich, der es einsammeln koennte.
    """
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    tarif = _role(db, "tarif-gross")
    _product(db, integration, role_id=tarif.id)

    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-30",
            json={
                "desired_state": "active",
                "external_subject": "kunde-30",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    assert tarif.id in _customer_roles(db, "svc-30")

    db.expire_all()
    vertrag = db.query(HosterService).one()
    kunde = db.query(User).filter(User.id == vertrag.identity.user_id).one()
    kunde.is_active = False
    db.commit()

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        gekuendigt = client.put(
            "/api/hoster/v1/services/svc-30",
            json={"desired_state": "terminated", "external_subject": "kunde-30"},
            headers={API_KEY_HEADER: api_key},
        )

    assert gekuendigt.status_code == 200, gekuendigt.text
    assert tarif.id not in _customer_roles(db, "svc-30")


def test_the_operator_cannot_put_a_role_above_himself_on_a_product(
    client: TestClient, db: Session, owner_user: User, regular_user: User, user_cookies: dict
) -> None:
    """`panel.hoster.write` ist kein Weg an der Rollenverwaltung vorbei.

    Die Pruefung im Dienst richtet sich gegen den *Dienstbenutzer* — und den
    waehlt genau dieser Akteur selbst aus. Ohne eine zweite Schranke gegen den
    Akteur genuegt `panel.hoster.write`, um einen privilegierten Dienstbenutzer
    einzutragen, die `admin`-Rolle an ein Produkt zu haengen, mit dem frisch
    erhaltenen API-Key einen Vertrag zu kaufen und sich per Handoff eine
    Sitzung als der so erzeugte Admin-Kunde ausstellen zu lassen.
    """
    betreiberrolle = _role(db, "hoster-betreiber", keys=("panel.hoster.read", "panel.hoster.write"))
    set_user_roles(db, regular_user, [betreiberrolle.id])
    service_user = _service_user(db)
    integration, _ = _integration(db, service_user)
    adminrolle = db.query(Role).filter(Role.name == "admin").one()
    fremde_macht = _role(db, "tarif-allmacht", keys=("roles.manage",))

    def speichern(role_id: int) -> int:
        return client.put(
            f"/api/hoster/integrations/{integration.id}/products",
            json={
                "external_product_key": "mc-8gb",
                "game_type": "dayz",
                "ram_limit_mb": 8192,
                "cpu_limit_percent": 200,
                "disk_limit_gb": 50,
                "node_id": None,
                "backup_interval_hours": None,
                "role_id": role_id,
                "enabled": True,
            },
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        ).status_code

    # Die Systemrolle `admin` ist ausdruecklich dem Owner vorbehalten.
    assert speichern(adminrolle.id) == 403
    # Und generell jede Rolle, deren Rechte der Akteur nicht selbst haelt.
    assert speichern(fremde_macht.id) == 403
    assert db.query(HosterProduct).count() == 0


# ── Handoff ────────────────────────────────────────────────────────────────


def test_handoff_is_single_use_and_never_stores_the_token(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Zielpunkt 14: kurzlebig, einmalig, kein Klartext-Token in der Datenbank."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-9",
            json={
                "desired_state": "active",
                "external_subject": "kunde-5",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    created = client.post(
        "/api/hoster/v1/handoffs",
        json={"external_service_id": "svc-9", "target_path": "/servers"},
        headers={API_KEY_HEADER: api_key},
    )
    assert created.status_code == 200, created.text
    token = created.json()["url"].rsplit("/", 1)[-1]

    stored = db.query(HosterHandoff).one()
    assert stored.token_hash == hash_token(token)
    assert token not in stored.token_hash
    # Kein Audit-Eintrag darf den Token enthalten.
    for entry in db.query(AuditLog).all():
        assert token not in (entry.details or "")

    first = client.get(f"/api/hoster/handoff/{token}", follow_redirects=False)
    second = client.get(f"/api/hoster/handoff/{token}", follow_redirects=False)

    assert first.status_code == 302
    assert first.headers["location"].endswith("/servers")
    assert "__Secure-access_token" in first.headers.get("set-cookie", "")
    # Zweiter Klick meldet niemanden mehr an.
    assert second.status_code == 302
    assert "handoff=invalid" in second.headers["location"]
    assert "__Secure-access_token" not in second.headers.get("set-cookie", "")


def test_handoff_target_is_restricted_to_internal_paths(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Der Handoff darf kein offener Redirect sein."""
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-10",
            json={
                "desired_state": "active",
                "external_subject": "kunde-6",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    for target in ("https://boese.example/phish", "//boese.example", "/admin", "/servers/../admin"):
        response = client.post(
            "/api/hoster/v1/handoffs",
            json={"external_service_id": "svc-10", "target_path": target},
            headers={API_KEY_HEADER: api_key},
        )
        assert response.status_code == 422, target
    assert db.query(HosterHandoff).count() == 0


def test_expired_handoff_does_not_authenticate(
    client: TestClient, db: Session, owner_user: User
) -> None:
    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-11",
            json={
                "desired_state": "active",
                "external_subject": "kunde-7",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    created = client.post(
        "/api/hoster/v1/handoffs",
        json={"external_service_id": "svc-11"},
        headers={API_KEY_HEADER: api_key},
    )
    token = created.json()["url"].rsplit("/", 1)[-1]
    handoff = db.query(HosterHandoff).one()
    handoff.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    response = client.get(f"/api/hoster/handoff/{token}", follow_redirects=False)

    assert response.status_code == 302
    assert "handoff=invalid" in response.headers["location"]
    assert "__Secure-access_token" not in response.headers.get("set-cookie", "")


def test_handoff_redirect_uses_referer_only_from_the_cors_allowlist(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """Der Klick aus dem Panel (Dev: panel_url zeigt aufs Backend) darf zur
    Frontend-Origin zurueckleiten — aber nur bei exaktem Allowlist-Treffer.
    Jeder fremde Referer faellt auf panel_url zurueck: der Endpunkt setzt
    Session-Cookies, ein offener Redirect waere ein Phishing-Werkzeug."""
    from config import settings

    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-12",
            json={
                "desired_state": "active",
                "external_subject": "kunde-8",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )

    def _neuer_token() -> str:
        created = client.post(
            "/api/hoster/v1/handoffs",
            json={"external_service_id": "svc-12", "target_path": "/servers"},
            headers={API_KEY_HEADER: api_key},
        )
        return created.json()["url"].rsplit("/", 1)[-1]

    panel_base = (settings.panel_url or "").rstrip("/")
    vorher = settings.cors_allowed_origins
    settings.cors_allowed_origins = "https://frontend.example"
    try:
        # Allowlist-Treffer: die Referer-Origin (nie deren Pfad) wird die Basis.
        erlaubt = client.get(
            f"/api/hoster/handoff/{_neuer_token()}",
            headers={"Referer": "https://frontend.example/settings?tab=hoster"},
            follow_redirects=False,
        )
        assert erlaubt.status_code == 302
        assert erlaubt.headers["location"] == "https://frontend.example/servers"

        # Fremder Referer (echter Shop): unveraendert panel_url.
        fremd = client.get(
            f"/api/hoster/handoff/{_neuer_token()}",
            headers={"Referer": "https://boese.example/checkout"},
            follow_redirects=False,
        )
        assert fremd.status_code == 302
        assert fremd.headers["location"] == f"{panel_base}/servers"

        # Auch der Fehlerfall bleibt auf der validierten Basis.
        kaputt = client.get(
            "/api/hoster/handoff/gibt-es-nicht",
            headers={"Referer": "https://frontend.example/settings"},
            follow_redirects=False,
        )
        assert kaputt.status_code == 302
        assert kaputt.headers["location"] == "https://frontend.example/login?handoff=invalid"
    finally:
        settings.cors_allowed_origins = vorher


# ── Loeschen von Kundenservern ─────────────────────────────────────────────


def test_servers_delete_does_not_bypass_the_hoster_gate(
    client: TestClient, db: Session, owner_user: User
) -> None:
    """`servers.delete` ist ein globaler Key und liefe sonst am Filter vorbei.

    Die Zusage lautet "unsichtbar und unbedienbar" — das schliesst das Loeschen
    ein, sonst waere ausgerechnet die extremste Bedienung die eine Luecke (und
    per 404-vs-Erfolg auch ein Existenzorakel). Durch duerfen: Inhaber des
    Hoster-Keys und der Dienstbenutzer der eigenen Integration; die Antwort
    fuer alle anderen ist 404, nicht 403 — wer den Server nicht sehen darf,
    erfaehrt auch hier nicht, dass es ihn gibt.
    """
    from services.server_deletion_service import delete_server_completely
    from services.actor_context import ActorContext

    service_user = _service_user(db)
    integration, api_key = _integration(db, service_user)
    _product(db, integration)
    with patch("services.server_provisioning_service.provision_server", _fake_provision(db)):
        client.put(
            "/api/hoster/v1/services/svc-30",
            json={
                "desired_state": "active",
                "external_subject": "kunde-30",
                "product_key": "mc-8gb",
            },
            headers={API_KEY_HEADER: api_key},
        )
    server = db.query(Server).one()

    def _actor_mit(keys: tuple[str, ...], name: str) -> ActorContext:
        user = AuthService.create_user(db, name, f"{name}@test.de", "ShopPass123!")
        role = _role(db, f"rolle-{name}", keys=keys)
        set_user_roles(db, user, [role.id])
        db.refresh(user)
        return ActorContext.for_user(user)

    # Rolle mit servers.delete, aber ohne Hoster-Key: der Server ist fuer sie
    # nicht existent — 404 vor jedem destruktiven Schritt.
    with pytest.raises(Exception) as excinfo:
        delete_server_completely(
            db, server_id=server.id, actor=_actor_mit(("servers.delete",), "loescher")
        )
    assert getattr(excinfo.value, "status_code", None) == 404
    assert db.query(Server).count() == 1

    # Mit Hoster-Key kommt derselbe Aufruf durchs Gate: die naechste Station
    # ist die PostgreSQL-Bereinigung — ihr provozierter Fehler (503) beweist,
    # dass nicht mehr das Gate (404) geantwortet hat.
    with patch(
        "services.server_deletion_service.postgres_service.drop_server_resources",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(Exception) as excinfo:
            delete_server_completely(
                db,
                server_id=server.id,
                actor=_actor_mit(
                    ("servers.delete", "servers.hoster_customers.view"), "befugter"
                ),
            )
        assert getattr(excinfo.value, "status_code", None) == 503

        # Der Dienstbenutzer der eigenen Integration ebenso — Purge und
        # Sandbox-Reset laufen ueber genau diesen Pfad.
        with pytest.raises(Exception) as excinfo:
            delete_server_completely(
                db,
                server_id=server.id,
                actor=ActorContext.for_user(service_user, origin="external"),
            )
        assert getattr(excinfo.value, "status_code", None) == 503

    # Der Dienstbenutzer eines FREMDEN Shops bleibt draussen.
    fremd_user = AuthService.create_user(db, "fremd-shop", "fremd-shop@test.de", "ShopPass123!")
    fremd_rolle = _role(db, "fremd-shop-rolle", keys=("servers.create", "servers.delete"))
    set_user_roles(db, fremd_user, [fremd_rolle.id])
    db.refresh(fremd_user)
    hoster_integration_service.create_integration(
        db,
        name="Fremdshop",
        slug="fremdshop",
        enabled=True,
        service_user_id=fremd_user.id,
        webhook_url=None,
        terminate_grace_days=7,
    )
    db.commit()
    with pytest.raises(Exception) as excinfo:
        delete_server_completely(
            db, server_id=server.id, actor=ActorContext.for_user(fremd_user)
        )
    assert getattr(excinfo.value, "status_code", None) == 404
    assert db.query(Server).count() == 1


# ── Panel-Verwaltung ───────────────────────────────────────────────────────


def test_integration_secrets_are_shown_once_and_never_read_back(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    service_user = _service_user(db)

    created = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Shop",
            "slug": "shop",
            "enabled": True,
            "service_user_id": service_user.id,
            "webhook_url": "https://shop.example/hooks/msm",
            "terminate_grace_days": 3,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert created.status_code == 201, created.text
    api_key = created.json()["value"]

    listed = client.get("/api/hoster/integrations", cookies=owner_cookies)
    assert listed.status_code == 200
    body = listed.json()[0]
    assert api_key not in listed.text
    assert body["api_key_hint"].startswith("...")
    assert body["webhook_secret_configured"] is False

    row = db.query(HosterIntegration).one()
    assert row.api_key_hash == hash_token(api_key)


def test_service_user_without_create_permission_is_rejected(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Ein Shop darf nicht an einen rechtlosen Benutzer gebunden werden."""
    powerless = _service_user(db, with_create=False)

    response = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Shop",
            "slug": "shop",
            "enabled": True,
            "service_user_id": powerless.id,
            "webhook_url": None,
            "terminate_grace_days": 7,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 422
    assert db.query(HosterIntegration).count() == 0


def test_owner_account_cannot_be_used_as_service_user(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    response = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Shop",
            "slug": "shop",
            "enabled": True,
            "service_user_id": owner_user.id,
            "webhook_url": None,
            "terminate_grace_days": 7,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 422
    assert db.query(HosterIntegration).count() == 0


def test_panel_management_requires_the_hoster_permission(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    listed = client.get("/api/hoster/integrations", cookies=user_cookies)
    created = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Shop",
            "slug": "shop",
            "enabled": True,
            "service_user_id": regular_user.id,
            "webhook_url": None,
            "terminate_grace_days": 7,
        },
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert listed.status_code == 403
    assert created.status_code == 403


def test_webhook_url_must_be_https_without_credentials(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    service_user = _service_user(db)
    for url in ("http://shop.example/hook", "https://a:b@shop.example/hook", "nicht-mal-eine-url"):
        response = client.post(
            "/api/hoster/integrations",
            json={
                "name": "Shop",
                "slug": "shop",
                "enabled": True,
                "service_user_id": service_user.id,
                "webhook_url": url,
                "terminate_grace_days": 7,
            },
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert response.status_code == 422, url
    assert db.query(HosterIntegration).count() == 0
