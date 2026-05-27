"""Tests fuer firewall_service.

UFW selbst wird gemockt — wir verifizieren nur, mit welchen Argumenten der
subprocess-Aufruf erfolgt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services import firewall_service as fw


def _stub_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# ── _comment_for ─────────────────────────────────────────────────────────


class TestCommentFor:
    def test_includes_prefix_name_and_role(self):
        assert fw._comment_for("dayz1", "game") == "MSM dayz1 game"

    def test_sanitizes_name(self):
        # Leerzeichen und Sonderzeichen werden zu Unterstrichen
        assert fw._comment_for("My Server #1!", "rcon").startswith("MSM My_Server_1_ ")

    def test_truncates_long_names(self):
        long_name = "x" * 200
        comment = fw._comment_for(long_name, "game")
        # Der Name-Anteil wird auf 24 Zeichen begrenzt
        assert comment.count("x") <= 24


# ── open_ports / close_ports ─────────────────────────────────────────────


class TestOpenPorts:
    def test_skipped_without_ufw(self):
        with patch("services.firewall_service._ufw_available", return_value=False):
            assert fw.open_ports("srv", 27015, 27016, 27017) is False

    def test_calls_ufw_allow_with_correct_protocols(self):
        with patch("services.firewall_service._ufw_available", return_value=True), \
             patch("services.firewall_service.subprocess.run", return_value=_stub_run()) as run:
            assert fw.open_ports("srv", 27015, 27016, 27017) is True

        calls = [tuple(c.args[0]) for c in run.call_args_list]
        # Wir erwarten drei Allow-Calls (game/udp, query/udp, rcon/tcp).
        assert any("27015/udp" in c for c in calls)
        assert any("27016/udp" in c for c in calls)
        assert any("27017/tcp" in c for c in calls)
        # Alle Calls enthalten MSM-Kommentar.
        for c in calls:
            assert "MSM" in " ".join(c)

    def test_skips_missing_ports(self):
        with patch("services.firewall_service._ufw_available", return_value=True), \
             patch("services.firewall_service.subprocess.run", return_value=_stub_run()) as run:
            fw.open_ports("srv", 27015, None, None)
        # Nur ein Allow-Call (Game-Port).
        assert sum(1 for c in run.call_args_list if "allow" in c.args[0]) == 1


class TestClosePorts:
    def test_skipped_without_ufw(self):
        with patch("services.firewall_service._ufw_available", return_value=False):
            assert fw.close_ports(27015, 27016, 27017) is False

    def test_deletes_all_ports(self):
        with patch("services.firewall_service._ufw_available", return_value=True), \
             patch("services.firewall_service.subprocess.run", return_value=_stub_run()) as run:
            assert fw.close_ports(27015, 27016, 27017) is True

        calls = [tuple(c.args[0]) for c in run.call_args_list]
        assert any("delete" in c and "27015/udp" in c for c in calls)
        assert any("delete" in c and "27016/udp" in c for c in calls)
        assert any("delete" in c and "27017/tcp" in c for c in calls)

    def test_idempotent_when_rule_missing(self):
        # UFW gibt non-zero zurueck, wenn die Regel nicht existiert. Wir
        # propagieren das nicht — der Caller bekommt trotzdem True.
        with patch("services.firewall_service._ufw_available", return_value=True), \
             patch("services.firewall_service.subprocess.run", return_value=_stub_run(returncode=1)):
            assert fw.close_ports(27015) is True


# ── cleanup_legacy_msm_ranges ────────────────────────────────────────────


_STATUS_OUTPUT = """\
Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere                   (# SSH)
[ 2] 80/tcp                     ALLOW IN    Anywhere                   (# HTTP)
[ 3] 27015:27999/udp            ALLOW IN    Anywhere                   (# MSM Game-Server UDP)
[ 4] 27015:27999/tcp            ALLOW IN    Anywhere                   (# MSM Game-Server TCP (RCon))
[ 5] 27015/udp                  ALLOW IN    Anywhere                   (# MSM dayz1 game)
"""


class TestCleanupLegacyRanges:
    def test_removes_only_msm_ranges(self):
        statuses = iter(
            [
                _stub_run(stdout="ufw 0.36"),               # _ufw_available()
                _stub_run(stdout=_STATUS_OUTPUT),           # status numbered
                _stub_run(returncode=0),                    # delete first range
                _stub_run(returncode=0),                    # delete second range
            ]
        )
        with patch("services.firewall_service.subprocess.run", side_effect=lambda *a, **kw: next(statuses)) as run:
            removed = fw.cleanup_legacy_msm_ranges()
        assert removed == 2

        # Pruefe: nur die beiden Ranges wurden geloescht. Einzelner Port und
        # SSH/HTTP bleiben unberuehrt.
        # Akzeptiere sowohl direkte ufw-Aufrufe als auch sudo -n ufw (neuer Wrapper)
        def is_delete_call(args):
            if args[:2] == ["ufw", "delete"]:
                return True
            if args[:3] == ["sudo", "-n", "ufw"] and args[3:5] == ["delete", "allow"]:
                return True
            return False

        delete_calls = [c.args[0] for c in run.call_args_list if is_delete_call(c.args[0])]
        assert any(c[0] == "ufw" and c[1:] == ["delete", "allow", "27015:27999/udp"] or
                   c[0] == "sudo" and c[3:] == ["delete", "allow", "27015:27999/udp"]
                   for c in delete_calls)
        assert any(c[0] == "ufw" and c[1:] == ["delete", "allow", "27015:27999/tcp"] or
                   c[0] == "sudo" and c[3:] == ["delete", "allow", "27015:27999/tcp"]
                   for c in delete_calls)
        # Kein Delete fuer 22/tcp, 80/tcp oder 27015/udp
        deleted_rules = {c[-1] for c in delete_calls}
        assert "22/tcp" not in deleted_rules
        assert "80/tcp" not in deleted_rules
        assert "27015/udp" not in deleted_rules

    def test_returns_zero_when_no_match(self):
        clean_status = """\
Status: active

[ 1] 22/tcp                     ALLOW IN    Anywhere                   (# SSH)
[ 2] 27015/udp                  ALLOW IN    Anywhere                   (# MSM dayz1 game)
"""
        statuses = iter([_stub_run(stdout="ufw 0.36"), _stub_run(stdout=clean_status)])
        with patch("services.firewall_service.subprocess.run", side_effect=lambda *a, **kw: next(statuses)):
            assert fw.cleanup_legacy_msm_ranges() == 0

    def test_returns_zero_when_ufw_missing(self):
        with patch("services.firewall_service._ufw_available", return_value=False):
            assert fw.cleanup_legacy_msm_ranges() == 0
