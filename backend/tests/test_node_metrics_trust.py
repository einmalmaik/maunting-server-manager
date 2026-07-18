from types import SimpleNamespace

from sqlalchemy import BigInteger

from models import Node, Server
from services.node_service import apply_agent_metrics


def _node():
    return SimpleNamespace(
        cpu_total=None,
        ram_total=None,
        disk_total=None,
        cpu_percent=None,
        ram_used=None,
        disk_used=None,
        container_count=7,
        docker_connected=False,
        agent_version="0.9.0",
    )


def test_apply_agent_metrics_persists_only_reported_agent_identity_fields():
    node = _node()

    apply_agent_metrics(node, {"cpu_count": 4})

    assert node.cpu_total == 4.0
    assert node.container_count == 7
    assert node.docker_connected is False
    assert node.agent_version == "0.9.0"


def test_apply_agent_metrics_accepts_valid_reported_identity_fields():
    node = _node()

    apply_agent_metrics(
        node,
        {
            "container_count": 3,
            "docker_connected": True,
            "agent_version": "1.2.3",
        },
    )

    assert node.container_count == 3
    assert node.docker_connected is True
    assert node.agent_version == "1.2.3"


def test_apply_agent_metrics_normalizes_resource_bytes_to_stored_megabytes():
    node = _node()

    apply_agent_metrics(
        node,
        {
            "ram_total_bytes": 8 * 1024 * 1024,
            "ram_used_bytes": 3 * 1024 * 1024,
            "disk_total_bytes": 20 * 1024 * 1024,
            "disk_used_bytes": 7 * 1024 * 1024,
        },
    )

    assert (node.ram_total, node.ram_used) == (8, 3)
    assert (node.disk_total, node.disk_used) == (20, 7)


def test_apply_agent_metrics_rejects_invalid_reported_identity_fields():
    node = _node()

    apply_agent_metrics(
        node,
        {
            "container_count": -1,
            "docker_connected": "yes",
            "agent_version": "   ",
        },
    )

    assert node.container_count == 7
    assert node.docker_connected is False
    assert node.agent_version == "0.9.0"


def test_node_resource_columns_and_server_node_index_match_migration_contract():
    for column_name in ("ram_total", "disk_total", "ram_used", "disk_used"):
        assert isinstance(Node.__table__.c[column_name].type, BigInteger)
    assert any(
        tuple(column.name for column in index.columns) == ("node_id",)
        for index in Server.__table__.indexes
    )
