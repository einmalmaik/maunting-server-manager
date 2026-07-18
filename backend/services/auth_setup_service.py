"""Erkennung und Recovery fuer interaktive Auth-Flows in Server-Containern.

Rein generisch: kein Wissen ueber einzelne Spiele. Erkennung laeuft ueber
Log-Pattern + Filesystem, Blueprint-Schema bleibt unangetastet.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

# OAuth/Auth-Fehler im Server-Log. Hytale, generic-discord-bots, anything
# that uses oauth2 against a third-party token service matches this.
_OAUTH_PATTERNS = (
    re.compile(r'oauth2:.*"(invalid_grant|invalid_token|expired)"', re.IGNORECASE),
    re.compile(r"refresh token expired", re.IGNORECASE),
    re.compile(r"could not get signed URL.*manifest", re.IGNORECASE),
    re.compile(r"please visit the following URL to authenticate", re.IGNORECASE),
    re.compile(r"authorization code:", re.IGNORECASE),
)


def detect_auth_required(log_lines: list[str]) -> bool:
    """True wenn der Container-Output einen interaktiven Auth-Flow verlangt.

    Reine Funktion; keine Side-Effects, keine DB-Zugriffe. Wird sowohl im
    Lifecycle-Thread nach Container-Exit als auch im WebSocket-Stream
    periodisch aufgerufen.
    """
    for line in log_lines:
        for pattern in _OAUTH_PATTERNS:
            if pattern.search(line):
                return True
    return False


# Files die explizit als Auth-Files bekannt sind (Hytale, andere Spiele
# mit bekannter Persistenz). Diese werden IMMER moved, auch wenn das
# Generic-Pattern unten nicht matcht.
_KNOWN_AUTH_FILES: frozenset[str] = frozenset({
    ".hytale-auth-tokens.json",
    ".hytale-downloader-credentials.json",
})

# Generische Pattern: alles was nach Credential/Token aussieht.
# Case-insensitive, weil manche Spiele SCREAMING_SNAKE oder camelCase nutzen.
_GENERIC_AUTH_PATTERNS = (
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"auth.*token", re.IGNORECASE),
    re.compile(r"token.*auth", re.IGNORECASE),
    re.compile(r"\btoken\b.*\.json$", re.IGNORECASE),
)


def _is_credential_file(name: str) -> bool:
    if name in _KNOWN_AUTH_FILES:
        return True
    if not name.endswith(".json"):
        return False
    return any(p.search(name) for p in _GENERIC_AUTH_PATTERNS)


def move_credentials(install_dir: os.PathLike[str] | str) -> int:
    """Moves all credential files in ``install_dir`` to ``<name>.bak``.

    Returns the number of files moved. Idempotent: re-running is a no-op.
    """
    base = Path(install_dir)
    moved = 0
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix != ".json":
            continue
        if not _is_credential_file(entry.name):
            continue
        backup = entry.with_suffix(entry.suffix + ".bak")
        if backup.exists():
            backup.unlink()  # overwrite any stale backup
        entry.rename(backup)
        moved += 1
    return moved


def wait_for_credentials(
    install_dir: os.PathLike[str] | str,
    *,
    timeout: float = 120.0,
    poll_interval: float = 1.0,
) -> Optional[Path]:
    """Pollt ``install_dir`` auf frische Credential-Files.

    Success-Bedingung: ein Live-Credential-File (kein ``.bak``-Pendant)
    ist im Verzeichnis. Das ist genau der Zustand, den der In-Container-Auth-Flow
    hinterlaesst: ``move_credentials`` hat das Original zu ``<name>.bak``
    weggeschoben, der Container schreibt jetzt das frische File ohne
    ``.bak``.

    Returnt den Pfad des neuen Credential-Files, oder None bei Timeout.
    """
    base = Path(install_dir)
    deadline = time.monotonic() + timeout
    # Schnellerer Initial-Polling: erste 30s alle 0.5s, danach alle 2s.
    fast_until = time.monotonic() + min(30.0, timeout)

    while time.monotonic() < deadline:
        for entry in base.iterdir():
            if not entry.is_file():
                continue
            if not _is_credential_file(entry.name):
                continue
            # Live-Credential-Files haben einen Namen OHNE .bak.
            # ``.bak``-Dateien sind die weg geschobenen Originale und
            # zaehlen nicht als "frisch" - der Container hat sie nicht
            # angelegt.
            if entry.suffix == ".bak":
                continue
            return entry
        if time.monotonic() < fast_until:
            time.sleep(min(0.5, poll_interval / 2))
        else:
            time.sleep(poll_interval)
    return None


# ──────────────────────────────────────────────────────────────────
# Background Recovery (orchestration)
# ──────────────────────────────────────────────────────────────────
#
# Die Funktion ``run_auth_setup_recovery`` ist der Background-Task,
# der nach einem Auth-Detect im Lifecycle-Thread gestartet wird. Sie
# ist generisch: kennt kein Spiel, nur Blueprint-Plugin-API.


def run_auth_setup_recovery(
    *,
    server_id: int,
    install_dir: os.PathLike[str] | str,
    docker_image: str,
    container_command: list[str] | None,
    container_env: dict[str, str] | None,
    port_publishes: list,
    volume_binds: list,
    cpu_limit_percent: int | None,
    ram_limit_mb: int | None,
    container_user: str,
    container_workdir: str | None,
    container_read_only_rootfs: bool,
    container_tmpfs_paths: list[str] | None,
    container_extra_networks: list[str] | None,
    container_name: str,
    on_log,
    on_status,
    restart_callback,
    wait_timeout: float = 300.0,
    node=None,
) -> str:
    """Orchestriert den Auth-Setup-Recovery-Flow.

    Diese Funktion laeuft im Background-Thread. Sie macht:

    1. Move credentials -> .bak (Auth-Flow wird erzwungen).
    2. Startet Container mit tty=True, ohne startup_check.
    3. Wartet bis wait_for_credentials neue Tokens findet.
    4. Stoppt den Auth-Container und ruft restart_callback fuer Clean-Restart.

    ``on_log(text)`` ist ein UI-Callback (z.B. _append_console_log) fuer Live-Output.
    ``on_status(status, message)`` aktualisiert die DB-Spalten auth_required/status_message.
    ``restart_callback()`` wird bei Erfolg aufgerufen (typischerweise eine
    queue_lifecycle_operation("restart", ...) - Call).

    Returnt einen Status-String ("recovered" | "no_credentials_moved" | "container_start_failed" | "timeout").
    """
    on_log(f"[MSM] Auth-Setup erkannt. Verschiebe Credentials...\n")

    remote_client = None
    if node is not None and not getattr(node, "is_local", True):
        from services.node_client import NodeClient

        remote_client = NodeClient.from_node(node)
        moved = 0
        for entry in remote_client.files_list(server_id):
            name = str(entry.get("name", ""))
            if entry.get("is_dir", False) or not _is_credential_file(name):
                continue
            try:
                remote_client.files_delete(server_id, f"{name}.bak")
            except Exception:
                pass
            remote_client.files_rename(server_id, name, f"{name}.bak")
            moved += 1
    else:
        moved = move_credentials(install_dir)
    if moved == 0:
        on_status(False, "Auth-Setup erforderlich, aber keine Credential-Dateien gefunden.")
        return "no_credentials_moved"

    on_log(f"[MSM] {moved} Credential-Datei(en) verschoben. Starte Auth-Setup-Container (TTY)...\n")

    # Import hier, um zirkulaere Imports zu vermeiden.
    from services import docker_service

    result = docker_service.run_container(
        name=container_name,
        image=docker_image,
        command=container_command,
        env=container_env,
        ports=port_publishes,
        volumes=volume_binds,
        cpu_limit_percent=cpu_limit_percent,
        ram_limit_mb=ram_limit_mb,
        user=container_user,
        workdir=container_workdir,
        read_only_rootfs=container_read_only_rootfs,
        tmpfs_paths=container_tmpfs_paths,
        extra_networks=container_extra_networks,
        detach=True,
        startup_check_seconds=0.0,  # NICHT nach 2s abbrechen - wir wollen auf Input warten
        server_id=server_id,
        tty=True,  # Pseudo-TTY fuer interaktiven Auth-Flow
        node=node,
    )
    if not result["ok"]:
        on_status(False, f"Auth-Setup-Container konnte nicht starten: {result['error']}")
        return "container_start_failed"

    on_log(
        f"[MSM] Auth-Setup-Container laeuft. Bitte URL im Konsolen-Tab oeffnen "
        f"und Anmeldung abschliessen (max. {int(wait_timeout)}s).\n"
    )

    if remote_client is not None:
        deadline = time.monotonic() + wait_timeout
        found_name = None
        while time.monotonic() < deadline:
            for entry in remote_client.files_list(server_id):
                name = str(entry.get("name", ""))
                if not entry.get("is_dir", False) and _is_credential_file(name):
                    found_name = name
                    break
            if found_name:
                break
            time.sleep(1.0)
        found = found_name
    else:
        found = wait_for_credentials(install_dir, timeout=wait_timeout)
    if found is None:
        on_status(False, "Auth-Setup Timeout. Bitte manuell pruefen.")
        docker_service.stop(container_name, timeout=10, node=node)
        return "timeout"

    found_label = found.name if isinstance(found, Path) else found
    on_log(f"[MSM] Neue Credentials gefunden ({found_label}). Starte Server neu...\n")
    docker_service.stop(container_name, timeout=15, node=node)
    on_status(False, None)  # auth_required -> False, status_message -> None
    restart_callback()
    return "recovered"
