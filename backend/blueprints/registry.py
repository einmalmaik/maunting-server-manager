"""Blueprint-Registry — liest native + community Ordner und cached in-memory.

KISS: keine Watcher, kein Hot-Reload. Wir cachen einmal beim Import und stellen
``reload_registry()`` bereit, damit Router/Tests gezielt invalidieren koennen.

Pfade:
- Native: ``backend/blueprints/native/`` (read-only, im Repo committed)
- Community: ``settings.blueprints_dir`` (Default: ``/opt/msm/blueprints/community/``,
  bei Tests auf temporaeres Verzeichnis ueberschreibbar).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .schema import Blueprint, BlueprintValidationError, load_blueprint_file


logger = logging.getLogger(__name__)

_NATIVE_DIR = Path(__file__).resolve().parent / "native"

# Dateiname-Regel — keine Pfad-Trennzeichen, keine ``..``. Wir akzeptieren nur
# das Schema ``<id>.blueprint.json`` damit Listing+Loeschen vorhersehbar sind.
_FILENAME_RE = re.compile(r"^[a-z0-9_]{1,64}\.blueprint\.json$")


class BlueprintSourceOrigin(str, Enum):
    NATIVE = "native"
    COMMUNITY = "community"


@dataclass(frozen=True)
class BlueprintEntry:
    """Eine geladene Blueprint plus Herkunft."""

    blueprint: Blueprint
    origin: BlueprintSourceOrigin
    path: Path


class BlueprintRegistry:
    """In-Memory-Registry. Native + community zusammengefuehrt.

    Konflikt-Regel: native Blueprints haben **Vorrang**. Wenn eine Community-
    Blueprint dieselbe ID benutzt, wird sie ignoriert und der Konflikt geloggt.
    """

    def __init__(self) -> None:
        self._entries: dict[str, BlueprintEntry] = {}

    # ── public ────────────────────────────────────────────────────────────

    def list(self) -> list[BlueprintEntry]:
        return sorted(self._entries.values(), key=lambda e: e.blueprint.meta.id)

    def get(self, blueprint_id: str) -> BlueprintEntry | None:
        return self._entries.get(blueprint_id)

    def exists(self, blueprint_id: str) -> bool:
        return blueprint_id in self._entries

    # ── internal ─────────────────────────────────────────────────────────

    def _load_dir(self, directory: Path, origin: BlueprintSourceOrigin) -> None:
        if not directory.is_dir():
            return
        # ``iterdir`` reicht — wir lesen flach (keine Subordner erlaubt).
        for entry_path in sorted(directory.iterdir()):
            if not entry_path.is_file():
                continue
            if not _FILENAME_RE.match(entry_path.name):
                logger.warning(
                    "Blueprint-Datei ignoriert (Dateiname entspricht nicht "
                    "'<id>.blueprint.json'): %s",
                    entry_path.name,
                )
                continue
            expected_id = entry_path.name.removesuffix(".blueprint.json")
            try:
                blueprint = load_blueprint_file(entry_path)
            except BlueprintValidationError as exc:
                logger.warning(
                    "Blueprint %s ungueltig, wird uebersprungen: %s",
                    entry_path.name,
                    "; ".join(exc.errors),
                )
                continue
            if blueprint.meta.id != expected_id:
                logger.warning(
                    "Blueprint %s: meta.id='%s' stimmt nicht mit Dateinamen "
                    "ueberein — wird uebersprungen.",
                    entry_path.name,
                    blueprint.meta.id,
                )
                continue
            existing = self._entries.get(blueprint.meta.id)
            if existing is not None:
                if existing.origin == BlueprintSourceOrigin.NATIVE:
                    logger.info(
                        "Community-Blueprint '%s' ignoriert: native Blueprint "
                        "hat Vorrang.",
                        blueprint.meta.id,
                    )
                    continue
                logger.warning(
                    "Blueprint-ID-Konflikt: '%s' bereits geladen aus %s, "
                    "ueberschreibe mit %s.",
                    blueprint.meta.id,
                    existing.path,
                    entry_path,
                )
            self._entries[blueprint.meta.id] = BlueprintEntry(
                blueprint=blueprint, origin=origin, path=entry_path
            )


_REGISTRY: BlueprintRegistry | None = None


def _community_dir() -> Path:
    """Liest den Community-Pfad aus ``settings.blueprints_dir``.

    Inline-Import vermeidet zirkulaeren Import beim Modul-Load und macht das
    Modul testbar (Tests koennen ``settings.blueprints_dir`` patchen).
    """
    from config import settings  # local import — break cycle
    return Path(settings.blueprints_dir)


def _build_registry() -> BlueprintRegistry:
    registry = BlueprintRegistry()
    # Reihenfolge zaehlt: native zuerst, damit Community-IDs bei Konflikten ignoriert werden.
    registry._load_dir(_NATIVE_DIR, BlueprintSourceOrigin.NATIVE)
    community_dir = _community_dir()
    registry._load_dir(community_dir, BlueprintSourceOrigin.COMMUNITY)
    return registry


def get_registry() -> BlueprintRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def reload_registry() -> BlueprintRegistry:
    """Verwirft den Cache und liest beide Ordner neu. Nutzbar nach Import/Delete."""
    global _REGISTRY
    _REGISTRY = _build_registry()
    return _REGISTRY


def community_blueprint_path(blueprint_id: str) -> Path:
    """Liefert den erwarteten Datei-Pfad einer Community-Blueprint.

    Wirft ``ValueError``, wenn ``blueprint_id`` keine sichere Slug-ID ist.
    """
    if not re.fullmatch(r"[a-z0-9_]{1,64}", blueprint_id):
        raise ValueError("Ungueltige Blueprint-ID — nur ^[a-z0-9_]{1,64}$ erlaubt.")
    return _community_dir() / f"{blueprint_id}.blueprint.json"


def ensure_community_dir() -> Path:
    """Legt das Community-Verzeichnis an, falls noetig (mode 750)."""
    target = _community_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, 0o750)
    except OSError as exc:
        logger.warning("chmod 0750 fuer %s fehlgeschlagen: %s", target, exc)
    return target


def native_dir() -> Path:
    """Exportiert den nativen Pfad — Tests koennen ihn lesen."""
    return _NATIVE_DIR
