"""Tests fuer port_check_service.

Wir testen die Bauteile (`_port_in_use_via_ss`, `_can_bind`) plus den
Kombinator (`is_port_available`). Der `ss`-Aufruf wird gemockt, der
Bind-Versuch laeuft real gegen einen Localhost-Socket.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from services import port_check_service as pcs


# ── _normalize_protocol ──────────────────────────────────────────────────


class TestNormalizeProtocol:
    def test_lowercases_and_strips(self):
        assert pcs._normalize_protocol("  TCP ") == "tcp"
        assert pcs._normalize_protocol("UDP") == "udp"

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            pcs._normalize_protocol("sctp")


# ── _port_in_use_via_ss ──────────────────────────────────────────────────


class TestSsCheck:
    def test_returns_true_when_ss_lists_listener(self):
        completed = MagicMock(returncode=0, stdout="LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n")
        with patch("services.port_check_service.subprocess.run", return_value=completed) as run:
            assert pcs._port_in_use_via_ss(22, "tcp") is True
            run.assert_called_once()
            args = run.call_args.args[0]
            assert args[0] == "ss"
            assert "-Hltn" in args  # TCP-Flag
            assert "sport" in args
            assert ":22" in args[-1]

    def test_uses_udp_flag_for_udp(self):
        completed = MagicMock(returncode=0, stdout="")
        with patch("services.port_check_service.subprocess.run", return_value=completed) as run:
            pcs._port_in_use_via_ss(27015, "udp")
            args = run.call_args.args[0]
            assert "-Hlun" in args

    def test_empty_output_means_free(self):
        completed = MagicMock(returncode=0, stdout="\n")
        with patch("services.port_check_service.subprocess.run", return_value=completed):
            assert pcs._port_in_use_via_ss(27015, "tcp") is False

    def test_returns_false_when_ss_missing(self):
        with patch("services.port_check_service.subprocess.run", side_effect=FileNotFoundError):
            assert pcs._port_in_use_via_ss(27015, "tcp") is False

    def test_returns_false_when_ss_times_out(self):
        import subprocess as sp
        with patch(
            "services.port_check_service.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="ss", timeout=5),
        ):
            assert pcs._port_in_use_via_ss(27015, "tcp") is False

    def test_returns_false_on_nonzero_exit(self):
        completed = MagicMock(returncode=2, stdout="")
        with patch("services.port_check_service.subprocess.run", return_value=completed):
            assert pcs._port_in_use_via_ss(27015, "tcp") is False


# ── _can_bind ────────────────────────────────────────────────────────────


def _grab_tcp_port() -> int:
    """Reserviere einen freien Port und gib ihn zurueck (ohne ihn zu halten)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestCanBind:
    def test_free_port_can_bind_tcp(self):
        port = _grab_tcp_port()
        assert pcs._can_bind(port, "tcp", "127.0.0.1") is True

    def test_free_port_can_bind_udp(self):
        port = _grab_tcp_port()  # genuegt, UDP-Bind ist unabhaengig
        assert pcs._can_bind(port, "udp", "127.0.0.1") is True

    def test_occupied_tcp_port_cannot_bind(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            assert pcs._can_bind(port, "tcp", "127.0.0.1") is False
        finally:
            s.close()

    def test_occupied_udp_port_cannot_bind(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        try:
            assert pcs._can_bind(port, "udp", "127.0.0.1") is False
        finally:
            s.close()


# ── is_port_available ────────────────────────────────────────────────────


class TestIsPortAvailable:
    def test_rejects_out_of_range_port(self):
        with pytest.raises(ValueError):
            pcs.is_port_available(0, "tcp")
        with pytest.raises(ValueError):
            pcs.is_port_available(70000, "udp")

    def test_rejects_invalid_protocol(self):
        with pytest.raises(ValueError):
            pcs.is_port_available(27015, "sctp")

    def test_free_when_ss_clean_and_bind_succeeds(self):
        port = _grab_tcp_port()
        with patch("services.port_check_service._port_in_use_via_ss", return_value=False):
            assert pcs.is_port_available(port, "tcp", "127.0.0.1") is True

    def test_busy_when_ss_reports_listener(self):
        with patch("services.port_check_service._port_in_use_via_ss", return_value=True):
            assert pcs.is_port_available(27015, "tcp", "127.0.0.1") is False

    def test_busy_when_bind_fails(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            with patch("services.port_check_service._port_in_use_via_ss", return_value=False):
                assert pcs.is_port_available(port, "tcp", "127.0.0.1") is False
        finally:
            s.close()
