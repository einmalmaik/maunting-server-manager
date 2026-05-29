"""Statische Pruefung: alle in den Routern verwendeten Permission-Keys
muessen im Catalog stehen. Verhindert Tippfehler / Drift.
"""
import re
from pathlib import Path

from services.permission_catalog import ALL_KEYS, GLOBAL_KEYS, SERVER_KEYS

ROUTERS_DIR = Path(__file__).resolve().parent.parent / "routers"


def _scan_permission_calls() -> set[str]:
    """Sammelt alle als Strings hinterlegten Permission-Keys aus den Routern.

    Heuristik: alle String-Literale, die in require_server_permission(...)
    oder require_global(...) als letztes Argument bzw. einziges Argument stehen.
    """
    keys: set[str] = set()
    pat_server = re.compile(r'require_server_permission\([^)]+?,\s*["\']([\w.]+)["\']\s*\)')
    pat_global = re.compile(r'require_global\(\s*["\']([\w.]+)["\']\s*\)')
    pat_has_global = re.compile(r'has_global_permission\([^)]+?,\s*["\']([\w.]+)["\']\s*\)')
    pat_has_server = re.compile(r'has_server_permission\([^)]+?,\s*["\']([\w.]+)["\']\s*\)')
    for path in ROUTERS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in pat_server.finditer(text):
            keys.add(m.group(1))
        for m in pat_global.finditer(text):
            keys.add(m.group(1))
        for m in pat_has_global.finditer(text):
            keys.add(m.group(1))
        for m in pat_has_server.finditer(text):
            keys.add(m.group(1))
    return keys


def test_all_router_keys_are_in_catalog():
    used = _scan_permission_calls()
    # Sollten wirklich Keys gefunden worden sein (sanity).
    assert used, "Heuristik findet keine Permission-Keys in den Routern."
    unknown = used - ALL_KEYS
    assert not unknown, f"Unbekannte Permission-Keys in Routern: {sorted(unknown)}"


def test_global_and_server_keys_disjoint():
    assert GLOBAL_KEYS.isdisjoint(SERVER_KEYS), "Global- und Server-Keys ueberlappen."


def test_known_global_keys_present():
    must_have = {
        "users.read", "users.manage", "users.permissions.manage", "roles.manage",
        "panel.settings.read", "panel.settings.write",
        "servers.create", "servers.delete", "system.view",
    }
    assert must_have <= GLOBAL_KEYS


def test_known_server_keys_present():
    must_have = {
        "server.view", "server.start", "server.stop", "server.restart", "server.install",
        "server.config.write", "server.network.manage", "server.resources.manage",
        "server.console.read", "server.console.write",
        "server.files.read", "server.files.write", "server.files.delete",
        "server.backups.read", "server.backups.create", "server.backups.restore", "server.backups.delete",
        "server.mods.read", "server.mods.write", "server.mods.toggle",
        "server.kill",
    }
    assert must_have <= SERVER_KEYS
