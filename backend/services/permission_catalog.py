"""Permission-Katalog: Single Source of Truth.

Alle bekannten Permission-Keys sind hier als Konstanten gelistet, gruppiert
nach globalem vs. server-scoped Geltungsbereich. Die `admin`-Built-in-Rolle
bekommt **alle** Keys automatisch (Self-Heal beim Startup), `user` ist leer.

KISS: keine Decorator-Registry, keine Magic, nur flache Konstanten + Listen.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    """Eine Permission-Definition (Key + UI-Metadaten)."""

    key: str
    group: str       # "users", "panel", "servers", "server", "system" — UI-Gruppierung
    label: str       # kurzer DE-Text fuer das Settings-UI


# ── Globale Permissions ───────────────────────────────────────────────
# Werden in `role_permissions` einer Rolle zugeordnet.
# `servers.delete` ist BEWUSST global (destruktiv, nur Admin/Owner).

GLOBAL_PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef("users.read",                "users",   "Userliste sehen"),
    PermissionDef("users.manage",              "users",   "User anlegen, bearbeiten, loeschen"),
    PermissionDef("users.permissions.manage",  "users",   "User-Rollen und Server-Permissions verwalten"),
    PermissionDef("roles.manage",              "users",   "Rollen anlegen, bearbeiten, loeschen"),
    PermissionDef("panel.settings.read",       "panel",   "Panel-Einstellungen lesen"),
    PermissionDef("panel.settings.write",      "panel",   "Panel-Einstellungen aendern (Steam, E-Mail, ...)"),
    PermissionDef("panel.database.read",       "panel",   "Panel-Datenbank lesen"),
    PermissionDef("panel.database.admin",      "panel",   "Panel-Datenbank verwalten"),
    PermissionDef("servers.create",            "servers", "Neuen Server anlegen"),
    PermissionDef("servers.delete",            "servers", "Server loeschen (global, nicht delegierbar)"),
    PermissionDef("system.view",               "system",  "System-Ressourcen, Interfaces, Version"),
    # OAuth-Provider-Konfiguration (Phase 4 — Social Login).
    # `secret_update` ist bewusst separat: erfordert zusaetzliche Audit-Bestaetigung.
    # `test` ist read-only, damit ein Operator ohne write-Rechte die Konfiguration pruefen kann.
    PermissionDef("panel.oauth.read",          "panel",   "OAuth-Provider-Konfiguration lesen"),
    PermissionDef("panel.oauth.create",        "panel",   "OAuth-Provider anlegen"),
    PermissionDef("panel.oauth.update",        "panel",   "OAuth-Provider bearbeiten (Slug, Client-ID, Endpoints)"),
    PermissionDef("panel.oauth.delete",        "panel",   "OAuth-Provider loeschen"),
    PermissionDef("panel.oauth.secret_update", "panel",   "OAuth-Client-Secret aendern (rotieren)"),
    PermissionDef("panel.oauth.test",          "panel",   "OAuth-Provider-Verbindung testen"),
)


# ── Server-scoped Permissions ─────────────────────────────────────────
# Koennen pauschal in `role_permissions` einer Rolle stecken (gilt fuer alle
# Server) ODER per-Server in `server_permissions` delegiert sein.

SERVER_PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef("server.view",             "server", "Server in Liste und Detail sehen"),
    PermissionDef("server.start",            "server", "Server starten"),
    PermissionDef("server.stop",             "server", "Server stoppen"),
    PermissionDef("server.restart",          "server", "Server neustarten"),
    PermissionDef("server.kill",             "server", "Server erzwungen beenden (kill)"),
    PermissionDef("server.install",          "server", "Server (re)installieren"),
    PermissionDef("server.config.write",     "server", "Server-Einstellungen aendern (Name, Auto-Restart, Backup-Schedule)"),
    PermissionDef("server.update",           "server", "Server updaten (Reinstall/Update, Outbound-Webhooks)"),
    PermissionDef("server.network.manage",   "server", "Ports und Bind-IP aendern"),
    PermissionDef("server.resources.manage", "server", "CPU-/RAM-/Disk-Limits aendern"),
    PermissionDef("server.console.read",     "server", "Konsole und Logs lesen"),
    PermissionDef("server.console.write",    "server", "Befehle an die Konsole senden"),
    PermissionDef("server.console.exec",     "server", "Befehle im Container ausfuehren (Exec-Tab, Blueprint-Gate)"),
    PermissionDef("server.files.read",       "server", "Dateien lesen, downloaden"),
    PermissionDef("server.files.write",      "server", "Dateien hochladen, anlegen, bearbeiten, entpacken"),
    PermissionDef("server.files.delete",     "server", "Dateien loeschen"),
    PermissionDef("server.backups.read",     "server", "Backups auflisten"),
    PermissionDef("server.backups.create",   "server", "Backup erstellen"),
    PermissionDef("server.backups.restore",  "server", "Backup wiederherstellen"),
    PermissionDef("server.backups.delete",   "server", "Backup loeschen"),
    PermissionDef("server.mods.read",        "server", "Mods auflisten, Workshop durchsuchen"),
    PermissionDef("server.mods.write",       "server", "Mods abonnieren, entfernen, sortieren"),
    PermissionDef("server.mods.toggle",      "server", "Mods aktivieren oder deaktivieren"),
    PermissionDef("server.databases.read",   "server", "PostgreSQL-Datenbanken lesen"),
    PermissionDef("server.databases.write",  "server", "PostgreSQL-Tabellen und Daten bearbeiten"),
    PermissionDef("server.databases.admin",  "server", "PostgreSQL-Datenbanken und User verwalten"),
)


GLOBAL_KEYS: frozenset[str] = frozenset(p.key for p in GLOBAL_PERMISSIONS)
SERVER_KEYS: frozenset[str] = frozenset(p.key for p in SERVER_PERMISSIONS)
ALL_KEYS: frozenset[str] = GLOBAL_KEYS | SERVER_KEYS


def is_known_key(key: str) -> bool:
    return key in ALL_KEYS


def is_server_key(key: str) -> bool:
    return key in SERVER_KEYS


def is_global_key(key: str) -> bool:
    return key in GLOBAL_KEYS


# ── Built-in-Rollen ───────────────────────────────────────────────────
# admin = alle Keys (global + server-scoped pauschal); user = leer.
# owner ist KEINE Rolle, sondern das is_owner-Flag auf User (Bootstrap-Override).

SYSTEM_ROLE_ADMIN = "admin"
SYSTEM_ROLE_USER = "user"
SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({SYSTEM_ROLE_ADMIN, SYSTEM_ROLE_USER})


def admin_role_keys() -> frozenset[str]:
    return ALL_KEYS


def user_role_keys() -> frozenset[str]:
    return frozenset()


# ── Mapping: alte can_*-Spalten → neue Keys (fuer Lifespan-Migration) ──

LEGACY_PERMISSION_MAPPING: dict[str, tuple[str, ...]] = {
    "can_start":         ("server.start",),
    "can_stop":          ("server.stop",),
    "can_restart":       ("server.restart",),
    "can_update":        ("server.install",),
    "can_edit_config":   ("server.config.write", "server.files.read", "server.files.write"),
    "can_manage_mods":   ("server.mods.read", "server.mods.write", "server.mods.toggle"),
    "can_backup":        ("server.backups.read", "server.backups.create"),
    "can_restore":       ("server.backups.restore",),
    "can_view_console":  ("server.view", "server.console.read"),
    "can_view_logs":     ("server.view",),
}
