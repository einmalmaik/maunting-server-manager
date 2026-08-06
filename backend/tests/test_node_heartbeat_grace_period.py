"""Integration & Unit tests for Node Heartbeat Grace Period & Hysteresis.

Ensures that transient I/O delays or temporary probe timeouts (e.g. during heavy
SteamCMD downloads or file extractions) do NOT falsely flip node.status to 'offline',
preventing UI console streaming disruption and unnecessary action locking.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from models import Node, Server
from services.node_client import NodeClient, NodeClientError
from services.node_service import handle_node_probe_failure, probe_node_metrics
from services.scheduler_service import _node_heartbeat_task

VALID_ENC_TOKEN = "test-enc-v1:6d736d3a6e6f64653a617574685f746f6b656e:73796e746865746963"


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def test_handle_node_probe_failure_retains_online_status_within_grace_period(db):
    """A node with a recent heartbeat (<60s) retains 'online' status during a transient probe failure."""
    now = datetime.now(timezone.utc)
    recent_hb = now - timedelta(seconds=15)

    node = Node(
        name="Busy Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=recent_hb,
    )
    db.add(node)
    db.commit()

    # Simulate a probe failure (e.g. timeout during heavy SteamCMD extraction)
    handle_node_probe_failure(db, node, now=now)

    db.refresh(node)
    assert node.status == "online"


def test_handle_node_probe_failure_marks_offline_when_grace_period_exceeded(db):
    """A node with a stale heartbeat (>60s) transitions to 'offline' on probe failure."""
    now = datetime.now(timezone.utc)
    stale_hb = now - timedelta(seconds=120)

    node = Node(
        name="Crashed Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=stale_hb,
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    server = Server(
        name="Test Server",
        game_type="minecraft_paper",
        install_dir="/tmp/test-srv",
        node_id=node.id,
        status="running",
        guardian_observed_state="healthy",
        guardian_container_status="running",
        public_bind_ip="127.0.0.1",
    )
    db.add(server)
    db.commit()

    handle_node_probe_failure(db, node, now=now)
    db.commit()
    db.expire_all()
    db.refresh(node)
    db.refresh(server)
    assert node.status == "offline"
    assert server.guardian_observed_state == "unknown"


def test_handle_node_probe_failure_marks_offline_when_no_prior_heartbeat(db):
    """A node with no prior heartbeat (last_heartbeat is None) immediately transitions to 'offline'."""
    now = datetime.now(timezone.utc)

    node = Node(
        name="New Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=None,
    )
    db.add(node)
    db.commit()

    handle_node_probe_failure(db, node, now=now)
    db.commit()
    db.refresh(node)
    assert node.status == "offline"


@pytest.mark.anyio
async def test_scheduler_heartbeat_retains_online_during_steamcmd_io_burst(db):
    """Integration test: background scheduler heartbeat task retains 'online' status during heavy download/extraction I/O."""
    now = datetime.now(timezone.utc)
    recent_hb = now - timedelta(seconds=10)

    node = Node(
        name="Downloading 7D2D Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=recent_hb,
    )
    db.add(node)
    db.commit()

    # Simulate SteamCMD download causing HTTP probe timeout (NodeClientError)
    with patch.object(NodeClient, "metrics_async", side_effect=NodeClientError("Timeout during 7D2D install")):
        await _node_heartbeat_task()

    db.refresh(node)
    # Status MUST remain 'online' so user UI & console stream are not interrupted!
    assert node.status == "online"


@pytest.mark.anyio
async def test_scheduler_heartbeat_marks_offline_after_persistent_downtime(db):
    """Integration test: background scheduler heartbeat task marks node 'offline' after persistent downtime (>60s)."""
    now = datetime.now(timezone.utc)
    old_hb = now - timedelta(seconds=90)

    node = Node(
        name="Dead Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=old_hb,
    )
    db.add(node)
    db.commit()

    with patch.object(NodeClient, "metrics_async", side_effect=NodeClientError("Connection refused")):
        await _node_heartbeat_task()

    db.refresh(node)
    assert node.status == "offline"


def test_probe_node_metrics_uses_grace_period(db):
    """probe_node_metrics retains status when mark_status=True and probe fails during grace period."""
    now = datetime.now(timezone.utc)
    recent_hb = now - timedelta(seconds=5)

    node = Node(
        name="Probe Grace Node",
        host="https://127.0.0.1:19999",
        auth_token_enc=VALID_ENC_TOKEN,
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
        last_heartbeat=recent_hb,
    )
    db.add(node)
    db.commit()

    with patch.object(NodeClient, "metrics", side_effect=NodeClientError("busy")):
        res = probe_node_metrics(node, mark_status=True)

    assert res is None
    db.refresh(node)
    assert node.status == "online"
