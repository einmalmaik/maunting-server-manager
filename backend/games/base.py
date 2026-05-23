import os
import threading
import subprocess
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConfigField:
    key: str
    label: str
    type: str  # text, number, bool, select, textarea
    default: Any = None
    options: list[str] | None = None
    description: str = ""
    required: bool = False


@dataclass
class ServerStatus:
    status: str  # stopped, running, installing, updating, error
    cpu_percent: float | None = None
    ram_mb: int | None = None
    disk_mb: int | None = None
    uptime_seconds: int | None = None
    players_online: int | None = None
    message: str | None = None


def _run_install_with_logging(cmd: list[str], server_id: int, install_dir: str) -> None:
    """Runs an install command in background, writes output to msm_console.log,
    and updates server status on completion."""
    log_path = os.path.join(install_dir, "msm_console.log")
    os.makedirs(install_dir, exist_ok=True)
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"[MSM] Installation gestartet...\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True
            )
            proc.wait()
            if proc.returncode == 0:
                log_file.write(f"\n[MSM] Installation abgeschlossen.\n")
            else:
                log_file.write(f"\n[MSM] Installation fehlgeschlagen (exit code {proc.returncode}).\n")
            log_file.flush()
    except Exception as e:
        logger.warning("Install failed for server %s: %s", server_id, e)
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n[MSM] Installation Fehler: {e}\n")
        except OSError:
            pass

    # Update server status in DB
    try:
        from database import SessionLocal
        from models import Server
        db = SessionLocal()
        try:
            srv = db.query(Server).filter(Server.id == server_id).first()
            if srv:
                srv.status = "stopped" if proc.returncode == 0 else "error"
                srv.status_message = "Installation abgeschlossen" if proc.returncode == 0 else f"Installation fehlgeschlagen (exit {proc.returncode})"
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("Could not update server status after install: %s", e)


class GamePlugin(ABC):
    game_id: str = ""
    game_name: str = ""
    supports_mods: bool = False

    @abstractmethod
    def install(self, server) -> dict:
        """Installiert den Server. Returns dict with 'message' key."""
        ...

    @abstractmethod
    def update(self, server) -> dict:
        """Aktualisiert den Server."""
        ...

    @abstractmethod
    def start(self, server) -> dict:
        """Startet den Server-Prozess."""
        ...

    @abstractmethod
    def stop(self, server) -> dict:
        """Stoppt den Server-Prozess."""
        ...

    @abstractmethod
    def get_status(self, server) -> ServerStatus:
        """Liefert aktuellen Server-Status."""
        ...

    @abstractmethod
    def get_logs(self, server, lines: int = 100) -> str:
        """Liest die letzten N Zeilen aus dem Log."""
        ...

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]:
        """Liefert die Config-Schema-Felder für dieses Spiel."""
        ...

    @abstractmethod
    def get_config_files(self) -> list[dict]:
        """Liefert die editierbaren Config-Dateien."""
        ...

    @abstractmethod
    def get_backup_paths(self, server) -> list[str]:
        """Liefert Pfade, die in Backups eingeschlossen werden sollen."""
        ...

    def get_console_log(self, server, lines: int = 200) -> str:
        """Reads the MSM console log (install output, system events)."""
        log_path = os.path.join(server.install_dir, "msm_console.log")
        if not os.path.exists(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:])
        except Exception:
            return ""

    def install_mod(self, server, workshop_id: str) -> dict:
        """Installs a single mod. Override in game-specific plugins."""
        return {"error": "Mod-Installation nicht implementiert"}

    def get_mod_support(self) -> dict | None:
        """Liefert Mod-Metadaten, falls unterstützt."""
        if self.supports_mods:
            return {"workshop_id": None, "dependency_resolution": False}
        return None


def build_systemd_unit(
    name: str,
    linux_user: str,
    working_dir: str,
    exec_start: str,
    cpu_limit_percent: int | None = None,
    ram_limit_mb: int | None = None,
    disk_limit_gb: int | None = None,
) -> str:
    """Erzeugt eine systemd-Unit mit Resource-Limits und Security-Hardening.

    Args:
        name: Anzeigename des Servers
        linux_user: Linux-User unter dem der Server läuft
        working_dir: Arbeitsverzeichnis
        exec_start: ExecStart-Kommando
        cpu_limit_percent: Max CPU-Usage (10-100). None = kein Limit.
        ram_limit_mb: Max RAM in MB. None = kein Limit.
        disk_limit_gb: Max Disk in GB. None = kein Limit.
          Hinweis: systemd kann kein hartes Disk-Limit. Wir nutzen
          ReadWritePaths + LimitNOFILE als Defense-in-Depth.
    """
    lines: list[str] = [
        "[Unit]",
        f"Description=MSM Server {name}",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={linux_user}",
        f"WorkingDirectory={working_dir}",
        f"ExecStart={exec_start}",
        "Restart=on-failure",
        "RestartSec=10",
        "StandardOutput=journal",
        "StandardError=journal",
        "",
        "# Security Hardening",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        f"ReadWritePaths={working_dir}",
        "TasksMax=100",
        "LimitNOFILE=4096",
    ]

    if cpu_limit_percent:
        lines.append(f"CPUQuota={cpu_limit_percent}%")

    if ram_limit_mb:
        lines.append(f"MemoryMax={ram_limit_mb}M")
        lines.append("MemorySwapMax=0")
        # OOM-Killer priorisiert den Game-Server niedriger, damit der Host stabil bleibt
        lines.append("OOMScoreAdjust=500")

    if disk_limit_gb:
        # systemd kann kein hartes Disk-Limit. Wir dokumentieren es
        # und setzen zusätzliche Einschränkungen.
        lines.append(f"# Disk-Limit: {disk_limit_gb}GB (Monitoring via Panel)")
        # Verhindert, dass der Prozess Dateien außerhalb seines Verzeichnisses anlegt
        lines.append("ProtectKernelTunables=true")
        lines.append("ProtectKernelModules=true")
        lines.append("ProtectControlGroups=true")

    lines.extend([
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ])

    return "\n".join(lines)
