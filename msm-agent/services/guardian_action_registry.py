"""Registered, bounded Guardian recovery actions.

Blueprints select only action IDs from this registry.  They can never provide
shell commands, executable paths, Docker commands or arbitrary delete paths.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from services import docker_service, file_service
from services.agent_operation_coordinator import operation
from services.guardian_contract import GuardianConfig, RECOVERY_ACTIONS


class UnsupportedRecoveryAction(ValueError):
    pass


class RecoveryPreconditionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryContext:
    server_id: int
    container_name: str
    guardian: GuardianConfig


@dataclass(frozen=True)
class RecoveryActionResult:
    ok: bool
    action_id: str
    details: dict[str, Any] = field(default_factory=dict)


class RecoveryAction:
    action_id: ClassVar[str]
    risk_class: ClassVar[str]
    required_capabilities: ClassVar[tuple[str, ...]] = ()
    requires_stopped_container: ClassVar[bool] = False
    requires_suspension_lease: ClassVar[bool] = False
    execution_timeout_seconds: ClassVar[int] = 120
    verification_required: ClassVar[bool] = True

    def execute(self, context: RecoveryContext) -> RecoveryActionResult:
        raise NotImplementedError


class RestartAction(RecoveryAction):
    action_id = "restart"
    risk_class = "medium"

    def execute(self, context: RecoveryContext) -> RecoveryActionResult:
        result = docker_service.restart_container(context.container_name, timeout=30)
        return RecoveryActionResult(bool(result.get("ok")), self.action_id)


class GracefulRestartAction(RecoveryAction):
    action_id = "graceful_restart"
    risk_class = "medium"

    def execute(self, context: RecoveryContext) -> RecoveryActionResult:
        stopped = docker_service.stop_container(context.container_name, timeout=30)
        if not stopped.get("ok"):
            return RecoveryActionResult(False, self.action_id, {"phase": "stop"})
        started = docker_service.start_container(context.container_name)
        return RecoveryActionResult(
            bool(started.get("ok")),
            self.action_id,
            {"phase": "start"},
        )


def _path_overlaps_protected(path: PurePosixPath, protected: list[str]) -> bool:
    path_parts = path.parts
    for raw in protected:
        protected_parts = PurePosixPath(raw).parts
        common = min(len(path_parts), len(protected_parts))
        if path_parts[:common] == protected_parts[:common]:
            return True
    return False


def _assert_container_stopped(container_name: str) -> None:
    state = docker_service.inspect_container_state(container_name)
    if state is not None and bool(state.get("running")):
        raise RecoveryPreconditionError("container must be stopped before lock-file removal")


def _unlink_with_openat(root: Path, relative: PurePosixPath, container_name: str) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        name = relative.parts[-1]
        try:
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(before.st_mode):
            raise RecoveryPreconditionError("declared lock path is not a regular file")
        _assert_container_stopped(container_name)
        after = os.stat(name, dir_fd=current, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
            raise RecoveryPreconditionError("declared lock file changed during validation")
        os.unlink(name, dir_fd=current)
        return True
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _unlink_fallback(root: Path, relative: PurePosixPath, container_name: str) -> bool:
    candidate = root.joinpath(*relative.parts)
    try:
        before = candidate.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RecoveryPreconditionError("declared lock path is not a regular file")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise RecoveryPreconditionError("declared lock path escapes the server root") from exc
    _assert_container_stopped(container_name)
    after = candidate.lstat()
    if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
        raise RecoveryPreconditionError("declared lock file changed during validation")
    candidate.unlink()
    return True


def clear_declared_lock_files(context: RecoveryContext) -> list[str]:
    declared = context.guardian.recovery.safe_lock_files
    if not declared:
        raise RecoveryPreconditionError("no safe lock files were declared")
    root = file_service.server_root(context.server_id)
    if root.is_symlink() or not root.is_dir():
        raise RecoveryPreconditionError("server root is unavailable or unsafe")
    protected = context.guardian.backups.protected_paths
    removed: list[str] = []
    supports_openat = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd and os.unlink in os.supports_dir_fd
    for declaration in declared:
        relative = PurePosixPath(declaration.path)
        if _path_overlaps_protected(relative, protected):
            raise RecoveryPreconditionError("declared lock file overlaps a protected path")
        if supports_openat:
            deleted = _unlink_with_openat(root, relative, context.container_name)
        else:
            deleted = _unlink_fallback(root, relative, context.container_name)
        if deleted:
            removed.append(declaration.path)
    return removed


class ClearDeclaredLockFilesAction(RecoveryAction):
    action_id = "clear_declared_lock_files"
    risk_class = "high"
    requires_stopped_container = True

    def execute(self, context: RecoveryContext) -> RecoveryActionResult:
        state = docker_service.inspect_container_state(context.container_name)
        was_running = bool(state and state.get("running"))
        if was_running:
            stopped = docker_service.stop_container(context.container_name, timeout=30)
            if not stopped.get("ok"):
                return RecoveryActionResult(False, self.action_id, {"phase": "stop"})
        removed = clear_declared_lock_files(context)
        if was_running:
            started = docker_service.start_container(context.container_name)
            if not started.get("ok"):
                return RecoveryActionResult(
                    False,
                    self.action_id,
                    {"phase": "start", "removed_files": removed},
                )
        return RecoveryActionResult(True, self.action_id, {"removed_files": removed})


class QuarantineAction(RecoveryAction):
    action_id = "quarantine"
    risk_class = "low"
    verification_required = False

    def execute(self, context: RecoveryContext) -> RecoveryActionResult:
        return RecoveryActionResult(True, self.action_id, {"quarantine": True})


ACTION_REGISTRY: dict[str, type[RecoveryAction]] = {
    "restart": RestartAction,
    "graceful_restart": GracefulRestartAction,
    "clear_declared_lock_files": ClearDeclaredLockFilesAction,
    "quarantine": QuarantineAction,
}


def action_capabilities() -> list[str]:
    return sorted(ACTION_REGISTRY)


def _execute_locked(action_id: str, context: RecoveryContext) -> RecoveryActionResult:
    action_class = ACTION_REGISTRY.get(action_id)
    if action_class is None or action_id not in RECOVERY_ACTIONS:
        raise UnsupportedRecoveryAction(f"unsupported recovery action: {action_id}")
    with operation(context.server_id):
        return action_class().execute(context)


async def execute_action(action_id: str, context: RecoveryContext) -> RecoveryActionResult:
    action_class = ACTION_REGISTRY.get(action_id)
    if action_class is None:
        raise UnsupportedRecoveryAction(f"unsupported recovery action: {action_id}")
    return await asyncio.wait_for(
        asyncio.to_thread(_execute_locked, action_id, context),
        timeout=action_class.execution_timeout_seconds,
    )

