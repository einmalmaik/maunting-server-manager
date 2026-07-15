"""Docker hardening gates on create."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path

import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient

from services.docker_service import HardeningError, assert_msm_container_name, create_container


def test_container_name_prefix_required() -> None:
    with pytest.raises(Exception):
        assert_msm_container_name("evil-container")
    assert assert_msm_container_name("msm-srv-1") == "msm-srv-1"


def test_privileged_rejected() -> None:
    with pytest.raises(HardeningError):
        create_container(
            name="msm-srv-99",
            image="alpine:latest",
            privileged=True,
        )


def test_host_network_rejected() -> None:
    with pytest.raises(HardeningError):
        create_container(
            name="msm-srv-99",
            image="alpine:latest",
            network_mode="host",
        )


def test_cap_add_rejected() -> None:
    with pytest.raises(HardeningError):
        create_container(
            name="msm-srv-99",
            image="alpine:latest",
            cap_add=["SYS_ADMIN"],
        )


def test_api_rejects_privileged(client: TestClient, auth_headers: dict, monkeypatch) -> None:
    # Ensure we don't hit real docker
    r = client.post(
        "/containers",
        headers=auth_headers,
        json={
            "name": "msm-srv-1",
            "image": "alpine:latest",
            "privileged": True,
        },
    )
    assert r.status_code == 403
    assert "privileged" in r.json()["detail"].lower()


def test_api_rejects_bad_name(client: TestClient, auth_headers: dict) -> None:
    r = client.post(
        "/containers",
        headers=auth_headers,
        json={"name": "not-msm", "image": "alpine:latest"},
    )
    assert r.status_code == 400


def test_create_preserves_runtime_hardening_and_attaches_extra_network(monkeypatch) -> None:
    existing_lookup = MagicMock(side_effect=NotFound("missing"))
    container = MagicMock(id="abcdef1234567890", attrs={"State": {"Status": "running"}})
    run = MagicMock(return_value=container)
    network = MagicMock()
    docker_client = SimpleNamespace(
        containers=SimpleNamespace(get=existing_lookup, run=run),
        networks=SimpleNamespace(get=MagicMock(return_value=network)),
    )
    monkeypatch.setattr("services.docker_service._get_client", lambda: docker_client)

    result = create_container(
        name="msm-srv-42",
        image="example.invalid/runtime:test",
        read_only_rootfs=True,
        tmpfs_paths=["/tmp"],
        network="primary",
        extra_networks=["msm-managed-postgres"],
        restart_policy_name="on-failure",
    )

    assert result["ok"] is True
    kwargs = run.call_args.kwargs
    assert kwargs["read_only"] is True
    assert kwargs["tmpfs"] == {"/tmp": "rw,size=64m,mode=1777"}
    assert kwargs["restart_policy"] == {"Name": "on-failure"}
    network.connect.assert_called_once_with(container)


def test_network_attach_failure_removes_started_container(monkeypatch) -> None:
    container = MagicMock(id="abcdef1234567890")
    docker_client = SimpleNamespace(
        containers=SimpleNamespace(
            get=MagicMock(side_effect=NotFound("missing")),
            run=MagicMock(return_value=container),
        ),
        networks=SimpleNamespace(get=MagicMock(side_effect=OSError("synthetic failure"))),
    )
    monkeypatch.setattr("services.docker_service._get_client", lambda: docker_client)

    with pytest.raises(Exception, match="network attachment"):
        create_container(
            name="msm-srv-42",
            image="example.invalid/runtime:test",
            extra_networks=["msm-managed-postgres"],
        )
    container.remove.assert_called_once_with(force=True)


def test_ephemeral_container_is_hardened_and_cleaned_up(
    monkeypatch, servers_dir: Path
) -> None:
    target = servers_dir / "42"
    target.mkdir()
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"done\n"
    docker_client = SimpleNamespace(
        containers=SimpleNamespace(run=MagicMock(return_value=container))
    )
    monkeypatch.setattr("services.docker_service._get_client", lambda: docker_client)

    from services.docker_service import run_ephemeral

    result = run_ephemeral(
        image="example.invalid/tool:test",
        command=["true"],
        volumes={str(target): {"bind": "/data", "mode": "rw"}},
        cap_add=["CHOWN"],
    )

    assert result["ok"] is True
    kwargs = docker_client.containers.run.call_args.kwargs
    assert kwargs["privileged"] is False
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["cap_add"] == ["CHOWN"]
    container.remove.assert_called_once_with(force=True)


def test_bind_mount_outside_managed_root_is_rejected(monkeypatch, servers_dir: Path) -> None:
    monkeypatch.setattr(
        "services.docker_service._get_client",
        lambda: SimpleNamespace(containers=SimpleNamespace(run=MagicMock())),
    )
    from services.docker_service import run_ephemeral

    with pytest.raises(HardeningError, match="outside"):
        run_ephemeral(
            image="example.invalid/tool:test",
            command=["true"],
            volumes={str(servers_dir.parent): {"bind": "/host", "mode": "rw"}},
        )
