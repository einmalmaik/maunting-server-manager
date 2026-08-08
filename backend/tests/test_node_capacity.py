"""Node RAM booking: SUM(limits), allocatable, and create/update guard."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from models import Node, Server
from services import node_capacity
from services.node_service import apply_agent_metrics, node_out_dict


def test_apply_agent_metrics_persists_cpu_model():
    node = SimpleNamespace(
        cpu_total=None,
        cpu_model=None,
        ram_total=None,
        disk_total=None,
        cpu_percent=None,
        ram_used=None,
        disk_used=None,
        container_count=None,
        docker_connected=None,
        agent_version=None,
    )
    apply_agent_metrics(
        node,
        {
            "cpu_count": 8,
            "cpu_model": "  AMD EPYC 7763 64-Core Processor  ",
        },
    )
    assert node.cpu_total == 8.0
    assert node.cpu_model == "AMD EPYC 7763 64-Core Processor"


def test_apply_agent_metrics_ignores_blank_cpu_model():
    node = SimpleNamespace(cpu_model="keep-me", cpu_total=None)
    apply_agent_metrics(node, {"cpu_model": "   "})
    assert node.cpu_model == "keep-me"


def test_node_out_dict_includes_cpu_model_and_allocated():
    node = SimpleNamespace(
        id=1,
        name="edge",
        host="https://example:9000",
        is_local=False,
        status="online",
        tls_fingerprint=None,
        cpu_total=16.0,
        cpu_model="Test CPU",
        ram_total=32768,
        disk_total=100000,
        last_heartbeat=None,
        cpu_percent=40.0,
        ram_used=8192,
        disk_used=1000,
        agent_version="1.0",
        docker_connected=True,
        container_count=2,
        servers=[],
    )
    out = node_out_dict(node, server_count=2, ram_allocated_mb=16384)
    assert out["cpu_model"] == "Test CPU"
    assert out["ram_allocated_mb"] == 16384
    # default headroom 1024 → 32768 - 1024 - 16384 = 15360
    assert out["ram_allocatable_mb"] == 15360
    assert "auth_token" not in out
    assert out["metrics"]["cpu_model"] == "Test CPU"


def test_sum_allocated_excludes_null_limits_and_other_nodes(db):
    node_a = Node(
        name="a",
        host="https://a:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        ram_total=32768,
    )
    node_b = Node(
        name="b",
        host="https://b:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        ram_total=16384,
    )
    db.add_all([node_a, node_b])
    db.commit()
    db.refresh(node_a)
    db.refresh(node_b)

    db.add_all(
        [
            Server(
                name="s1",
                game_type="t",
                install_dir="/tmp/s1",
                node_id=node_a.id,
                ram_limit_mb=8192,
            ),
            Server(
                name="s2",
                game_type="t",
                install_dir="/tmp/s2",
                node_id=node_a.id,
                ram_limit_mb=4096,
            ),
            Server(
                name="s3",
                game_type="t",
                install_dir="/tmp/s3",
                node_id=node_a.id,
                ram_limit_mb=None,
            ),
            Server(
                name="s4",
                game_type="t",
                install_dir="/tmp/s4",
                node_id=node_b.id,
                ram_limit_mb=16000,
            ),
        ]
    )
    db.commit()

    assert node_capacity.sum_allocated_ram_mb(db, node_a.id) == 8192 + 4096
    assert node_capacity.sum_allocated_ram_mb(db, node_b.id) == 16000

    servers = db.query(Server).filter(Server.node_id == node_a.id).all()
    exclude_id = next(s.id for s in servers if s.ram_limit_mb == 8192)
    assert node_capacity.sum_allocated_ram_mb(
        db, node_a.id, exclude_server_id=exclude_id
    ) == 4096


def test_ensure_ram_limit_fits_allows_overbook(db, monkeypatch):
    monkeypatch.setattr(node_capacity, "ram_headroom_mb", lambda: 1024)
    node = Node(
        name="n",
        host="https://n:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        ram_total=8192,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    db.add(
        Server(
            name="existing",
            game_type="t",
            install_dir="/tmp/e",
            node_id=node.id,
            ram_limit_mb=6144,
        )
    )
    db.commit()

    # Overcommit is allowed (no exception raised)
    node_capacity.ensure_ram_limit_fits(
        db, node, new_ram_limit_mb=2048, exclude_server_id=None
    )
    node_capacity.ensure_ram_limit_fits(
        db, node, new_ram_limit_mb=1024, exclude_server_id=None
    )


def test_ensure_ram_limit_skips_when_total_unknown(db):
    node = Node(
        name="n",
        host="https://n:9000",
        auth_token_enc="enc",
        is_local=False,
        status="unknown",
        ram_total=None,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    # Must not raise
    node_capacity.ensure_ram_limit_fits(
        db, node, new_ram_limit_mb=999_999, exclude_server_id=None
    )


def test_ensure_ram_limit_skips_unlimited(db):
    node = Node(
        name="n",
        host="https://n:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        ram_total=4096,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    node_capacity.ensure_ram_limit_fits(
        db, node, new_ram_limit_mb=None, exclude_server_id=None
    )


def test_capacity_summary_endpoint_auth_and_shape(client, owner_cookies, db):
    node = Node(
        name="busy",
        host="https://busy:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        cpu_total=32,
        cpu_model="EPYC Test",
        ram_total=32768,
        ram_used=20000,
    )
    quiet = Node(
        name="quiet",
        host="https://quiet:9000",
        auth_token_enc="enc",
        is_local=False,
        status="offline",
        cpu_total=8,
        ram_total=8192,
        ram_used=1000,
    )
    db.add_all([node, quiet])
    db.commit()
    db.refresh(node)
    db.add(
        Server(
            name="s",
            game_type="t",
            install_dir="/tmp/s",
            node_id=node.id,
            ram_limit_mb=16000,
        )
    )
    db.commit()

    r = client.get("/api/nodes/capacity-summary?limit=5", cookies=owner_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 2
    assert body["online"] >= 1
    assert len(body["items"]) >= 1
    first = body["items"][0]
    assert first["name"] == "busy"
    assert first["cpu_model"] == "EPYC Test"
    assert first["ram_allocated_mb"] == 16000
    assert "host" not in first
    assert "auth_token" not in first

    # Without auth
    denied = client.get("/api/nodes/capacity-summary")
    assert denied.status_code in (401, 403)


def test_disk_capacity_accounting(db):
    node = Node(
        name="disk_node",
        host="https://disk:9000",
        auth_token_enc="enc",
        is_local=False,
        status="online",
        disk_total=204800,  # 200 GB in MB
        disk_used=51200,    # 50 GB in MB
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    db.add_all([
        Server(
            name="d1",
            game_type="t",
            install_dir="/tmp/d1",
            node_id=node.id,
            disk_limit_gb=20,
            disk_usage_mb=10240,
        ),
        Server(
            name="d2",
            game_type="t",
            install_dir="/tmp/d2",
            node_id=node.id,
            disk_limit_gb=30,
            disk_usage_mb=5120,
        ),
    ])
    db.commit()

    assert node_capacity.sum_allocated_disk_gb(db, node.id) == 50
    assert node_capacity.sum_panel_disk_used_mb(db, node.id) == 15360
    # 200 GB total - 50 GB allocated = 150 GB allocatable
    assert node_capacity.allocatable_disk_gb(node, 50) == 150

