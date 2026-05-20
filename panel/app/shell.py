from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_settings
from .server_layout import get_servers_root

logger = logging.getLogger(__name__)
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x1B\x07]*(?:\x07|\x1B\\))"
)
_TASK_CHANNELS = frozenset({"default", "workshop"})
_SUDO_FORWARD_ENV_KEYS = frozenset({"PANEL_SKIP_PRESTART"})


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class PanelCommandError(RuntimeError):
    def __init__(self, result: CommandResult):
        self.result = result
        message = _clean_command_text(result.stderr) or _clean_command_text(result.stdout) or "Command failed."
        super().__init__(message)


def _strip_ansi_sequences(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _clean_command_text(text: str) -> str:
    return _strip_ansi_sequences(text or "").replace("\r", "").strip()


def _normalize_task_channel(task_channel: str | None) -> str:
    channel = (task_channel or "default").strip().lower()
    if channel not in _TASK_CHANNELS:
        raise ValueError(f"Invalid task_channel: {task_channel!r}")
    return channel


def _task_artifact_paths(server_dir: Path, task_channel: str) -> tuple[Path, Path, Path]:
    channel = _normalize_task_channel(task_channel)
    if channel == "default":
        return (
            server_dir / "panel_action.log",
            server_dir / ".panel_task.json",
            server_dir / ".panel_task.lock",
        )

    suffix = f".{channel}"
    return (
        server_dir / f"panel_action{suffix}.log",
        server_dir / f".panel_task{suffix}.json",
        server_dir / f".panel_task{suffix}.lock",
    )


def _manager_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PANEL_SKIP_PRESTART"] = "1"
    return env


def _read_task_info(task_file_path: Path) -> dict[str, Any] | None:
    if not task_file_path.exists():
        return None
    try:
        with open(task_file_path, "r", encoding="utf-8") as handle:
            task = json.load(handle)
        return task if isinstance(task, dict) else None
    except Exception:
        return None


def _task_lock_owner_running(lock_file_path: Path) -> bool:
    try:
        with open(lock_file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        pid = int(payload.get("pid", 0))
    except Exception:
        return False

    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _claim_task_slot(task_file_path: Path, lock_file_path: Path, task_info: dict[str, Any]) -> None:
    for _ in range(2):
        try:
            fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _task_lock_owner_running(lock_file_path):
                raise RuntimeError(
                    f"An action is already running for server: {task_info.get('server_name') or 'default'}"
                )
            try:
                os.unlink(lock_file_path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError("Another action is already preparing to start.") from exc
            continue

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "action": task_info.get("action"),
                        "channel": task_info.get("channel"),
                        "server_name": task_info.get("server_name"),
                        "started_at": task_info.get("started_at"),
                    },
                    handle,
                )
        except Exception:
            try:
                os.unlink(lock_file_path)
            except OSError:
                pass
            raise
        return

    raise RuntimeError("Failed to reserve action slot.")


def _get_base_args(
    *,
    server_name: str | None = None,
    forwarded_env: dict[str, str] | None = None,
    manager_path: str | None = None,
) -> list[str]:
    """Helper to construct the base command with sudo support if needed."""
    settings = get_settings()
    path = manager_path or settings.conan_manager_path
    if not path:
        raise RuntimeError("Manager path is not configured.")
    runtime_user = os.getenv("PANEL_RUNTIME_USER", "")
    args: list[str] = []

    if runtime_user:
        try:
            import getpass
            if getpass.getuser() != runtime_user:
                args = ["sudo", "-n", "-u", runtime_user]
                forwarded_pairs = [
                    f"{key}={value}"
                    for key, value in (forwarded_env or {}).items()
                    if key in _SUDO_FORWARD_ENV_KEYS and value is not None
                ]
                if forwarded_pairs:
                    args += ["env", *forwarded_pairs]
        except (ImportError, Exception):
            # Fallback or ignore if getpass fails (e.g. no tty)
            pass

    args += ["bash", path]
    if server_name:
        args += ["--server", server_name]
    return args


def _get_base_args_for_manager(
    manager_path: str,
    server_name: str | None = None,
    forwarded_env: dict[str, str] | None = None,
) -> list[str]:
    """Helper to construct the base command with sudo support if needed."""
    runtime_user = os.getenv("PANEL_RUNTIME_USER", "")
    args: list[str] = []

    if runtime_user:
        try:
            import getpass
            if getpass.getuser() != runtime_user:
                args = ["sudo", "-n", "-u", runtime_user]
                forwarded_pairs = [
                    f"{key}={value}"
                    for key, value in (forwarded_env or {}).items()
                    if key in _SUDO_FORWARD_ENV_KEYS and value is not None
                ]
                if forwarded_pairs:
                    args += ["env", *forwarded_pairs]
        except (ImportError, Exception):
            pass

    args += ["bash", manager_path]
    if server_name:
        args += ["--server", server_name]
    return args


def run_game_command(
    manager_path: str,
    *args: str,
    server_name: str | None = None,
    expect_json: bool = False,
) -> Any:
    """Generic game command runner using the given manager script path."""
    if not manager_path:
        raise RuntimeError("Manager path is not configured.")

    s = get_settings()
    manager_env = _manager_env()
    base_args = _get_base_args_for_manager(manager_path, server_name=server_name, forwarded_env=manager_env)
    full_args = base_args + list(args)

    try:
        completed = subprocess.run(
            full_args,
            cwd=Path(manager_path).resolve().parent,
            capture_output=True,
            text=True,
            timeout=s.command_timeout,
            check=False,
            env=manager_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise PanelCommandError(
            CommandResult(
                args=full_args,
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {s.command_timeout}s.",
            )
        ) from exc

    result = CommandResult(
        args=full_args,
        returncode=completed.returncode,
        stdout=_clean_command_text(completed.stdout),
        stderr=_clean_command_text(completed.stderr),
    )

    if result.returncode != 0:
        raise PanelCommandError(result)

    if expect_json:
        if not result.stdout:
            raise RuntimeError("Bridge command returned empty output when JSON was expected.")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Bridge command returned invalid JSON: {exc}. Output: {result.stdout[:200]!r}"
            ) from exc

    return result


def _get_base_args_for_manager(
    manager_path: str,
    server_name: str | None = None,
    forwarded_env: dict[str, str] | None = None,
) -> list[str]:
    """Helper to construct the base command with sudo support if needed."""
    runtime_user = os.getenv("PANEL_RUNTIME_USER", "")
    args: list[str] = []

    if runtime_user:
        try:
            import getpass
            if getpass.getuser() != runtime_user:
                args = ["sudo", "-n", "-u", runtime_user]
                forwarded_pairs = [
                    f"{key}={value}"
                    for key, value in (forwarded_env or {}).items()
                    if key in _SUDO_FORWARD_ENV_KEYS and value is not None
                ]
                if forwarded_pairs:
                    args += ["env", *forwarded_pairs]
        except (ImportError, Exception):
            pass

    args += ["bash", manager_path]
    if server_name:
        args += ["--server", server_name]
    return args


def run_manager_command(
    *args: str,
    server_name: str | None = None,
    expect_json: bool = False,
    manager_path: str | None = None,
) -> Any:
    """Run a manager command; uses CONAN_MANAGER_PATH by default, or the given manager_path."""
    settings = get_settings()
    path = manager_path or settings.conan_manager_path
    if not path:
        raise RuntimeError("Manager path is not configured.")

    manager_env = _manager_env()
    base_args = _get_base_args(server_name=server_name, forwarded_env=manager_env, manager_path=path)
    full_args = base_args + list(args)
    try:
        completed = subprocess.run(
            full_args,
            cwd=settings.manager_workdir(path),
            capture_output=True,
            text=True,
            timeout=settings.command_timeout,
            check=False,
            env=manager_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise PanelCommandError(
            CommandResult(
                args=full_args,
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {settings.command_timeout}s.",
            )
        ) from exc

    result = CommandResult(
        args=full_args,
        returncode=completed.returncode,
        stdout=_clean_command_text(completed.stdout),
        stderr=_clean_command_text(completed.stderr),
    )

    if result.returncode != 0:
        raise PanelCommandError(result)

    if expect_json:
        if not result.stdout:
            raise RuntimeError("Bridge command returned empty output when JSON was expected.")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Bridge command returned invalid JSON: {exc}. Output: {result.stdout[:200]!r}"
            ) from exc

    return result


def fetch_core_status(server_name: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "status", server_name=server_name, expect_json=True)


def fetch_panel_status() -> Any:
    return run_manager_command("panel", "status", "--json", expect_json=True)


def fetch_backup_runs(server_name: str | None = None, manager_path: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "backups", server_name=server_name, expect_json=True, manager_path=manager_path)


def fetch_autorestart_status(server_name: str | None = None, manager_path: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "autorestart", server_name=server_name, expect_json=True, manager_path=manager_path)


def fetch_workshop_status(server_name: str | None = None, manager_path: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "workshop", server_name=server_name, expect_json=True, manager_path=manager_path)


def invoke_core_action(
    *args: str,
    server_name: str | None = None,
    manager_path: str | None = None,
) -> CommandResult:
    return run_manager_command(*args, server_name=server_name, expect_json=False, manager_path=manager_path)


def _validate_mod_id(mod_id: str) -> None:
    if not re.match(r'^\d+$', mod_id):
        raise ValueError(f"Invalid mod_id: {mod_id!r}")


def fetch_mods_list(server_name: str | None = None, manager_path: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "mods", "list", server_name=server_name, expect_json=True, manager_path=manager_path)


def mods_add(mod_id: str, mod_name: str, server_name: str | None = None, manager_path: str | None = None) -> Any:
    _validate_mod_id(mod_id)
    _validate_mod_name(mod_name)
    return run_manager_command("panel", "bridge", "mods", "add", mod_id, mod_name, server_name=server_name, expect_json=True, manager_path=manager_path)


def mods_remove(mod_id: str, server_name: str | None = None, manager_path: str | None = None) -> Any:
    _validate_mod_id(mod_id)
    return run_manager_command("panel", "bridge", "mods", "remove", mod_id, server_name=server_name, expect_json=True, manager_path=manager_path)


_VALID_MOD_TYPES = frozenset({"client", "server"})
_VALID_MOD_STATES = frozenset({"on", "off"})
def _validate_mod_name(mod_name: str) -> None:
    if not mod_name or len(mod_name) > 128:
        raise ValueError(f"Invalid mod_name: {mod_name!r}")
    if any(char in mod_name for char in (';', '"', '/', '\\')):
        raise ValueError(f"Invalid mod_name: {mod_name!r}")
    if re.search(r'[\x00-\x1f\x7f]', mod_name):
        raise ValueError(f"Invalid mod_name: {mod_name!r}")


def _validate_mod_type(mod_type: str) -> None:
    if mod_type not in _VALID_MOD_TYPES:
        raise ValueError(f"Invalid mod_type: {mod_type!r}. Must be one of: {sorted(_VALID_MOD_TYPES)}")


def _validate_state(state: str) -> None:
    if state not in _VALID_MOD_STATES:
        raise ValueError(f"Invalid state: {state!r}. Must be one of: {sorted(_VALID_MOD_STATES)}")


def mods_toggle(mod_id: str, mod_type: str, state: str, server_name: str | None = None, manager_path: str | None = None) -> Any:
    _validate_mod_id(mod_id)
    _validate_mod_type(mod_type)
    _validate_state(state)
    return run_manager_command(
        "panel", "bridge", "mods", "toggle", mod_id, mod_type, state,
        server_name=server_name,
        expect_json=True,
        manager_path=manager_path,
    )


def fetch_mods_timestamps(server_name: str | None = None, manager_path: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "mods", "timestamps", server_name=server_name, expect_json=True, manager_path=manager_path)


def mods_reorder(mod_ids: list[str], server_name: str | None = None, manager_path: str | None = None) -> Any:
    for mod_id in mod_ids:
        _validate_mod_id(mod_id)
    return run_manager_command("panel", "bridge", "mods", "reorder", *mod_ids, server_name=server_name, expect_json=True, manager_path=manager_path)


def invoke_mods_update_selective(mod_ids: list[str], server_name: str | None = None, manager_path: str | None = None) -> CommandResult:
    for mod_id in mod_ids:
        _validate_mod_id(mod_id)
    return run_manager_command("panel", "bridge", "mods", "update", *mod_ids, server_name=server_name, expect_json=False, manager_path=manager_path)


def invoke_workshop_autoupdate_set(interval_minutes: int, server_name: str | None = None, manager_path: str | None = None) -> CommandResult:
    if interval_minutes in {10, 30}:
        return run_manager_command(
            "workshop",
            "autoupdate",
            "set",
            "minutes",
            str(interval_minutes),
            server_name=server_name,
            expect_json=False,
            manager_path=manager_path,
        )

    if interval_minutes > 0 and interval_minutes % 60 == 0:
        return run_manager_command(
            "workshop",
            "autoupdate",
            "set",
            "interval",
            str(interval_minutes // 60),
            server_name=server_name,
            expect_json=False,
            manager_path=manager_path,
        )

    raise ValueError(f"Invalid interval_minutes: {interval_minutes!r}")


def invoke_workshop_autoupdate_clear(server_name: str | None = None, manager_path: str | None = None) -> CommandResult:
    return run_manager_command("workshop", "autoupdate", "clear", server_name=server_name, expect_json=False, manager_path=manager_path)


def fetch_servers_list(server_name: str | None = None) -> Any:
    return run_manager_command("panel", "bridge", "servers", server_name=server_name, expect_json=True)


def get_server_dir(server_name: str | None) -> Path:
    """Matches the logic in conanserver.sh and servers.py to find the server directory."""
    if server_name and ('/' in server_name or '\\' in server_name or '..' in server_name or server_name.startswith('.')):
        raise ValueError(f"Invalid server_name: {server_name!r}")
    return get_servers_root() / (server_name or "default")


def invoke_core_action_async(
    action_name: str,
    *args: str,
    server_name: str | None = None,
    task_channel: str = "default",
    manager_path: str | None = None,
) -> None:
    """Runs a core action in the background, logging output to a file."""
    server_dir = get_server_dir(server_name)
    normalized_channel = _normalize_task_channel(task_channel)
    log_file_path, task_file_path, task_lock_path = _task_artifact_paths(server_dir, normalized_channel)

    task_info = {
        "action": action_name,
        "channel": normalized_channel,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "server_name": server_name or "default",
    }
    task_slot_claimed = False
    try:
        _claim_task_slot(task_file_path, task_lock_path, task_info)
        task_slot_claimed = True
        with open(task_file_path, "w", encoding="utf-8") as f:
            json.dump(task_info, f)
    except Exception as exc:
        if task_slot_claimed:
            try:
                task_lock_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove task lock %s after startup error", task_lock_path)
        logger.error("Failed to write task info: %s", exc)
        raise

    def run_command():
        settings = get_settings()
        path = manager_path or settings.conan_manager_path
        manager_env = _manager_env()
        base_args = _get_base_args(server_name=server_name, forwarded_env=manager_env, manager_path=path)
        full_args = base_args + [action_name] + list(args)

        try:
            with open(log_file_path, "w") as log_f:
                log_f.write(f"--- Action: {action_name} started at {task_info['started_at']} ---\n")
                log_f.flush()
                process = subprocess.Popen(
                    full_args,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=settings.manager_workdir(path),
                    text=True,
                    env=manager_env,
                )
                try:
                    process.wait(timeout=settings.command_timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    task_info["status"] = "timeout"
                    task_info["error"] = "Process timed out"
                    task_info["finished_at"] = datetime.now().isoformat()
                else:
                    task_info["status"] = "finished" if process.returncode == 0 else "failed"
                    task_info["returncode"] = process.returncode
                    task_info["finished_at"] = datetime.now().isoformat()
        except Exception as exc:
            logger.exception("Async action %s failed", action_name)
            task_info["status"] = "failed"
            task_info["error"] = str(exc)
            task_info["finished_at"] = datetime.now().isoformat()

        try:
            with open(task_file_path, "w", encoding="utf-8") as f:
                json.dump(task_info, f)
        except Exception as exc:
            logger.error("Failed to update task info: %s", exc)
        finally:
            try:
                task_lock_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove task lock %s", task_lock_path)

    thread = threading.Thread(target=run_command, daemon=False)
    thread.start()


def fetch_action_task(server_name: str | None = None, task_channel: str = "default") -> dict | None:
    """Returns the current background task info for the server."""
    server_dir = get_server_dir(server_name)
    _log_file_path, task_file_path, _task_lock_path = _task_artifact_paths(server_dir, task_channel)

    return _read_task_info(task_file_path)


def fetch_action_log(server_name: str | None = None, task_channel: str = "default") -> list[str]:
    """Returns the last lines of the action log."""
    server_dir = get_server_dir(server_name)
    log_file_path, _task_file_path, _task_lock_path = _task_artifact_paths(server_dir, task_channel)

    if not log_file_path.exists():
        return []

    try:
        with open(log_file_path, "r") as f:
            return [
                _strip_ansi_sequences(line).replace("\r", "").rstrip("\n")
                for line in f.readlines()[-200:]
                if _strip_ansi_sequences(line).replace("\r", "").rstrip("\n")
            ]
    except Exception:
        return []
