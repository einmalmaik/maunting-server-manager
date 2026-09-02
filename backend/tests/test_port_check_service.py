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
            # Kein `sport = :N` mehr: ein Aufruf listet alle Listener, statt je
            # Kandidat einen eigenen Prozess zu starten.
            assert "sport" not in args

    def test_a_free_port_is_free_even_when_others_listen(self):
        """Der Schnappschuss darf nicht jeden Port als belegt melden.

        Die Umstellung von "frage nach Port N" auf "liste alles" verschiebt die
        Entscheidung vom Vorhandensein irgendeiner Ausgabe zum Vergleich der
        Portnummer. Faellt das Parsen aus, waere plausibel *jeder* Port belegt —
        und keine Servererstellung fuende je einen Port.
        """
        completed = MagicMock(
            returncode=0,
            stdout=(
                "LISTEN 0 4096   0.0.0.0:22    0.0.0.0:*\n"
                "LISTEN 0 511          *:80          *:*\n"
                "LISTEN 0 128    [::1]:631       [::]:*\n"
            ),
        )
        with patch("services.port_check_service.subprocess.run", return_value=completed):
            assert pcs._port_in_use_via_ss(22, "tcp") is True
            assert pcs._port_in_use_via_ss(80, "tcp") is True
            # IPv6: der Adressteil enthaelt selbst Doppelpunkte.
            assert pcs._port_in_use_via_ss(631, "tcp") is True
            assert pcs._port_in_use_via_ss(27015, "tcp") is False

    def test_many_checks_cost_a_single_process(self):
        """Der eigentliche Grund der Aenderung, als Test.

        Vorher startete jeder Kandidat einen eigenen ``ss``-Prozess. Bei einer
        Servererstellung waren das gemessene 1181 Prozesse — auf einem
        Windows-Entwicklungsrechner ueber hundert Sekunden, und damit allein
        ein Fuenftel der Laufzeit der gesamten Testsuite.
        """
        completed = MagicMock(returncode=0, stdout="LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n")
        with patch("services.port_check_service.subprocess.run", return_value=completed) as run:
            for port in range(20000, 20500):
                pcs._port_in_use_via_ss(port, "tcp")
            assert run.call_count == 1, f"{run.call_count} ss-Aufrufe fuer 500 Ports"

    def test_a_missing_ss_is_not_retried_for_every_port(self):
        """Auch der Fehlschlag wird gemerkt.

        Ohne das startet ein System ohne ``ss`` weiterhin je Kandidat einen
        Prozess, der sofort scheitert — der teuerste Fall ueberhaupt, weil der
        Prozessstart selbst die Kosten sind, nicht die Antwort.
        """
        with patch(
            "services.port_check_service.subprocess.run", side_effect=FileNotFoundError
        ) as run:
            for port in range(20000, 20500):
                assert pcs._port_in_use_via_ss(port, "tcp") is False
            assert run.call_count == 1

    def test_tcp_and_udp_keep_separate_snapshots(self):
        """Ein UDP-Listener auf 27015 sagt nichts ueber TCP 27015."""
        def antwort(args, **_kwargs):
            if "-Hlun" in args:
                return MagicMock(returncode=0, stdout="UNCONN 0 0 0.0.0.0:27015 0.0.0.0:*\n")
            return MagicMock(returncode=0, stdout="")

        with patch("services.port_check_service.subprocess.run", side_effect=antwort):
            assert pcs._port_in_use_via_ss(27015, "udp") is True
            assert pcs._port_in_use_via_ss(27015, "tcp") is False

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


def _grab_udp_port() -> int:
    """Reserviere einen freien UDP-Port und gib ihn zurueck (ohne ihn zu halten)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestCanBind:
    def test_free_port_can_bind_tcp(self):
        port = _grab_tcp_port()
        assert pcs._can_bind(port, "tcp", "127.0.0.1") is True

    def test_free_port_can_bind_udp(self):
        port = _grab_udp_port()
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
