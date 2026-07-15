"""Docker hardening gates on create."""

from __future__ import annotations

import pytest
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
