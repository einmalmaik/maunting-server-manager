"""Tests fuer den Hoster-Sandbox-Modus und den Panel-Simulator."""

from __future__ import annotations

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    HosterHandoff,
    HosterIdentity,
    HosterIntegration,
    HosterProduct,
    HosterService,
    Role,
    RolePermission,
    Server,
    User,
)
from services import hoster_integration_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _service_user(db: Session) -> User:
    user = AuthService.create_user(db, "sandbox-bot", "sandbox-bot@test.de", "ShopPass123!")
    role = Role(name="sandbox-service-role", is_system=False)
    db.add(role)
    db.flush()
    for perm in ("servers.create", "servers.delete", "server.start", "server.stop", "server.restart"):
        db.add(RolePermission(role_id=role.id, permission_key=perm))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.refresh(user)
    return user


def _sandbox_integration(db: Session, service_user: User) -> tuple[HosterIntegration, str]:
    integration, api_key = hoster_integration_service.create_integration(
        db,
        name="Sandbox Shop",
        slug="sandbox-shop",
        enabled=True,
        is_sandbox=True,
        service_user_id=service_user.id,
        webhook_url="https://shop.example/webhooks",
        terminate_grace_days=7,
    )
    db.commit()
    db.refresh(integration)
    return integration, api_key


def _product(db: Session, integration: HosterIntegration) -> HosterProduct:
    product = HosterProduct(
        integration_id=integration.id,
        external_product_key="dayz-test",
        game_type="dayz",
        ram_limit_mb=2048,
        cpu_limit_percent=100,
        disk_limit_gb=10,
        enabled=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def test_create_and_update_sandbox_integration(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    service_user = _service_user(db)
    res = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Mein Testshop",
            "slug": "mein-testshop",
            "enabled": True,
            "is_sandbox": True,
            "service_user_id": service_user.id,
            "webhook_url": "https://test.example/hook",
            "terminate_grace_days": 3,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert res.status_code == 201
    assert "value" in res.json()

    list_res = client.get("/api/hoster/integrations", cookies=owner_cookies)
    assert list_res.status_code == 200
    created = [row for row in list_res.json() if row["slug"] == "mein-testshop"][0]
    assert created["is_sandbox"] is True
    assert created["terminate_grace_days"] == 3

    # Update is_sandbox to False
    patch_res = client.patch(
        f"/api/hoster/integrations/{created['id']}",
        json={"is_sandbox": False},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["is_sandbox"] is False


def _fake_provision(db: Session):
    from services.server_provisioning_service import ProvisioningResult
    from services.operation_task_service import TASK_SERVER_PROVISION, create_or_reuse_task
    import hashlib
    import json
    from models import Server

    def _provision(session, request, actor, *, idempotency_key=None, retry_of_id=None):
        task, created = create_or_reuse_task(
            session,
            actor=actor,
            task_type=TASK_SERVER_PROVISION,
            request_hash=hashlib.sha256(
                json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest(),
            idempotency_key=idempotency_key,
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


def test_simulator_full_flow(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    service_user = _service_user(db)
    integration, _ = _sandbox_integration(db, service_user)
    _product(db, integration)

    with patch("services.server_provisioning_service.provision_server", side_effect=_fake_provision(db)), patch(
        "services.server_action_service.request_lifecycle_operation",
        lambda *a, **k: {"task_id": None},
    ):
        # 1. Simulator: Kauf (Order)
        order_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "order", "product_key": "dayz-test"},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert order_res.status_code == 200
        data = order_res.json()
        assert data["ok"] is True
        assert data["action"] == "order"
        assert data["service"] is not None
        assert data["service"]["desired_state"] == "active"
        assert data["service"]["status"] == "ready"
        assert data["handoff_url"] is not None
        service_id = data["service"]["external_service_id"]

        # 2. Simulator: Zahlungssperre (Suspend)
        suspend_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "suspend", "external_service_id": service_id},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert suspend_res.status_code == 200, f"Suspend failed: {suspend_res.json()}"
        assert suspend_res.json()["service"]["desired_state"] == "suspended"

        # 3. Simulator: Reaktivierung (Reactivate)
        reactivate_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "reactivate", "external_service_id": service_id},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert reactivate_res.status_code == 200
        assert reactivate_res.json()["service"]["desired_state"] == "active"

        # 4. Simulator: Kuendigung (Terminate)
        terminate_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "terminate", "external_service_id": service_id},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert terminate_res.status_code == 200
        assert terminate_res.json()["service"]["desired_state"] == "terminated"

        # 5. Simulator: Webhook Test
        with patch("services.hoster_webhook_service.enqueue_custom_event") as mock_wh:
            wh_res = client.post(
                f"/api/hoster/integrations/{integration.id}/simulate",
                json={"action": "test_webhook"},
                cookies=owner_cookies,
                headers=_csrf(owner_cookies),
            )
            assert wh_res.status_code == 200
            assert wh_res.json()["webhook_status"] == "queued"
            assert mock_wh.called

        # 6. Simulator: Testdaten aufraeumen (Clean Sandbox Data)
        # Der Loeschpfad selbst (Container, Ports, Verzeichnisse) laeuft in
        # der Runtime; hier zaehlt, dass er mit dem Dienstbenutzer der
        # Integration aufgerufen wird und die Server-Zeile verschwindet.
        deletion_calls: list[tuple[int, str]] = []

        def _fake_delete(db_, *, server_id, actor):
            deletion_calls.append((actor.user.id, actor.origin))
            db_.query(Server).filter(Server.id == server_id).delete(synchronize_session=False)
            return {"message": "geloescht"}

        with patch(
            "services.server_deletion_service.delete_server_completely",
            _fake_delete,
        ):
            clean_res = client.delete(
                f"/api/hoster/integrations/{integration.id}/sandbox-data",
                cookies=owner_cookies,
                headers=_csrf(owner_cookies),
            )
        assert clean_res.status_code == 200
        assert clean_res.json()["deleted_services_count"] >= 1
        assert deletion_calls == [(service_user.id, "external")]

        # Check DB is clean for this integration — auch die Server-Zeilen:
        # frueher schluckte ein except den kaputten Loeschaufruf und liess
        # verwaiste Server zurueck, waehrend der Test nur HosterService sah.
        remaining = db.query(HosterService).filter(HosterService.integration_id == integration.id).count()
        assert remaining == 0
        assert db.query(Server).count() == 0


def test_sandbox_reset_without_delete_permission_keeps_data(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    """Ohne `servers.delete` am Dienstbenutzer bricht der Reset mit 403 ab.

    Wichtig ist der zweite Teil: die Vertragsdaten bleiben stehen. Ein halber
    Reset (Server noch da, HosterService weg) waere schlimmer als gar keiner,
    weil niemand mehr wuesste, zu welchem Vertrag der verwaiste Server gehoert.
    """
    user = AuthService.create_user(db, "sandbox-lame", "sandbox-lame@test.de", "ShopPass123!")
    role = Role(name="sandbox-ohne-delete", is_system=False)
    db.add(role)
    db.flush()
    for perm in ("servers.create", "server.start", "server.stop", "server.restart"):
        db.add(RolePermission(role_id=role.id, permission_key=perm))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.refresh(user)

    integration, _ = hoster_integration_service.create_integration(
        db,
        name="Sandbox ohne Delete",
        slug="sandbox-ohne-delete",
        enabled=True,
        is_sandbox=True,
        service_user_id=user.id,
        webhook_url=None,
        terminate_grace_days=7,
    )
    db.commit()
    db.refresh(integration)
    _product(db, integration)

    with patch("services.server_provisioning_service.provision_server", side_effect=_fake_provision(db)):
        order_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "order", "product_key": "dayz-test"},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert order_res.status_code == 200

    clean_res = client.delete(
        f"/api/hoster/integrations/{integration.id}/sandbox-data",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert clean_res.status_code == 403

    remaining = db.query(HosterService).filter(HosterService.integration_id == integration.id).count()
    assert remaining == 1
    assert db.query(Server).count() == 1


def test_sandbox_reset_with_disabled_service_user_answers_422(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    """Ein deaktivierter Dienstbenutzer ist eine Fehlkonfiguration, kein 500.

    `_actor` wirft dann HosterConfigurationError — die Zusage ihres Docstrings
    ("wird am Rand zu einem 422") muss auch fuer den Reset gelten, sonst ist
    die Sandbox bis zur Reaktivierung ueber die API nicht aufraeumbar und der
    Betreiber liest einen Serverfehler statt der Ursache.
    """
    service_user = _service_user(db)
    integration, _ = _sandbox_integration(db, service_user)
    _product(db, integration)

    with patch("services.server_provisioning_service.provision_server", side_effect=_fake_provision(db)):
        order_res = client.post(
            f"/api/hoster/integrations/{integration.id}/simulate",
            json={"action": "order", "product_key": "dayz-test"},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert order_res.status_code == 200

    service_user.is_active = False
    db.commit()

    clean_res = client.delete(
        f"/api/hoster/integrations/{integration.id}/sandbox-data",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert clean_res.status_code == 422
    assert "Dienstbenutzer" in clean_res.json()["detail"]
    # Nichts wurde halb geloescht — der Reset bleibt wiederholbar.
    remaining = db.query(HosterService).filter(HosterService.integration_id == integration.id).count()
    assert remaining == 1


def test_simulator_rejected_on_live_integration(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    service_user = _service_user(db)
    live_int, _ = hoster_integration_service.create_integration(
        db,
        name="Live Shop",
        slug="live-shop",
        enabled=True,
        is_sandbox=False,
        service_user_id=service_user.id,
        webhook_url=None,
        terminate_grace_days=7,
    )
    db.commit()

    res = client.post(
        f"/api/hoster/integrations/{live_int.id}/simulate",
        json={"action": "order"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert res.status_code == 400
    assert "Sandbox" in res.json()["detail"]
