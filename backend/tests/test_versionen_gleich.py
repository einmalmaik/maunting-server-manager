"""Alle Versionsstellen tragen dieselbe Version (AGENTS.md, Punkt 11).

`scripts/sync-tauri-version.py` setzt sie beim Release aus dem Tag. Bis
09.10.2026 prüfte kein Test, ob sie im Repo übereinstimmen, und der Browser
stand daneben auf 1.0.0.
"""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(pfad: str) -> str:
    return json.loads((ROOT / pfad).read_text(encoding="utf-8"))["version"]


def _cargo(pfad: str) -> str:
    return tomllib.loads((ROOT / pfad).read_text(encoding="utf-8"))["package"]["version"]


def _api() -> str:
    main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    return re.search(r'"version":\s*"([^"]+)"', main).group(1)


def test_jede_versionsstelle_traegt_dieselbe_version() -> None:
    stellen = {
        "frontend/package.json": _json("frontend/package.json"),
        "smart-system/src-tauri/tauri.conf.json": _json("smart-system/src-tauri/tauri.conf.json"),
        "smart-system/src-tauri/Cargo.toml": _cargo("smart-system/src-tauri/Cargo.toml"),
        "recovery/src-tauri/tauri.conf.json": _json("recovery/src-tauri/tauri.conf.json"),
        "recovery/src-tauri/Cargo.toml": _cargo("recovery/src-tauri/Cargo.toml"),
        "browser/package.json": _json("browser/package.json"),
        "browser/src-tauri/tauri.conf.json": _json("browser/src-tauri/tauri.conf.json"),
        "browser/src-tauri/Cargo.toml": _cargo("browser/src-tauri/Cargo.toml"),
        "backend/main.py (/api/version)": _api(),
    }
    erwartet = stellen["frontend/package.json"]
    abweichend = {stelle: v for stelle, v in stellen.items() if v != erwartet}
    assert not abweichend, f"Version {erwartet} erwartet, abweichend: {abweichend}"


def test_der_release_gleicht_auch_den_browser_an() -> None:
    skript = (ROOT / "scripts" / "sync-tauri-version.py").read_text(encoding="utf-8")
    for pfad in ("browser/src-tauri/tauri.conf.json", "browser/src-tauri/Cargo.toml", "browser/package.json"):
        assert pfad in skript
