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
    group: str       # "users", "panel", "servers", "server", "system", "ai" — UI-Gruppierung
    label: str       # kurzer DE-Text fuer das Settings-UI


# ── Globale Permissions ───────────────────────────────────────────────
# Werden in `role_permissions` einer Rolle zugeordnet.
# `servers.delete` ist BEWUSST global (destruktiv, nur Admin/Owner).

GLOBAL_PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef("users.read",                "users",   "Userliste sehen"),
    PermissionDef("users.manage",              "users",   "User anlegen, bearbeiten, löschen"),
    PermissionDef("users.permissions.manage",  "users",   "User-Rollen und Server-Permissions verwalten"),
    PermissionDef("roles.manage",              "users",   "Rollen anlegen, bearbeiten, löschen"),
    PermissionDef("panel.settings.read",       "panel",   "Panel-Einstellungen lesen"),
    PermissionDef("panel.settings.write",      "panel",   "Panel-Einstellungen ändern (Steam, E-Mail, ...)"),
    PermissionDef("panel.database.read",       "panel",   "Panel-Datenbank lesen"),
    PermissionDef("panel.database.admin",      "panel",   "Panel-Datenbank verwalten"),
    PermissionDef("servers.create",            "servers", "Neuen Server anlegen"),
    PermissionDef("servers.delete",            "servers", "Server löschen (global, nicht delegierbar)"),
    # Blueprints hingen bisher an `panel.settings.write` — dem Recht, das auch
    # Steam-Zugangsdaten und E-Mail-Versand öffnet. Ein Hoster, der jemandem
    # erlauben will, eine Spielversion zu pflegen, musste ihm damit das halbe
    # Panel geben. Eigenes Recht, weil es eine eigene Aufgabe ist.
    PermissionDef("blueprints.manage",         "servers", "Blueprints anlegen, ändern, löschen"),
    PermissionDef("system.view",               "system",  "System-Ressourcen, Interfaces, Version"),
    PermissionDef("system.audit.read",         "system",  "Admin-Audit-Log lesen (privilegierte Aktionen)"),
    PermissionDef("system.secrets.rotate",     "system",  "Cluster-Secrets rotieren (Managed-Postgres-Admin)"),
    PermissionDef("nodes.read",                "system",  "Nodeliste sehen"),
    PermissionDef("nodes.manage",              "system",  "Nodes anlegen, bearbeiten, löschen"),
    # Ein Team buendelt geteiltes KI-Wissen und gibt Serverrechte weiter. Die
    # Weitergabe ist nach oben gedeckelt (services/permission_service.py):
    # niemand kann ueber ein Team mehr vergeben, als er selbst direkt haelt.
    # Deshalb ist dieses Recht auch fuer Kunden vertretbar.
    PermissionDef("teams.create",              "users",   "Eigene Teams gründen und verwalten"),
    # AI-Rechte bleiben fein granular. Kein Recht impliziert freie Shell- oder
    # Host-Ausführung; spätere Tools prüfen zusätzlich ihr jeweiliges MSM-Recht.
    PermissionDef("ai.chat.use",               "ai",      "KI-Chat verwenden"),
    PermissionDef("ai.attachments.use",        "ai",      "Anhänge im KI-Chat verwenden"),
    PermissionDef("ai.memory.use",             "ai",      "Eigenes KI-Memory verwenden"),
    PermissionDef("ai.skills.use",             "ai",      "Freigegebene KI-Skills verwenden"),
    PermissionDef("ai.skills.manage",          "ai",      "KI-Skills erstellen und verwalten"),
    # Durchgesetzt in ai_action_service._execute_web_search. Ohne hinterlegten
    # Suchschluessel wird das Werkzeug dem Modell gar nicht erst angeboten.
    PermissionDef("ai.web_search.use",         "ai",      "Websuche über die KI verwenden"),
    # Durchgesetzt in routers/ai_settings.py::get_usage_overview. Bewusst nicht
    # an `panel.settings.read` gehaengt: wer Verbraeuche sieht, sieht das
    # Nutzungsverhalten fremder Kunden. Den *eigenen* Verbrauch zeigt
    # `/api/ai/usage/me` ohne Sonderrecht — wer abgewiesen wird, muss erfahren
    # duerfen, warum.
    PermissionDef("ai.usage.read.all",         "ai",      "KI-Nutzung aller Benutzer einsehen"),
    # Durchgesetzt in routers/ai_autonomy.py und services/ai_autonomy_service.py.
    PermissionDef("ai.autonomous.use",         "ai",      "Autonomen KI-Modus verwenden"),
    # Durchgesetzt in services/ai_task_service.py. Ein stehender Auftrag ist die
    # einzige Sache, die die KI *ohne* anwesenden Menschen in Gang setzt, ohne
    # dass eine Störung sie geweckt hat — deshalb ein eigenes Recht und keines
    # der vorhandenen. `ai.chat.use` wäre zu weit (jeder Chatbenutzer hätte es),
    # `ai.autonomous.use` zu eng: eine Aufgabe, die nur liest und berichtet,
    # verlangt keine Autonomie. Handelnde Aufgaben prüfen sie zusätzlich.
    PermissionDef("ai.tasks.manage",           "ai",      "Wiederkehrende KI-Aufgaben anlegen und verwalten"),
    # Durchgesetzt in routers/ai_voice.py beim WebSocket-Upgrade. Ein eigenes
    # Recht und nicht `ai.chat.use`, obwohl es dieselbe KI mit denselben
    # Werkzeugen ist: der Sprachweg bestaetigt Schreibaktionen per Stimme, und
    # eine gesprochene Zustimmung ist schwaecher als ein Klick. Wer das fuer
    # seine Kunden nicht will, muss es abwaehlen koennen, ohne ihnen den Chat zu
    # nehmen. Ausserdem kostet Sprache ein Vielfaches: Audio wird bei
    # `gpt-realtime-2.1` mit 32 USD je Million Eingabe- und 64 USD je Million
    # Ausgabetokens berechnet.
    PermissionDef("ai.voice.use",              "ai",      "Mit der KI sprechen (Realtime-Sprachmodus)"),
    # OAuth-Provider-Konfiguration (Phase 4 — Social Login).
    # `secret_update` ist bewusst separat: erfordert zusaetzliche Audit-Bestaetigung.
    # `test` ist read-only, damit ein Operator ohne write-Rechte die Konfiguration pruefen kann.
    PermissionDef("panel.oauth.read",          "panel",   "OAuth-Provider-Konfiguration lesen"),
    PermissionDef("panel.oauth.create",        "panel",   "OAuth-Provider anlegen"),
    PermissionDef("panel.oauth.update",        "panel",   "OAuth-Provider bearbeiten (Slug, Client-ID, Endpoints)"),
    PermissionDef("panel.oauth.delete",        "panel",   "OAuth-Provider löschen"),
    PermissionDef("panel.oauth.secret_update", "panel",   "OAuth-Client-Secret ändern (rotieren)"),
    PermissionDef("panel.oauth.test",          "panel",   "OAuth-Provider-Verbindung testen"),
    # Hoster-Anbindung (Phase 6). Bewusst getrennt von panel.settings.*, damit
    # ein Support-Mitarbeiter Vertraege einsehen kann, ohne API-Keys rotieren
    # oder Produkte umkonfigurieren zu duerfen.
    PermissionDef("panel.hoster.read",          "panel",   "Hoster-Integrationen und Verträge einsehen"),
    PermissionDef("panel.hoster.write",         "panel",   "Hoster-Integrationen, Produkte und Schlüssel verwalten"),
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
    PermissionDef("server.config.write",     "server", "Server-Einstellungen ändern (Name, Auto-Restart, Backup-Schedule)"),
    # Der Schluessel heisst historisch `server.update`, durchgesetzt wird er
    # aber an genau einer Stelle: routers/webhooks_outbound.py. Die
    # Spieldateien holt POST /servers/{id}/install, und das prueft
    # `server.install`. Das alte Label versprach beides. Wer daraufhin eine
    # Wartungsrolle mit `server.update` baute, vergab ein Recht, das die
    # Wartung gar nicht oeffnet - und uebersah, dass `server.install` sie
    # stillschweigend mitbringt. Umbenannt wird der Schluessel nicht: er steckt
    # in bestehenden `role_permissions`- und `server_permissions`-Zeilen, eine
    # Umbenennung waere eine Migration mit Rechteverlust als Fehlerfall.
    PermissionDef("server.update",           "server", "Outbound-Webhooks dieses Servers verwalten"),
    PermissionDef("server.network.manage",   "server", "Ports und Bind-IP ändern"),
    PermissionDef("server.resources.manage", "server", "CPU-/RAM-/Disk-Limits ändern"),
    PermissionDef("server.console.read",     "server", "Konsole und Logs lesen"),
    PermissionDef("server.console.write",    "server", "Befehle an die Konsole senden"),
    PermissionDef("server.console.exec",     "server", "Befehle im Container ausführen (Exec-Tab, Blueprint-Gate)"),
    PermissionDef("server.files.read",       "server", "Dateien lesen, downloaden"),
    PermissionDef("server.files.write",      "server", "Dateien hochladen, anlegen, bearbeiten, entpacken"),
    PermissionDef("server.files.delete",     "server", "Dateien löschen"),
    PermissionDef("server.backups.read",     "server", "Backups auflisten"),
    PermissionDef("server.backups.create",   "server", "Backup erstellen"),
    PermissionDef("server.backups.restore",  "server", "Backup wiederherstellen"),
    PermissionDef("server.backups.delete",   "server", "Backup löschen"),
    PermissionDef("server.mods.read",        "server", "Mods auflisten, Workshop durchsuchen"),
    PermissionDef("server.mods.write",       "server", "Mods abonnieren, entfernen, sortieren"),
    PermissionDef("server.mods.toggle",      "server", "Mods aktivieren oder deaktivieren"),
    PermissionDef("server.databases.read",   "server", "PostgreSQL-Datenbanken lesen"),
    PermissionDef("server.databases.write",  "server", "PostgreSQL-Tabellen und Daten bearbeiten"),
    PermissionDef("server.databases.admin",  "server", "PostgreSQL-Datenbanken und User verwalten"),
    # Phase 7: bestimmt, wer einem Server eigene Zugangsdaten zuweisen darf.
    # Eigene Zugangsdaten anlegen darf jeder Benutzer im Profil; hier geht es
    # ausschliesslich um die Bindung an einen konkreten Server.
    PermissionDef("server.credentials.manage", "server", "Zugangsdaten für diesen Server zuweisen"),
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
