"""Pytest fixtures for MSM Agent."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Configure env BEFORE importing app/settings
os.environ["MSM_AGENT_TOKEN"] = "test-agent-token-not-a-real-secret"
os.environ["MSM_SERVERS_DIR"] = ""  # set per-test via fixture
os.environ["MSM_AGENT_LOG_LEVEL"] = "WARNING"


@pytest.fixture()
def servers_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "servers"
    root.mkdir()
    monkeypatch.setenv("MSM_SERVERS_DIR", str(root))
    # Reload settings bound values
    from config import settings

    monkeypatch.setattr(settings, "servers_dir", str(root))
    monkeypatch.setattr(settings, "guardian_state_dir", str(tmp_path / "guardian"))
    monkeypatch.setattr(settings, "agent_token", "test-agent-token-not-a-real-secret")
    return root


@pytest.fixture()
def client(servers_dir: Path) -> TestClient:
    from main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-agent-token-not-a-real-secret"}
