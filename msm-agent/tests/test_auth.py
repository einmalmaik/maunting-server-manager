"""Auth middleware invariants."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_no_auth(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "docker_connected" in body


def test_containers_without_token_401(client: TestClient) -> None:
    r = client.get("/containers")
    assert r.status_code == 401


def test_containers_wrong_token_401(client: TestClient) -> None:
    r = client.get("/containers", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_containers_with_token(client: TestClient, auth_headers: dict, monkeypatch) -> None:
    from services import docker_service

    monkeypatch.setattr(docker_service, "list_containers", lambda: [])
    r = client.get("/containers", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_metrics_requires_auth(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 401
