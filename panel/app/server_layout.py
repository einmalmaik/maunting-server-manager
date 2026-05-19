from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_TEXT_EXTENSIONS = {
    ".cfg",
    ".conf",
    ".css",
    ".env",
    ".env.example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_INI_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")
_RECENT_SKIP_DIRS = {
    ".git",
    ".panel_runtime",
    "__pycache__",
    "backup",
    "steamcmd",
}
_RECENT_SKIP_SERVERFILES_DIRS = {
    "steamapps",
}
SERVERDZ_SCHEMA_SOURCE = "https://conanexiles.fandom.com/wiki/Dedicated_server_system_requirements#Official_server_config_files"


def get_runtime_home() -> Path:
    panel_runtime_user = os.getenv("PANEL_RUNTIME_USER", "")
    if panel_runtime_user:
        try:
            import pwd

            return Path(pwd.getpwnam(panel_runtime_user).pw_dir)
        except (ImportError, KeyError):
            return Path.home()
    return Path.home()


def get_servers_root() -> Path:
    explicit_root = os.getenv("CONAN_DATA_ROOT", "").strip()
    if explicit_root:
        resolved = Path(explicit_root).resolve()
        return resolved if resolved.name == "servers" else resolved / "servers"
    return (get_runtime_home() / "servers").resolve()


def get_server_base_dir(server_name: str) -> Path:
    servers_root = get_servers_root().resolve()
    candidate = (servers_root / server_name).resolve()
    try:
        candidate.relative_to(servers_root)
    except ValueError as exc:
        raise ValueError("Invalid server name.") from exc
    return candidate


def get_config_ini_path(base_dir: Path) -> Path:
    return base_dir / "config.ini"


def read_config_ini(base_dir: Path) -> tuple[Path, dict[str, str]]:
    path = get_config_ini_path(base_dir)
    values: dict[str, str] = {}
    if not path.is_file():
        return path, values

    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        match = _INI_KEY_RE.match(line)
        if not match:
            continue
        key, value = match.groups()
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] == '"':
            cleaned = cleaned[1:-1]
        values[key] = cleaned
    return path, values


def read_json_file(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def get_server_cfg_relative_path(base_dir: Path) -> str:
    _config_path, values = read_config_ini(base_dir)
    config_dir = (values.get("server_config_dir") or "ConanSandbox/Saved/Config/LinuxServer").strip().strip('"')
    config_path = Path(config_dir)
    if config_path.is_absolute() or any(part == ".." for part in config_path.parts):
        config_path = Path("ConanSandbox/Saved/Config/LinuxServer")
    return (Path("serverfiles") / config_path / "ServerSettings.ini").as_posix()


def get_server_cfg_path(base_dir: Path) -> Path:
    serverfiles_root = (base_dir / "serverfiles").resolve()
    cfg_path = (base_dir / Path(get_server_cfg_relative_path(base_dir))).resolve(strict=False)
    try:
        cfg_path.relative_to(serverfiles_root)
    except ValueError:
        return serverfiles_root / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "ServerSettings.ini"
    return cfg_path


def get_mission_folder(base_dir: Path) -> str | None:
    # Conan Exiles does not use DayZ-style mission folders. The key remains in
    # the API as a stable compatibility field for older panel clients.
    return None


def resolve_quick_files(base_dir: Path) -> list[dict[str, Any]]:
    _config_path, values = read_config_ini(base_dir)
    config_dir = (values.get("server_config_dir") or "ConanSandbox/Saved/Config/LinuxServer").strip().strip('"')
    if Path(config_dir).is_absolute() or any(part == ".." for part in Path(config_dir).parts):
        config_dir = "ConanSandbox/Saved/Config/LinuxServer"
    entries = [
        {
            "key": "server_settings",
            "label": "ServerSettings.ini",
            "path": (Path("serverfiles") / config_dir / "ServerSettings.ini").as_posix(),
        },
        {
            "key": "engine_ini",
            "label": "Engine.ini",
            "path": (Path("serverfiles") / config_dir / "Engine.ini").as_posix(),
        },
        {
            "key": "game_ini",
            "label": "Game.ini",
            "path": (Path("serverfiles") / config_dir / "Game.ini").as_posix(),
        },
        {
            "key": "modlist",
            "label": "modlist.txt",
            "path": "serverfiles/ConanSandbox/Mods/modlist.txt",
        },
        {
            "key": "save_db",
            "label": "game_0.db",
            "path": "serverfiles/ConanSandbox/Saved/game_0.db",
        },
        {
            "key": "config_ini",
            "label": "config.ini",
            "path": "config.ini",
        },
    ]

    for entry in entries:
        entry["exists"] = (base_dir / Path(entry["path"])).is_file()
    return entries


def resolve_quick_directories(base_dir: Path) -> list[dict[str, Any]]:
    entries = [
        {
            "key": "server_root",
            "label": "Server root",
            "path": "",
        },
        {
            "key": "serverfiles",
            "label": "serverfiles",
            "path": "serverfiles",
        },
        {
            "key": "saved",
            "label": "ConanSandbox/Saved",
            "path": "serverfiles/ConanSandbox/Saved",
        },
        {
            "key": "mods",
            "label": "ConanSandbox/Mods",
            "path": "serverfiles/ConanSandbox/Mods",
        },
    ]

    for entry in entries:
        entry["exists"] = (base_dir / Path(entry["path"])).exists() if entry["path"] else base_dir.exists()
    return entries


def collect_recent_files(base_dir: Path, *, limit: int = 20, root_path: str = "") -> list[dict[str, Any]]:
    root = (base_dir / root_path) if root_path else base_dir
    root = root.resolve()
    try:
        root.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise ValueError("Path is outside the server directory.") from exc

    if not root.exists():
        return []
    if root.is_file():
        candidates = [root]
    else:
        candidates: list[Path] = []
        base_resolved = base_dir.resolve()
        root_rel = root.relative_to(base_resolved).as_posix() if root != base_resolved else ""
        root_parts = tuple(part for part in root_rel.split("/") if part)

        def _skip_dir(parts: tuple[str, ...]) -> bool:
            if not parts:
                return False
            if root_parts and parts[: len(root_parts)] == root_parts:
                return False
            if parts[0] in _RECENT_SKIP_DIRS:
                return True
            if len(parts) >= 2 and parts[0] == "serverfiles" and parts[1] in _RECENT_SKIP_SERVERFILES_DIRS:
                return True
            return False

        for current_root, dirnames, filenames in os.walk(root, topdown=True):
            current_path = Path(current_root)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not (current_path / dirname).is_symlink()
                and not _skip_dir((current_path / dirname).resolve().relative_to(base_resolved).parts)
            ]
            for filename in filenames:
                candidate = current_path / filename
                if candidate.suffix.lower() not in _TEXT_EXTENSIONS or not candidate.is_file():
                    continue
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    recent: list[dict[str, Any]] = []
    for path in candidates[:limit]:
        stat_result = path.stat()
        recent.append(
            {
                "name": path.name,
                "path": path.relative_to(base_dir).as_posix(),
                "modified": int(stat_result.st_mtime),
                "size": stat_result.st_size,
            }
        )
    return recent
