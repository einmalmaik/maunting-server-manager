"""Resource live-update must use Docker Engine API fields (NanoCpus).

docker-py's Container.update() rejects nano_cpus (TypeError). The agent must
post /containers/{id}/update with NanoCpus/Memory/MemorySwap — same contract
as the panel local path — so multi-node resource PATCH works.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services import docker_service


def test_update_container_raw_posts_nanocpus_and_memory_to_engine_api() -> None:
    """Real containers must not call SDK update() with nano_cpus kwargs."""
    api = MagicMock()
    api._url.return_value = "/containers/abc/update"
    api._post_json.return_value = MagicMock()
    api._result.return_value = {"Warnings": None}

    container = SimpleNamespace(
        id="abc123",
        client=SimpleNamespace(api=api),
        update=MagicMock(side_effect=TypeError("unexpected keyword argument 'nano_cpus'")),
    )

    result = docker_service._update_container_raw(
        container,
        nano_cpus=4_000_000_000,
        mem_limit="16000m",
        memswap_limit="16000m",
    )

    assert result == {"Warnings": None}
    container.update.assert_not_called()
    api._url.assert_called_once()
    posted = api._post_json.call_args.kwargs.get("data") or api._post_json.call_args[1].get("data")
    if posted is None:
        # positional: _post_json(url, data=data) or (url, data)
        args, kwargs = api._post_json.call_args
        posted = kwargs.get("data") if kwargs else None
        if posted is None and len(args) >= 2:
            posted = args[1]
    assert posted["NanoCpus"] == 4_000_000_000
    assert posted["Memory"] == 16000 * 1024 * 1024
    assert posted["MemorySwap"] == 16000 * 1024 * 1024


def test_update_container_raw_mock_falls_back_to_container_update() -> None:
    """Unit tests with MagicMock keep using container.update (panel parity)."""
    container = MagicMock()
    container.update.return_value = {"Warnings": []}

    result = docker_service._update_container_raw(container, nano_cpus=1_000_000_000)

    assert result == {"Warnings": []}
    container.update.assert_called_once_with(nano_cpus=1_000_000_000)


@pytest.mark.parametrize(
    "cpu_percent,expected_nano",
    [
        (50, 500_000_000),
        (100, 1_000_000_000),
        (400, 4_000_000_000),
    ],
)
def test_update_container_resources_maps_cpu_via_raw_path(
    cpu_percent: int, expected_nano: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = MagicMock()
    container.attrs = {
        "HostConfig": {"NanoCpus": 1_000_000_000, "Memory": 0, "MemorySwap": 0},
    }
    container.reload = MagicMock()
    monkeypatch.setattr(docker_service, "_get_container", lambda _name: container)

    captured: dict = {}

    def fake_raw(ctr, **kwargs):
        captured.update(kwargs)
        return {"Warnings": None}

    monkeypatch.setattr(docker_service, "_update_container_raw", fake_raw)

    result = docker_service.update_container_resources(
        "msm-srv-89", {"cpu_limit_percent": cpu_percent}
    )

    assert result == {"ok": True}
    assert captured["nano_cpus"] == expected_nano
    container.update.assert_not_called()


def test_update_container_resources_maps_ram_and_cpu_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = MagicMock()
    container.attrs = {
        "HostConfig": {
            "NanoCpus": 1_000_000_000,
            "Memory": 8_589_934_592,
            "MemorySwap": 8_589_934_592,
        },
    }
    container.reload = MagicMock()
    monkeypatch.setattr(docker_service, "_get_container", lambda _name: container)

    captured: dict = {}

    def fake_raw(ctr, **kwargs):
        captured.update(kwargs)
        return {"Warnings": None}

    monkeypatch.setattr(docker_service, "_update_container_raw", fake_raw)

    result = docker_service.update_container_resources(
        "msm-srv-89",
        {"cpu_limit_percent": 200, "ram_limit_mb": 16000},
    )

    assert result == {"ok": True}
    assert captured["nano_cpus"] == 2_000_000_000
    assert captured["mem_limit"] == "16000m"
    assert captured["memswap_limit"] == "16000m"


def test_update_container_resources_sdk_typeerror_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: bare TypeError must not become HTTP 500 Internal error."""
    container = MagicMock()
    container.attrs = {"HostConfig": {"NanoCpus": 0, "Memory": 0, "MemorySwap": 0}}
    container.reload = MagicMock()
    monkeypatch.setattr(docker_service, "_get_container", lambda _name: container)

    def boom(*_a, **_k):
        raise TypeError("unexpected keyword argument 'nano_cpus'")

    monkeypatch.setattr(docker_service, "_update_container_raw", boom)

    with pytest.raises(docker_service.DockerUnavailableError):
        docker_service.update_container_resources(
            "msm-srv-89", {"cpu_limit_percent": 400}
        )


def test_api_patch_resources_uses_service(
    client, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        docker_service,
        "update_container_resources",
        lambda name, updates: {"ok": True, "name": name, "updates": updates},
    )
    r = client.patch(
        "/containers/msm-srv-89/resources",
        headers=auth_headers,
        json={"cpu_limit_percent": 400, "ram_limit_mb": 16000},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
