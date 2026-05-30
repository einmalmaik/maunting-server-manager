"""Tests fuer port_allocation_service.

Wir mocken den Host-Check (``is_port_available``), damit die Tests reproduzier-
bar und unabhaengig von echten Listenern laufen. Die DB-Logik laeuft real
gegen das In-Memory-SQLite aus der conftest.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from models import Server
from services.port_allocation_service import (
    BLOCK_SIZE,
    PORT_RANGE_END,
    PORT_RANGE_START,
    PortConflictError,
    allocate_ports,
)


# ── Auto-Vergabe ─────────────────────────────────────────────────────────


class TestAutoAllocation:
    def test_first_call_returns_first_block(self, db: Session):
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            game, query, rcon = allocate_ports(db)
        assert game == PORT_RANGE_START
        assert query == PORT_RANGE_START + 1
        assert rcon == PORT_RANGE_START + 2

    def test_skips_blocks_with_db_collisions(self, db: Session):
        # Vorbelegung: erster Block ist in der DB belegt
        srv = Server(
            name="Existing",
            game_type="dayz",
            install_dir="/tmp/x",
            status="stopped",
            game_port=PORT_RANGE_START,
            query_port=PORT_RANGE_START + 1,
            rcon_port=PORT_RANGE_START + 2,
        )
        db.add(srv)
        db.commit()

        with patch("services.port_allocation_service.is_port_available", return_value=True):
            game, _query, _rcon = allocate_ports(db)
        assert game == PORT_RANGE_START + BLOCK_SIZE

    def test_skips_blocks_with_host_collisions(self, db: Session):
        # Host belegt den ersten Block, alles andere ist frei
        busy = {PORT_RANGE_START, PORT_RANGE_START + 1, PORT_RANGE_START + 2}

        def host_check(port, protocol, bind_ip):  # noqa: ARG001
            return port not in busy

        with patch("services.port_allocation_service.is_port_available", side_effect=host_check):
            game, _query, _rcon = allocate_ports(db)
        assert game == PORT_RANGE_START + BLOCK_SIZE

    def test_uses_correct_protocols_during_host_check(self, db: Session):
        seen: list[tuple[int, str]] = []

        def host_check(port, protocol, bind_ip):  # noqa: ARG001
            seen.append((port, protocol))
            return True

        with patch("services.port_allocation_service.is_port_available", side_effect=host_check):
            allocate_ports(db)

        # game_port (UDP), query_port (UDP), rcon_port (TCP) – exakt drei Calls
        assert seen[0] == (PORT_RANGE_START, "udp")
        assert seen[1] == (PORT_RANGE_START + 1, "udp")
        assert seen[2] == (PORT_RANGE_START + 2, "tcp")

    def test_raises_when_range_exhausted(self, db: Session):
        # Alle Ports gelten als belegt
        with patch("services.port_allocation_service.is_port_available", return_value=False):
            with pytest.raises(RuntimeError):
                allocate_ports(db)


# ── Manuelle Vergabe ─────────────────────────────────────────────────────


class TestManualAllocation:
    def test_accepts_unique_unblocked_ports(self, db: Session):
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            game, query, rcon = allocate_ports(
                db,
                requested_game_port=27050,
                requested_query_port=27051,
                requested_rcon_port=27052,
            )
        assert (game, query, rcon) == (27050, 27051, 27052)

    def test_defaults_query_and_rcon_to_offsets(self, db: Session):
        """Wenn nur game_port gesetzt ist, leitet die Logik query/rcon ab."""
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            game, query, rcon = allocate_ports(db, requested_game_port=27050)
        assert (game, query, rcon) == (27050, 27051, 27052)

    def test_rejects_when_db_already_uses_port(self, db: Session):
        existing = Server(
            name="Other",
            game_type="dayz",
            install_dir="/tmp/x",
            status="stopped",
            game_port=27050, query_port=27051, rcon_port=27052,
        )
        db.add(existing)
        db.commit()

        with patch("services.port_allocation_service.is_port_available", return_value=True):
            with pytest.raises(PortConflictError):
                allocate_ports(db, requested_game_port=27050)

    def test_rejects_when_host_busy_on_manual_port(self, db: Session):
        with patch("services.port_allocation_service.is_port_available", return_value=False):
            with pytest.raises(PortConflictError):
                allocate_ports(db, requested_game_port=27050)

    def test_rejects_out_of_range_port(self, db: Session):
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            with pytest.raises(ValueError):
                allocate_ports(db, requested_game_port=22)  # SSH-Port
            with pytest.raises(ValueError):
                allocate_ports(db, requested_game_port=70000)

    def test_exclude_self_during_update(self, db: Session):
        srv = Server(
            name="Self",
            game_type="dayz",
            install_dir="/tmp/x",
            status="stopped",
            game_port=27050, query_port=27051, rcon_port=27052,
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)

        # Erneute Vergabe derselben Ports darf erfolgreich sein
        # (Server wird via exclude_server_id ausgenommen).
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            game, _query, _rcon = allocate_ports(
                db,
                requested_game_port=27050,
                requested_query_port=27051,
                requested_rcon_port=27052,
                exclude_server_id=srv.id,
            )
        assert game == 27050


class TestDynamicAllocation:
    def test_dynamic_allocation_success(self, db: Session):
        requirements = [
            ("game", "udp"),
            ("query", "udp"),
            ("rcon", "tcp"),
            ("custom_1", "udp"),
            ("custom_2", "tcp"),
        ]
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            allocated = allocate_ports(
                db,
                port_requirements=requirements,
            )
        assert len(allocated) == 5
        assert allocated[0] == ("game", PORT_RANGE_START, "udp")
        assert allocated[1] == ("query", PORT_RANGE_START + 1, "udp")
        assert allocated[2] == ("rcon", PORT_RANGE_START + 2, "tcp")
        assert allocated[3] == ("custom_1", PORT_RANGE_START + 3, "udp")
        assert allocated[4] == ("custom_2", PORT_RANGE_START + 4, "tcp")

    def test_dynamic_allocation_with_overrides(self, db: Session):
        requirements = [
            ("game", "udp"),
            ("custom_1", "udp"),
        ]
        requested = {
            "game": 28000,
        }
        with patch("services.port_allocation_service.is_port_available", return_value=True):
            allocated = allocate_ports(
                db,
                port_requirements=requirements,
                requested_ports=requested,
            )
        assert len(allocated) == 2
        assert allocated[0] == ("game", 28000, "udp")
        assert allocated[1] == ("custom_1", PORT_RANGE_START, "udp")
