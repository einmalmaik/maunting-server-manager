"""Invarianten für autorisierte, idempotente Provisionierungs-Tasks."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, OperationTask, Server, User
from schemas import ServerCreate
from services.actor_context import ActorContext
from services.operation_task_service import (
    TASK_SERVER_PROVISION,
    create_or_reuse_task,
    mark_failed,
    mark_running,
    recover_interrupted_tasks,
    set_phase,
)


def _create_server(
    client: TestClient,
    cookies: dict,
    csrf_token: str,
    *,
    name: str,
    idempotency_key: str,
):
    with patch("services.server_provisioning_service.os.makedirs"), \
         patch("services.server_provisioning_service.os.chmod"), \
         patch("services.server_provisioning_service.os.path.exists", return_value=False), \
         patch(
             "services.server_provisioning_service.allocate_ports",
             return_value=(27015, 27016, 27017),
         ), \
         patch("services.server_provisioning_service.get_plugin", return_value=None):
        return client.post(
            "/api/servers",
            json={"name": name, "game_type": "dayz"},
            cookies=cookies,
            headers={
                "X-CSRF-Token": csrf_token,
                "Idempotency-Key": idempotency_key,
            },
        )


def test_create_replay_returns_same_server_without_duplicate(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    csrf_token: str,
    db: Session,
) -> None:
    """Gleicher Key plus Payload erzeugt exakt einen Server und einen Task."""
    first = _create_server(
        client,
        owner_cookies,
        csrf_token,
        name="Idempotent Server",
        idempotency_key="provision-request-001",
    )
    second = _create_server(
        client,
        owner_cookies,
        csrf_token,
        name="Idempotent Server",
        idempotency_key="provision-request-001",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["task_id"] == second.json()["task_id"]
    assert db.query(Server).filter(Server.name == "Idempotent Server").count() == 1
    tasks = db.query(OperationTask).filter(
        OperationTask.actor_user_id == owner_user.id,
        OperationTask.task_type == TASK_SERVER_PROVISION,
    ).all()
    assert len(tasks) == 1
    assert tasks[0].status == "succeeded"
    assert tasks[0].idempotency_key_hash != "provision-request-001"


def test_reused_key_with_changed_payload_is_rejected(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
) -> None:
    first = _create_server(
        client,
        owner_cookies,
        csrf_token,
        name="Original",
        idempotency_key="provision-request-002",
    )
    changed = _create_server(
        client,
        owner_cookies,
        csrf_token,
        name="Changed",
        idempotency_key="provision-request-002",
    )

    assert first.status_code == 201
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_key_conflict"


def test_task_status_is_owner_scoped(
    client: TestClient,
    owner_cookies: dict,
    regular_user: User,
    user_cookies: dict,
    csrf_token: str,
) -> None:
    created = _create_server(
        client,
        owner_cookies,
        csrf_token,
        name="Visible Task",
        idempotency_key="provision-request-003",
    )
    task_id = created.json()["task_id"]

    owner_response = client.get(f"/api/tasks/{task_id}", cookies=owner_cookies)
    other_response = client.get(f"/api/tasks/{task_id}", cookies=user_cookies)

    assert owner_response.status_code == 200
    assert owner_response.json()["status"] == "succeeded"
    assert "idempotency_key_hash" not in owner_response.json()
    assert "request_hash" not in owner_response.json()
    assert other_response.status_code == 404


def test_explicit_retry_creates_linked_attempt(
    db: Session,
    owner_user: User,
) -> None:
    actor = ActorContext.for_user(owner_user)
    first, created = create_or_reuse_task(
        db,
        actor=actor,
        task_type=TASK_SERVER_PROVISION,
        request_hash="a" * 64,
        idempotency_key="retry-original",
    )
    assert created is True
    mark_failed(db, first, error_code="port_check_unavailable")

    retry, retry_created = create_or_reuse_task(
        db,
        actor=actor,
        task_type=TASK_SERVER_PROVISION,
        request_hash="a" * 64,
        idempotency_key="retry-attempt-2",
        retry_of_id=first.id,
    )

    assert retry_created is True
    assert retry.retry_of_id == first.id
    assert retry.attempt == 2
    assert retry.id != first.id


def test_failed_provisioning_persists_only_typed_error(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    db: Session,
) -> None:
    with patch("services.server_provisioning_service.os.makedirs"), \
         patch("services.server_provisioning_service.os.chmod"), \
         patch("services.server_provisioning_service.os.path.exists", return_value=False), \
         patch(
             "services.server_provisioning_service.allocate_ports",
             return_value=(27015, 27016, 27017),
         ), \
         patch("services.server_provisioning_service.get_plugin", return_value=None), \
         patch(
             "services.server_provisioning_service.postgres_service.provision_server_databases",
             side_effect=RuntimeError("postgres://admin:secret@internal.invalid/db"),
         ), \
         patch("services.server_provisioning_service.postgres_service.drop_server_resources"):
        response = client.post(
            "/api/servers",
            json={"name": "Broken Task", "game_type": "dayz", "postgres_enabled": True},
            cookies=owner_cookies,
            headers={
                "X-CSRF-Token": csrf_token,
                "Idempotency-Key": "provision-request-004",
            },
        )

    assert response.status_code == 503
    task = db.query(OperationTask).order_by(OperationTask.created_at.desc()).first()
    assert task is not None
    assert task.status == "failed"
    assert task.error_code == "postgres_provision_failed"
    persisted = f"{task.error_code} {task.error_message}"
    assert "secret" not in persisted
    assert "internal.invalid" not in persisted


def test_install_callback_completes_active_provisioning_task(
    db: Session,
    owner_user: User,
    test_server: Server,
) -> None:
    """Der bestehende Install-Thread schließt denselben persistenten Task ab."""
    from games.base import finish_install

    task, _ = create_or_reuse_task(
        db,
        actor=ActorContext.for_user(owner_user),
        task_type=TASK_SERVER_PROVISION,
        request_hash="b" * 64,
        idempotency_key="install-callback-001",
    )
    mark_running(db, task, "installing")
    set_phase(db, task, "installing", server_id=test_server.id)
    test_server.status = "installing"
    db.commit()

    finish_install(test_server.id, {"ok": True, "next_status": "awaiting_files"})

    db.expire_all()
    completed = db.query(OperationTask).filter(OperationTask.id == task.id).one()
    assert completed.status == "succeeded"
    assert completed.phase == "awaiting_files"
    assert completed.completed_at is not None


def test_lifecycle_replay_queues_worker_exactly_once(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    test_server: Server,
    db: Session,
) -> None:
    test_server.public_bind_ip = "127.0.0.1"
    db.commit()
    with patch("services.server_action_service.get_plugin") as plugin_lookup, \
         patch("services.server_lifecycle_service._start_lifecycle_thread") as start_thread:
        plugin_lookup.return_value.get_blueprint.return_value = None
        headers = {
            "X-CSRF-Token": csrf_token,
            "Idempotency-Key": "lifecycle-start-001",
        }
        first = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=owner_cookies,
            headers=headers,
        )
        replay = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=owner_cookies,
            headers=headers,
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["task_id"] == replay.json()["task_id"]
    start_thread.assert_called_once()
    task = db.query(OperationTask).filter(OperationTask.id == first.json()["task_id"]).one()
    assert task.task_type == "server.lifecycle.start"
    assert task.status == "running"


def test_lifecycle_worker_completes_explicit_task(
    db: Session,
    owner_user: User,
    test_server: Server,
) -> None:
    from services.server_lifecycle_service import _run_lifecycle_job

    task, _ = create_or_reuse_task(
        db,
        actor=ActorContext.for_user(owner_user),
        task_type="server.lifecycle.stop",
        request_hash="c" * 64,
        idempotency_key="lifecycle-stop-worker-001",
    )
    mark_running(db, task, "queued")
    set_phase(db, task, "queued", server_id=test_server.id)

    with patch("services.server_lifecycle_service.get_plugin") as plugin_lookup, \
         patch("services.server_lifecycle_service._run_stop") as run_stop:
        plugin_lookup.return_value = object()
        _run_lifecycle_job(test_server.id, "stop", task_id=task.id)

    run_stop.assert_called_once()
    db.expire_all()
    completed = db.query(OperationTask).filter(OperationTask.id == task.id).one()
    assert completed.status == "succeeded"
    assert completed.error_code is None


def test_startup_marks_unconfirmed_installation_interrupted(
    db: Session,
    owner_user: User,
    test_server: Server,
) -> None:
    task, _ = create_or_reuse_task(
        db,
        actor=ActorContext.for_user(owner_user),
        task_type=TASK_SERVER_PROVISION,
        request_hash="d" * 64,
        idempotency_key="interrupted-install-001",
    )
    mark_running(db, task, "installing")
    set_phase(db, task, "installing", server_id=test_server.id)
    test_server.status = "installing"
    db.commit()

    assert recover_interrupted_tasks(db) == 1

    db.expire_all()
    recovered_task = db.query(OperationTask).filter(OperationTask.id == task.id).one()
    recovered_server = db.query(Server).filter(Server.id == test_server.id).one()
    assert recovered_task.status == "failed"
    assert recovered_task.error_code == "server_installation_interrupted"
    assert recovered_server.status == "error"


def test_ai_actor_uses_same_provisioning_service_and_audit(
    db: Session,
    owner_user: User,
) -> None:
    from services.server_provisioning_service import provision_server

    actor = ActorContext.for_user(owner_user, origin="ai")
    with patch("services.server_provisioning_service.os.makedirs"), \
         patch("services.server_provisioning_service.os.chmod"), \
         patch("services.server_provisioning_service.os.path.exists", return_value=False), \
         patch(
             "services.server_provisioning_service.allocate_ports",
             return_value=(27015, 27016, 27017),
         ), \
         patch("services.server_provisioning_service.get_plugin", return_value=None):
        result = provision_server(
            db,
            ServerCreate(name="AI Actor Server", game_type="dayz"),
            actor,
            idempotency_key="ai-provisioning-001",
        )

    assert result.server.name == "AI Actor Server"
    entries = db.query(AuditLog).filter(AuditLog.correlation_id == actor.correlation_id).all()
    assert {entry.action for entry in entries} == {
        "server.provision.requested",
        "server.provision.completed",
    }
    assert {entry.origin for entry in entries} == {"ai"}


def test_shared_provisioning_service_rechecks_rbac_before_side_effects(
    db: Session,
    regular_user: User,
) -> None:
    from services.server_provisioning_service import provision_server

    with patch("services.server_provisioning_service.allocate_ports") as allocate:
        with pytest.raises(HTTPException) as exc:
            provision_server(
                db,
                ServerCreate(name="Forbidden", game_type="dayz"),
                ActorContext.for_user(regular_user, origin="ai"),
            )

    assert exc.value.status_code == 403
    allocate.assert_not_called()
    assert db.query(OperationTask).filter(OperationTask.actor_user_id == regular_user.id).count() == 0
