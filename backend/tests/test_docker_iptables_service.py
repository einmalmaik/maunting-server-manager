"""Tests fuer docker_iptables_service.

Wir mocken iptables komplett — die Tests pruefen Argumente, Reihenfolge und
Idempotenz, nicht das tatsaechliche Netzwerkverhalten.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services import docker_iptables_service as dis


def _ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# ── ensure_baseline_drop ─────────────────────────────────────────────────


class TestBaselineDrop:
    def test_skipped_when_iptables_missing(self):
        with patch("services.docker_iptables_service._run_iptables", return_value=None):
            assert dis.ensure_baseline_drop() is False

    def test_skipped_when_docker_user_chain_missing(self):
        # version-check OK, chain check non-zero
        responses = iter([_ok(), _ok(returncode=1)])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ):
            assert dis.ensure_baseline_drop() is False

    def test_adds_both_protocols_when_missing(self):
        # version-check OK, chain exists, both checks miss, both inserts OK.
        responses = iter([
            _ok(),                       # _iptables_available (-version)
            _ok(),                       # _chain_exists (-L)
            _ok(returncode=1),           # _rule_exists udp baseline
            _ok(),                       # -A udp
            _ok(returncode=1),           # _rule_exists tcp baseline
            _ok(),                       # -A tcp
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.ensure_baseline_drop() is True

        # Pruefe: ein Insert fuer udp + ein Insert fuer tcp wurden aufgerufen.
        appended = [c.args for c in run.call_args_list if c.args and c.args[0] == "-A"]
        assert any("udp" in args for args in appended)
        assert any("tcp" in args for args in appended)

    def test_skips_when_rule_already_present(self):
        responses = iter([
            _ok(),               # -version
            _ok(),               # -L
            _ok(returncode=0),   # _rule_exists udp → bereits da
            _ok(returncode=0),   # _rule_exists tcp → bereits da
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.ensure_baseline_drop() is True

        # Kein -A-Call darf passieren
        appended = [c.args for c in run.call_args_list if c.args and c.args[0] == "-A"]
        assert appended == []


# ── accept_server / revoke_server ────────────────────────────────────────


class TestAcceptServer:
    def test_skipped_without_bind_ip(self):
        with patch("services.docker_iptables_service._iptables_available", return_value=True), \
             patch("services.docker_iptables_service._chain_exists", return_value=True):
            assert dis.accept_server("srv", "", 27015, 27016, 27017) is False

    def test_inserts_three_rules_at_top(self):
        # 1) version-check 2) chain-check 3..) per port: _rule_exists (miss) + insert
        responses = iter([
            _ok(),                   # iptables -version
            _ok(),                   # iptables -L DOCKER-USER
            _ok(returncode=1),       # exists? game-udp
            _ok(),                   # -I game
            _ok(returncode=1),       # exists? query-udp
            _ok(),                   # -I query
            _ok(returncode=1),       # exists? rcon-tcp
            _ok(),                   # -I rcon
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.accept_server("srv", "1.2.3.4", 27015, 27016, 27017) is True

        inserts = [c.args for c in run.call_args_list if c.args and c.args[0] == "-I"]
        assert len(inserts) == 3
        # Erstes Argument nach -I muss "DOCKER-USER" "1" sein (Top-Insert)
        for args in inserts:
            assert args[1] == "DOCKER-USER"
            assert args[2] == "1"
            assert "ACCEPT" in args
            assert "-d" in args
            assert "1.2.3.4" in args

    def test_idempotent_when_rule_present(self):
        responses = iter([
            _ok(), _ok(),
            _ok(returncode=0),  # game-udp existiert schon
            _ok(returncode=0),  # query-udp existiert schon
            _ok(returncode=0),  # rcon-tcp existiert schon
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.accept_server("srv", "1.2.3.4", 27015, 27016, 27017) is True
        inserts = [c.args for c in run.call_args_list if c.args and c.args[0] == "-I"]
        assert inserts == []


class TestRevokeServer:
    def test_deletes_three_rules(self):
        responses = iter([
            _ok(), _ok(),
            _ok(returncode=0),  # exists game → yes
            _ok(),              # -D game
            _ok(returncode=0),  # exists query
            _ok(),              # -D query
            _ok(returncode=0),  # exists rcon
            _ok(),              # -D rcon
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.revoke_server("srv", "1.2.3.4", 27015, 27016, 27017) is True
        deletes = [c.args for c in run.call_args_list if c.args and c.args[0] == "-D"]
        assert len(deletes) == 3

    def test_idempotent_when_rules_absent(self):
        responses = iter([
            _ok(), _ok(),
            _ok(returncode=1),  # game nicht da
            _ok(returncode=1),  # query nicht da
            _ok(returncode=1),  # rcon nicht da
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.revoke_server("srv", "1.2.3.4", 27015, 27016, 27017) is True
        deletes = [c.args for c in run.call_args_list if c.args and c.args[0] == "-D"]
        assert deletes == []

    def test_handles_partial_ports(self):
        # Nur game_port gesetzt — keine query/rcon-Calls
        responses = iter([
            _ok(), _ok(),
            _ok(returncode=1), _ok(),
        ])
        with patch(
            "services.docker_iptables_service._run_iptables",
            side_effect=lambda *a, **kw: next(responses),
        ) as run:
            assert dis.accept_server("srv", "1.2.3.4", 27015, None, None) is True
        inserts = [c.args for c in run.call_args_list if c.args and c.args[0] == "-I"]
        assert len(inserts) == 1
