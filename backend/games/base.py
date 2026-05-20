from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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


class GamePlugin(ABC):
    game_id: str = ""
    game_name: str = ""
    supports_mods: bool = False

    @abstractmethod
    def install(self, server) -> dict:
        """Installiert den Server frisch via SteamCMD."""
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

    def get_mod_support(self) -> dict | None:
        """Liefert Mod-Metadaten, falls unterstützt."""
        if self.supports_mods:
            return {"workshop_id": None, "dependency_resolution": False}
        return None
