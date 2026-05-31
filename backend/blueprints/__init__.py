"""Blueprint-System — JSON-Definitionen fuer Server-Typen.

Blueprints sind **reine Daten**: keine Skripte, keine Hooks, keine Eval-Pfade.
Sie beschreiben einen Server-Typ (Docker-Image, Startup-Argv-Template,
Port-Rollen, Source-Strategie, optional Mods/Workshop). Native Plugins
(`backend/games/<id>/plugin.py`) lesen ihre Metadaten ebenfalls aus einer
nativen Blueprint-JSON unter `backend/blueprints/native/`.

Oeffentliche API:

- :class:`Blueprint`, :class:`BlueprintValidationError` — siehe ``schema``
- :func:`load_blueprint_file` — laedt + validiert eine Datei vom Disk
- :func:`render_argv` — substituiert Tokens im ``runtime.startup``-Template
- :func:`get_registry` / :func:`reload_registry` — kombiniert native + community

KISS: keine DB-Tabelle, kein Pickle, kein YAML. Wenn eine Blueprint nicht
unter ``community/`` liegt oder im Repo committed ist, existiert sie nicht.
"""

from __future__ import annotations

from .schema import (
    Blueprint,
    BlueprintCategory,
    BlueprintMeta,
    BlueprintMods,
    BlueprintModInjection,
    BlueprintPort,
    BlueprintRuntime,
    BlueprintSource,
    BlueprintSourceType,
    BlueprintValidationError,
    COMMENTED_TEMPLATE_DE,
    COMMENTED_TEMPLATE_EN,
    load_blueprint_dict,
    load_blueprint_file,
)
from .registry import (
    BlueprintRegistry,
    BlueprintSourceOrigin,
    get_registry,
    reload_registry,
)
from .renderer import render_argv

__all__ = [
    "Blueprint",
    "BlueprintCategory",
    "BlueprintMeta",
    "BlueprintMods",
    "BlueprintModInjection",
    "BlueprintPort",
    "BlueprintRuntime",
    "BlueprintSource",
    "BlueprintSourceType",
    "BlueprintValidationError",
    "COMMENTED_TEMPLATE_DE",
    "COMMENTED_TEMPLATE_EN",
    "load_blueprint_dict",
    "load_blueprint_file",
    "BlueprintRegistry",
    "BlueprintSourceOrigin",
    "get_registry",
    "reload_registry",
    "render_argv",
]
