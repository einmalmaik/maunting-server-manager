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
from services.role_service import set_user_roles


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


def _product(db: Session, integration: HosterIntegration) -> None:
    hoster_integration_service.upsert_product(
        db,
        integration=integration,
        external_product_key="mc-8gb",
        game_type="dayz",
        ram_limit_mb=8192,
        cpu_limit_percent=200,
        disk_limit_gb=50,
        node_id=None,
        backup_interval_hours=None,
        enabled=True,
    )
    db.commit()


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
