"""Backup-Provider Interface und DTOs.

Single Source of Truth fuer alle Backup-Storage-Adapter. Jeder Adapter
implementiert dieselben 5 Methoden hinter ``BackupProvider``. So kann der
eigentliche Backup-Service (``services/backup_service.py``) provider-agnostisch
bleiben und der gleiche Code funktioniert fuer local, s3, sftp, dropbox,
gcs, azure.

KISS-Prinzipien:
- Methoden sind SYNCHRON — der Aufrufer wrappt sie bei Bedarf in einen
  asyncio-Executor. Das haelt die Adapter klein und testbar.
- Verschluesselung passiert VOR ``upload()`` und NACH ``download()`` im
  Backup-Service. Adapter sehen nur Chiffretext und sind daher klein
  und koennen die Schluesselverwaltung ignorieren.
- Progress wird per Callback gemeldet: ``progress_cb(bytes_done, bytes_total)``.
  Adapter ohne nativen Progress-Support rufen den Callback einmalig am
  Ende auf (100 %).
- Fehlermeldungen sind generisch (kein Pfad-Leak, keine Token-Leak).
- ``BackupMetadata`` ist das portable Schema, das als ``.meta.json`` parallel
  zum Backup im Provider liegt. Es enthaelt nur oeffentliche Felder — der
  verschluesselte tar.gz traegt den sensitiven Inhalt.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# DTOs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupMetadata:
    """Portables Backup-Schema, gespeichert als ``<remote_key>.meta.json``.

    Wird auf jedem Backup als JSON abgelegt, damit Restore auf einem
    komplett anderen Host (anderer panel_url, andere Server-IDs) die
    originalen Limits + Ports + Server-Identitaet kennt.

    Felder sind bewusst klein gehalten und nur das, was ein Restore
    braucht. Sensitive Inhalte (Savegames, Configs mit Passwoertern)
    bleiben im verschluesselten tar.gz.
    """

    backup_version: int
    server_id: int
    server_name: str
    game_type: str
    created_at: str  # ISO 8601 UTC, z. B. "2026-06-06T15:30:00Z"
    panel_version: str
    cpu_limit_percent: Optional[int]
    ram_limit_mb: Optional[int]
    disk_limit_gb: Optional[int]
    public_bind_ip: Optional[str]
    ports: list[dict]  # [{"role": "game", "port": 25565, "protocol": "udp"}, ...]
    name: Optional[str] = None
    size_mb: Optional[int] = None
    # remote_key + provider werden beim Schreiben gefuellt; bei Restore
    # aus Cloud nicht erforderlich, weil der Provider-Stage sie ohnehin setzt.
    remote_key: Optional[str] = None

    def to_json(self) -> str:
        import json
        # asdict + custom handler fuer None-Werte
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "BackupMetadata":
        import json
        data = json.loads(raw)
        return cls(**data)


@dataclass(frozen=True)
class BackupLocation:
    """Wo ein Backup im Provider-Namespace liegt."""

    remote_key: str  # z. B. "msm-backups/42/server_42_20260606_153000.tar.gz.enc"
    size_mb: Optional[int] = None


# Progress-Callback: (bytes_done, bytes_total) -> None.
# Adapter ohne nativen Progress-Hook rufen einmalig (total, total) am Ende auf.
ProgressCallback = Callable[[int, int], None]


# ─────────────────────────────────────────────────────────────────────────────
# ABC
# ─────────────────────────────────────────────────────────────────────────────


class BackupProvider(ABC):
    """Single interface jeder Storage-Adapter implementiert.

    Security:
    - Kein Pfad-Leak in Exceptions (Adapter werfen generische Werte).
    - Kein Logging von remote_key / size in Adapter-Schicht (Aufrufer loggt).
    - Verschluesselung ist Aufrufer-Verantwortung — Adapter sehen Chiffretext.
    """

    name: str  # z. B. "local", "s3", "sftp", "dropbox", "gcs", "azure"

    @abstractmethod
    def test_connection(self) -> bool:
        """Prueft Credentials + Erreichbarkeit. Wirft generische ProviderError."""

    @abstractmethod
    def upload(
        self,
        local_path: Path,
        remote_key: str,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> BackupLocation:
        """Laedt eine (bereits verschluesselte) Datei in den Provider hoch."""

    @abstractmethod
    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        """Laedt eine Datei aus dem Provider herunter (Chiffretext — Aufrufer
        entschluesselt)."""

    @abstractmethod
    def delete(self, remote_key: str) -> None:
        """Loescht ein Remote-Objekt. Idempotent (kein Fehler bei Missing)."""

    @abstractmethod
    def list_metadata(self) -> list[BackupMetadata]:
        """Listet alle Backup-Metadaten im Provider (parallel zu den .enc-Dateien).
        Beschädigte/parse-failende meta.json-Dateien werden uebersprungen, nicht geworfen."""


class ProviderError(Exception):
    """Generischer Provider-Fehler. Sub-Klassen koennen spezifischer werden,
    aber die oeffentliche API wirft immer nur generische Strings ohne Pfade/Tokens."""
