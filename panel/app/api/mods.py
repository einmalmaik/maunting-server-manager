from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..game_profile import CONAN_WORKSHOP_APP_ID, workshop_content_dir
from ..models import AuditLog, User
from ..permissions import (
    P_MODS_INSTALL,
    P_MODS_MANAGE,
    P_MODS_REORDER,
    P_MODS_UPDATE,
    P_MODS_VIEW,
    P_WORKSHOP_UPDATE,
    require_perm,
)
from ..server_layout import get_server_base_dir, read_config_ini
from ..shell import (
    PanelCommandError,
    fetch_mods_list,
    fetch_mods_timestamps,
    fetch_workshop_status,
    invoke_core_action_async,
    invoke_workshop_autoupdate_clear,
    invoke_workshop_autoupdate_set,
    mods_add,
    mods_reorder,
    mods_remove,
    mods_toggle,
)
from .deps import get_db, require_server, require_server_with_info

router = APIRouter()
logger = logging.getLogger(__name__)

_STEAM_WORKSHOP_APP_ID = CONAN_WORKSHOP_APP_ID
_STEAM_QUERY_URL = "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/"
_STEAM_DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
_STEAM_WORKSHOP_PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"


def _workshop_busy_status_code(detail: str | None) -> int:
    normalized = (detail or "").lower()
    return 409 if "already running" in normalized or "preparing to start" in normalized else 500


def _record_audit(
    db: Session,
    user: User,
    action: str,
    target: str | None,
    status_value: str,
    detail: str | None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        actor_username=user.username,
        action=action,
        target=target,
        status=status_value,
        detail=detail,
    )
    try:
        with db.begin_nested():
            db.add(entry)
        db.commit()
    except Exception:
        logger.exception("Failed to record audit log action=%s user=%s", action, user.username)


@dataclass
class DependencyResolution:
    status: Literal["verified", "unverified"]
    mod_detail: dict[str, Any] | None
    dependencies: list[dict[str, Any]]
    message: str | None = None


def _sanitize_mod_name(title: str | None, fallback: str) -> str:
    candidate = (title or "").strip().lower()
    candidate = re.sub(r"[\x00-\x1f\x7f]", "", candidate)
    candidate = re.sub(r'[;"\\/]+', "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    while candidate.startswith("."):
        candidate = candidate[1:]
    if not candidate:
        return fallback
    return candidate[:128]


def _details_payload(mod_ids: list[str]) -> dict[str, Any]:
    post_data: dict[str, Any] = {"itemcount": len(mod_ids)}
    steam_key = os.getenv("STEAM_API_KEY", "").strip()
    if steam_key:
        post_data["key"] = steam_key
    for index, mod_id in enumerate(mod_ids):
        post_data[f"publishedfileids[{index}]"] = mod_id
    return post_data


async def _fetch_published_file_details(mod_ids: list[str]) -> list[dict[str, Any]]:
    if not mod_ids:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(_STEAM_DETAILS_URL, data=_details_payload(mod_ids))
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError("Steam API returned an error while checking dependencies.") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("Could not reach Steam to verify mod dependencies.") from exc

    try:
        details = response.json().get("response", {}).get("publishedfiledetails", [])
    except ValueError as exc:
        raise RuntimeError("Steam returned invalid dependency data.") from exc

    if not isinstance(details, list):
        raise RuntimeError("Steam returned an unexpected dependency response.")

    return [detail for detail in details if isinstance(detail, dict)]


def _extract_dependency_ids(mod_detail: dict[str, Any]) -> list[str]:
    dependency_ids: list[str] = []
    seen: set[str] = set()
    for child in mod_detail.get("children", []) or []:
        if str(child.get("filetype")) != "1":
            continue
        dependency_id = str(child.get("publishedfileid", "")).strip()
        if not re.fullmatch(r"\d+", dependency_id) or dependency_id in seen:
            continue
        seen.add(dependency_id)
        dependency_ids.append(dependency_id)
    return dependency_ids


def _build_fallback_dependency_detail(mod_id: str, title: str | None) -> dict[str, Any]:
    return {
        "publishedfileid": mod_id,
        "result": 1,
        "title": title or mod_id,
        "preview_url": "",
        "children": [],
    }


def _parse_required_items_from_workshop_html(page_html: str) -> list[dict[str, str]]:
    container_match = re.search(
        r'<div class="requiredItemsContainer" id="RequiredItems">(.*?)</div>\s*</div>',
        page_html,
        re.DOTALL | re.IGNORECASE,
    )
    if not container_match:
        return []

    required_items: list[dict[str, str]] = []
    seen: set[str] = set()
    for dependency_id, raw_title in re.findall(
        r'href="https://steamcommunity\.com/(?:workshop|sharedfiles)/filedetails/\?id=(\d+)"[^>]*>'
        r'\s*<div class="requiredItem">\s*(.*?)\s*</div>',
        container_match.group(1),
        re.DOTALL | re.IGNORECASE,
    ):
        clean_id = dependency_id.strip()
        if not re.fullmatch(r"\d+", clean_id) or clean_id in seen:
            continue
        seen.add(clean_id)
        title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        required_items.append({"id": clean_id, "title": title or clean_id})
    return required_items


async def _fetch_required_items_from_workshop_page(mod_id: str) -> list[dict[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(_STEAM_WORKSHOP_PAGE_URL.format(mod_id=mod_id))
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            "Steam did not return dependency metadata and the Workshop page could not be checked."
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            "Steam did not return dependency metadata and the Workshop page could not be checked."
        ) from exc

    return _parse_required_items_from_workshop_html(response.text)


async def _resolve_direct_dependency_refs(
    mod_id: str,
    mod_detail: dict[str, Any],
) -> list[tuple[str, str | None]]:
    dependency_ids = _extract_dependency_ids(mod_detail)
    if dependency_ids:
        return [(dependency_id, None) for dependency_id in dependency_ids]

    required_items = await _fetch_required_items_from_workshop_page(mod_id)
    return [(item["id"], item["title"]) for item in required_items]


async def _resolve_mod_dependencies(mod_id: str) -> DependencyResolution:
    try:
        details = await _fetch_published_file_details([mod_id])
    except RuntimeError as exc:
        return DependencyResolution(
            status="unverified",
            mod_detail=None,
            dependencies=[],
            message=str(exc),
        )

    mod_detail = next(
        (
            detail
            for detail in details
            if str(detail.get("publishedfileid", "")).strip() == mod_id
        ),
        None,
    )
    if not mod_detail or mod_detail.get("result") != 1:
        return DependencyResolution(
            status="unverified",
            mod_detail=None,
            dependencies=[],
            message="Steam did not return dependency metadata for this mod.",
        )

    details_by_id: dict[str, dict[str, Any]] = {mod_id: mod_detail}
    ordered_dependency_ids: list[str] = []
    queued_ids: list[str] = [mod_id]
    seen_dependency_ids: set[str] = {mod_id}

    while queued_ids:
        current_id = queued_ids.pop(0)
        current_detail = details_by_id[current_id]
        try:
            dependency_refs = await _resolve_direct_dependency_refs(current_id, current_detail)
        except RuntimeError as exc:
            return DependencyResolution(
                status="unverified",
                mod_detail=mod_detail,
                dependencies=[],
                message=str(exc),
            )

        new_dependency_refs = [
            (dependency_id, dependency_title)
            for dependency_id, dependency_title in dependency_refs
            if dependency_id != mod_id and dependency_id not in seen_dependency_ids
        ]
        if not new_dependency_refs:
            continue

        missing_ids = [dependency_id for dependency_id, _dependency_title in new_dependency_refs]
        try:
            fetched_dependencies = await _fetch_published_file_details(missing_ids)
        except RuntimeError as exc:
            return DependencyResolution(
                status="unverified",
                mod_detail=mod_detail,
                dependencies=[],
                message=str(exc),
            )

        fetched_by_id = {
            str(detail.get("publishedfileid", "")).strip(): detail
            for detail in fetched_dependencies
            if detail.get("result") == 1
        }

        for dependency_id, dependency_title in new_dependency_refs:
            dependency_detail = fetched_by_id.get(dependency_id)
            if dependency_detail is None:
                if not dependency_title:
                    return DependencyResolution(
                        status="unverified",
                        mod_detail=mod_detail,
                        dependencies=[],
                        message="Steam returned incomplete dependency metadata for this mod.",
                    )
                dependency_detail = _build_fallback_dependency_detail(dependency_id, dependency_title)
            elif dependency_title and not str(dependency_detail.get("title", "")).strip():
                dependency_detail = {**dependency_detail, "title": dependency_title}

            details_by_id[dependency_id] = dependency_detail
            seen_dependency_ids.add(dependency_id)
            ordered_dependency_ids.append(dependency_id)
            queued_ids.append(dependency_id)

    dependency_details = [details_by_id[dependency_id] for dependency_id in ordered_dependency_ids]

    return DependencyResolution(
        status="verified",
        mod_detail=mod_detail,
        dependencies=dependency_details,
    )


def _parse_workshop_cfg(workshop_cfg_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if not workshop_cfg_path.is_file():
        return entries
    raw = workshop_cfg_path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        mod_id, mod_name = parts
        if not re.fullmatch(r"\d+", mod_id):
            continue
        entries.append({"id": mod_id, "name": mod_name.strip()})
    return entries


def _parse_config_mod_names(value: str | None) -> list[str]:
    if not value:
        return []
    names: list[str] = []
    for part in value.split(";"):
        cleaned = part.strip()
        if not cleaned:
            continue
        names.append(cleaned.lstrip("@"))
    return names


def _scan_mod_symlinks(serverfiles_dir: Path) -> dict[str, dict[str, str | None]]:
    symlinks_by_name: dict[str, dict[str, str | None]] = {}
    if not serverfiles_dir.is_dir():
        return symlinks_by_name

    for child in serverfiles_dir.iterdir():
        if not child.name.startswith("@") or not child.is_symlink():
            continue
        rel_target = os.readlink(child)
        resolved_target = str(child.resolve(strict=False))
        mod_name = child.name[1:]
        symlinks_by_name[mod_name] = {
            "path": child.name,
            "target": rel_target,
            "resolved_target": resolved_target,
        }
    return symlinks_by_name


async def _build_mod_analysis(server: str) -> dict[str, Any]:
    base_dir = get_server_base_dir(server)
    workshop_entries = _parse_workshop_cfg(base_dir / "workshop.cfg")
    _config_path, config_values = read_config_ini(base_dir)
    client_names = set(_parse_config_mod_names(config_values.get("workshop")))
    server_names = set(_parse_config_mod_names(config_values.get("servermods")))
    serverfiles_dir = base_dir / "serverfiles"
    workshop_dir = workshop_content_dir(serverfiles_dir)
    symlinks_by_name = _scan_mod_symlinks(serverfiles_dir)

    try:
        timestamp_data = fetch_mods_timestamps(server_name=server)
        local_timestamps: dict[str, int] = timestamp_data.get("timestamps", {})
    except Exception:
        local_timestamps = {}

    try:
        mods_data = fetch_mods_list(server_name=server)
        active_mods: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in mods_data.get("mods", [])}
    except Exception:
        active_mods = {}

    configured_ids = [entry["id"] for entry in workshop_entries]
    details_by_id: dict[str, dict[str, Any]] = {}
    dependency_ids_by_mod: dict[str, list[str]] = {}
    required_by: dict[str, list[str]] = {}
    steam_error: str | None = None
    if configured_ids:
        try:
            detail_rows = await _fetch_published_file_details(configured_ids)
            details_by_id = {
                str(detail.get("publishedfileid", "")).strip(): detail
                for detail in detail_rows
                if detail.get("result") == 1
            }
            for mod_id, detail in details_by_id.items():
                dep_ids = _extract_dependency_ids(detail)
                dependency_ids_by_mod[mod_id] = dep_ids
                for dependency_id in dep_ids:
                    required_by.setdefault(dependency_id, []).append(mod_id)
        except RuntimeError as exc:
            steam_error = str(exc)

    id_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    for entry in workshop_entries:
        id_counts[entry["id"]] = id_counts.get(entry["id"], 0) + 1
        name_counts[entry["name"]] = name_counts.get(entry["name"], 0) + 1

    analysis_rows: list[dict[str, Any]] = []
    conflict_total = 0
    for entry in workshop_entries:
        mod_id = entry["id"]
        mod_name = entry["name"]
        expected_target = str((workshop_dir / mod_id).resolve())
        installed = (workshop_dir / mod_id).exists()
        symlink = symlinks_by_name.get(mod_name)
        sources = ["workshop_cfg"]
        if mod_name in client_names or bool(active_mods.get(mod_id, {}).get("client")):
            sources.append("config_client")
        if mod_name in server_names or bool(active_mods.get(mod_id, {}).get("server")):
            sources.append("config_server")
        if required_by.get(mod_id):
            sources.append("dependency")

        conflicts: list[dict[str, str]] = []
        if id_counts.get(mod_id, 0) > 1:
            conflicts.append({"code": "duplicate_id", "message": "This Workshop ID appears more than once in workshop.cfg."})
        if name_counts.get(mod_name, 0) > 1:
            conflicts.append({"code": "duplicate_name", "message": "This mod name appears more than once in workshop.cfg."})
        if not installed:
            conflicts.append({"code": "missing_install", "message": "Workshop content is missing for this mod."})
        if symlink is None:
            conflicts.append({"code": "missing_symlink", "message": "The @mod symlink is missing from serverfiles."})
        elif symlink.get("resolved_target") != expected_target:
            conflicts.append({"code": "symlink_target_mismatch", "message": "The @mod symlink points to a different target than this mod ID."})

        conflict_total += len(conflicts)
        steam_detail = details_by_id.get(mod_id, {})
        analysis_rows.append(
            {
                "id": mod_id,
                "name": mod_name,
                "title": steam_detail.get("title") or mod_name,
                "sources": sources,
                "installed": installed,
                "enabled_client": mod_name in client_names or bool(active_mods.get(mod_id, {}).get("client")),
                "enabled_server": mod_name in server_names or bool(active_mods.get(mod_id, {}).get("server")),
                "symlink_path": symlink.get("path") if symlink else None,
                "symlink_target": symlink.get("target") if symlink else None,
                "expected_target": expected_target,
                "dependencies": dependency_ids_by_mod.get(mod_id, []),
                "required_by": required_by.get(mod_id, []),
                "local_timestamp": int(local_timestamps.get(mod_id, 0)),
                "steam_timestamp": int(steam_detail.get("time_updated", 0) or 0),
                "conflicts": conflicts,
            }
        )

    configured_names = {entry["name"] for entry in workshop_entries}
    stray_symlinks = [
        {
            "name": name,
            "path": info.get("path"),
            "target": info.get("target"),
        }
        for name, info in symlinks_by_name.items()
        if name not in configured_names
    ]
    config_only_mods = sorted(
        {
            *[name for name in client_names if name not in configured_names],
            *[name for name in server_names if name not in configured_names],
        }
    )

    return {
        "mods": analysis_rows,
        "summary": {
            "configured_mods": len(workshop_entries),
            "conflicts": conflict_total,
            "stray_symlinks": len(stray_symlinks),
            "config_only_mods": len(config_only_mods),
        },
        "stray_symlinks": stray_symlinks,
        "config_only_mods": config_only_mods,
        "steam_dependency_status": "verified" if steam_error is None else "unverified",
        "steam_dependency_error": steam_error,
    }


def _build_mod_dry_run(analysis: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, str]] = []
    noop_count = 0
    for mod in analysis.get("mods", []):
        if not mod.get("installed"):
            actions.append({"type": "install", "id": mod["id"], "name": mod["name"], "reason": "Workshop content is missing."})
            continue
        if any(conflict["code"] in {"missing_symlink", "symlink_target_mismatch"} for conflict in mod.get("conflicts", [])):
            actions.append({"type": "relink", "id": mod["id"], "name": mod["name"], "reason": "Symlink is missing or points to the wrong target."})
            continue
        if int(mod.get("steam_timestamp") or 0) > int(mod.get("local_timestamp") or 0):
            actions.append({"type": "update", "id": mod["id"], "name": mod["name"], "reason": "Steam reports a newer workshop timestamp."})
            continue
        noop_count += 1

    for stray in analysis.get("stray_symlinks", []):
        actions.append(
            {
                "type": "remove_symlink",
                "id": "",
                "name": stray["name"],
                "reason": "Symlink exists locally but the mod is no longer configured.",
            }
        )

    return {
        "actions": actions,
        "summary": {
            "total": len(actions),
            "noop": noop_count,
            "has_changes": len(actions) > 0,
        },
    }


# Mod List

@router.get("/mods")
def get_mods(
    user: User = require_perm(P_MODS_VIEW),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        return fetch_mods_list(server_name=server, manager_path=manager_path)
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("mods list failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to fetch mod list.")


# Add Mod

class AddModBody(BaseModel):
    mod_id: str
    mod_name: str
    confirm_unverified_dependencies: bool = False

    @field_validator("mod_id")
    @classmethod
    def validate_mod_id(cls, v: str) -> str:
        if not re.fullmatch(r"\d+", v.strip()):
            raise ValueError("mod_id must be a numeric Steam Workshop ID.")
        return v.strip()

    @field_validator("mod_name")
    @classmethod
    def validate_mod_name(cls, v: str) -> str:
        v = _sanitize_mod_name(v, "")
        if not v:
            raise ValueError("mod_name is required.")
        if len(v) > 128:
            raise ValueError("mod_name must be 128 characters or fewer.")
        return v


@router.post("/mods")
async def add_mod(
    body: AddModBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_MODS_INSTALL),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    resolution = await _resolve_mod_dependencies(body.mod_id)
    if resolution.status == "unverified" and not body.confirm_unverified_dependencies:
        return {
            "ok": False,
            "confirm_required": True,
            "dependency_status": "unverified",
            "installed_dependencies": [],
            "message": resolution.message or "Dependencies could not be verified automatically.",
        }

    try:
        result = await asyncio.to_thread(mods_add, body.mod_id, body.mod_name, server_name=server, manager_path=manager_path)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        _record_audit(db, user, "mods.add", body.mod_id, "success", body.mod_name)
    except HTTPException:
        raise
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, "mods.add", body.mod_id, "failed", detail)
        logger.error("mods.add failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to add mod.")

    installed_dependencies: list[dict[str, str]] = []
    dep_error: str | None = None
    if resolution.status == "verified":
        for dependency in resolution.dependencies:
            dependency_id = str(dependency.get("publishedfileid", "")).strip()
            if not re.fullmatch(r"\d+", dependency_id) or dependency_id == body.mod_id:
                continue

            dependency_name = _sanitize_mod_name(
                str(dependency.get("title", "")).strip() or None,
                dependency_id,
            )
            try:
                dep_result = await asyncio.to_thread(
                    mods_add,
                    dependency_id,
                    dependency_name,
                    server_name=server,
                    manager_path=manager_path,
                )
                if isinstance(dep_result, dict):
                    dep_error_value = dep_result.get("error")
                    if dep_error_value == "mod already exists":
                        continue
                    if dep_error_value:
                        raise RuntimeError(dep_error_value)
                installed_dependencies.append({"id": dependency_id, "name": dependency_name})
                _record_audit(db, user, "mods.add.dep", dependency_id, "success", dependency_name)
            except PanelCommandError as exc:
                detail = exc.result.stderr or exc.result.stdout or str(exc)
                _record_audit(db, user, "mods.add.dep", dependency_id, "failed", detail)
                logger.warning("Dependency install failed for %s: %s", dependency_id, detail)
                dep_error = "One or more dependencies could not be added automatically."
            except Exception as exc:
                logger.warning("Dependency install skipped for %s: %s", dependency_id, exc)
                dep_error = "One or more dependencies could not be added automatically."
    else:
        dep_error = resolution.message or "Dependencies could not be verified automatically."

    response: dict[str, Any] = {
        "ok": True,
        "installed_dependencies": installed_dependencies,
        "dependency_status": resolution.status,
    }
    if dep_error:
        response["dep_warning"] = dep_error
    return response


# Remove Mod

@router.delete("/mods/{mod_id}")
def remove_mod(
    mod_id: str,
    db: Session = Depends(get_db),
    user: User = require_perm(P_MODS_MANAGE),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    if not re.fullmatch(r"\d+", mod_id):
        raise HTTPException(status_code=400, detail="Invalid mod_id.")
    try:
        result = mods_remove(mod_id, server_name=server, manager_path=manager_path)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        _record_audit(db, user, "mods.remove", mod_id, "success", None)
        return {"ok": True}
    except HTTPException:
        raise
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, "mods.remove", mod_id, "failed", detail)
        logger.error("mods.remove failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to remove mod.")


# Toggle Enable/Disable

class ToggleBody(BaseModel):
    mod_type: Literal["client", "server"]
    enabled: bool


@router.patch("/mods/{mod_id}/toggle")
def toggle_mod(
    mod_id: str,
    body: ToggleBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_MODS_MANAGE),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    if not re.fullmatch(r"\d+", mod_id):
        raise HTTPException(status_code=400, detail="Invalid mod_id.")
    state = "on" if body.enabled else "off"
    action = f"mods.{'enable' if body.enabled else 'disable'}.{body.mod_type}"
    try:
        result = mods_toggle(mod_id, body.mod_type, state, server_name=server, manager_path=manager_path)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        _record_audit(db, user, action, mod_id, "success", None)
        return {"ok": True}
    except HTTPException:
        raise
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, action, mod_id, "failed", detail)
        logger.error("%s failed: %s", action, detail)
        raise HTTPException(status_code=500, detail="Failed to toggle mod.")


# Selective Update

class UpdateSelectiveBody(BaseModel):
    mod_ids: list[str]

    @field_validator("mod_ids")
    @classmethod
    def validate_mod_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("mod_ids must not be empty.")
        if len(v) > 100:
            raise ValueError("At most 100 mod IDs.")
        for item in v:
            if not re.fullmatch(r"\d+", item):
                raise ValueError(f"Invalid mod_id: {item!r}")
        return v


@router.get("/mods/updates")
async def get_mod_updates(
    user: User = require_perm(P_MODS_VIEW),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        mods_data = await asyncio.to_thread(fetch_mods_list, server_name=server, manager_path=manager_path)
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("mods list failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to fetch mod list.") from exc
    mods = mods_data.get("mods", [])
    if not mods:
        return {"mods": []}

    try:
        ts_data = await asyncio.to_thread(fetch_mods_timestamps, server_name=server, manager_path=manager_path)
        local_timestamps: dict[str, int] = ts_data.get("timestamps", {})
    except Exception as exc:
        logger.warning("Failed to fetch local mod timestamps: %s", exc)
        local_timestamps = {}

    mod_ids = [m["id"] for m in mods if "id" in m]
    post_data: dict[str, Any] = {"itemcount": len(mod_ids)}
    for i, mid in enumerate(mod_ids):
        post_data[f"publishedfileids[{i}]"] = mid
    steam_key = os.getenv("STEAM_API_KEY", "").strip()
    if steam_key:
        post_data["key"] = steam_key

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(_STEAM_DETAILS_URL, data=post_data)
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("Steam API update-check error: %s", exc)
        raise HTTPException(status_code=502, detail="Steam API returned an error.")
    except httpx.RequestError as exc:
        logger.error("Steam API update-check request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Steam API.")

    try:
        details = r.json().get("response", {}).get("publishedfiledetails", [])
    except ValueError:
        raise HTTPException(status_code=502, detail="Invalid JSON response from Steam API.")

    def _safe_int(val: object, default: int = 0) -> int:
        try:
            return int(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    steam_details: dict[str, int] = {
        d["publishedfileid"]: _safe_int(d.get("time_updated"))
        for d in details
        if d.get("result") == 1
    }

    return {"mods": [
        {
            "id": m["id"],
            "name": m.get("name", ""),
            "local_ts": int(local_timestamps.get(m["id"], 0)),
            "steam_ts": steam_details.get(m["id"], 0),
            "update_available": steam_details.get(m["id"], 0) > int(local_timestamps.get(m["id"], 0)),
        }
        for m in mods
    ]}


@router.patch("/mods/reorder")
def reorder_mods(
    body: UpdateSelectiveBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_MODS_REORDER),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    target_str = ",".join(body.mod_ids)[:128]
    try:
        result = mods_reorder(body.mod_ids, server_name=server, manager_path=manager_path)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        _record_audit(db, user, "mods.reorder", target_str, "success", None)
        return {"ok": True}
    except HTTPException:
        raise
    except PanelCommandError as exc:
        detail = (exc.result.stderr or exc.result.stdout or str(exc))[:255]
        _record_audit(db, user, "mods.reorder", target_str, "failed", detail)
        raise HTTPException(status_code=500, detail="Mod reorder failed.")


@router.post("/mods/update-selective")
def update_mods_selective(
    body: UpdateSelectiveBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_MODS_UPDATE),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    target_str = ",".join(body.mod_ids)[:128]
    try:
        invoke_core_action_async(
            "workshop",
            *body.mod_ids,
            server_name=server,
            task_channel="workshop",
            manager_path=manager_path,
        )
        _record_audit(db, user, "mods.update.selective", target_str, "started", None)
        return {"ok": True, "async": True}
    except PanelCommandError as exc:
        detail = (exc.result.stderr or exc.result.stdout or str(exc))[:255]
        _record_audit(db, user, "mods.update.selective", target_str, "failed", detail)
        raise HTTPException(
            status_code=_workshop_busy_status_code(detail),
            detail=detail or "Selective mod update failed.",
        )
    except Exception as exc:
        detail = str(exc)[:255]
        _record_audit(db, user, "mods.update.selective", target_str, "failed", detail)
        raise HTTPException(
            status_code=_workshop_busy_status_code(detail),
            detail=detail or "Selective mod update failed.",
        )


@router.get("/mods/analysis")
async def get_mod_analysis(
    user: User = require_perm(P_MODS_VIEW),
    server: str = Depends(require_server),
) -> Any:
    try:
        return await _build_mod_analysis(server)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("mod analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to analyze mods.")


@router.post("/mods/dry-run")
async def dry_run_mod_workshop(
    user: User = require_perm(P_MODS_VIEW),
    server: str = Depends(require_server),
) -> Any:
    try:
        analysis = await _build_mod_analysis(server)
        return _build_mod_dry_run(analysis)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("mod dry-run failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build workshop dry-run.")


# Workshop Auto-Update

class AutoUpdateBody(BaseModel):
    interval_minutes: int | None = None

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval_minutes(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in {10, 30, 60, 120, 180, 240, 360, 480, 720, 1440}:
            raise ValueError("interval_minutes must be one of: 10, 30, 60, 120, 180, 240, 360, 480, 720, 1440.")
        return value


@router.get("/mods/autoupdate")
def get_mod_autoupdate(
    user: User = require_perm(P_MODS_VIEW),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        status = fetch_workshop_status(server_name=server, manager_path=manager_path)
        return {
            "enabled": bool(status.get("autoupdate_enabled", False)),
            "interval_minutes": status.get("autoupdate_interval_minutes"),
            "display": status.get("autoupdate_display") or "Disabled",
            "scheduler_ready": bool(status.get("scheduler_ready", False)),
            "scheduler_error": status.get("scheduler_error") or None,
            "cron_active": bool(status.get("cron_active", False)),
            "cron_installed": bool(status.get("cron_installed", False)),
            "cron_service_name": status.get("cron_service_name") or "cron",
            "log_path": status.get("autoupdate_log_path") or None,
        }
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        logger.error("autoupdate list failed: %s", detail)
        raise HTTPException(status_code=500, detail=detail or "Failed to fetch auto-update status.")


@router.post("/mods/autoupdate")
def set_mod_autoupdate(
    body: AutoUpdateBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_WORKSHOP_UPDATE),
    server_info: dict[str, str] = Depends(require_server_with_info),
) -> Any:
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        if body.interval_minutes is None:
            invoke_workshop_autoupdate_clear(server_name=server, manager_path=manager_path)
            _record_audit(db, user, "mods.autoupdate.clear", None, "success", None)
        else:
            invoke_workshop_autoupdate_set(body.interval_minutes, server_name=server, manager_path=manager_path)
            _record_audit(db, user, "mods.autoupdate.set", str(body.interval_minutes), "success", None)
        return get_mod_autoupdate(user=user, server_info=server_info)
    except PanelCommandError as exc:
        detail = (exc.result.stderr or exc.result.stdout or str(exc))[:255]
        logger.error("autoupdate set/clear failed: %s", detail)
        raise HTTPException(status_code=500, detail=detail or "Failed to configure auto-update.")


# Steam API Proxy

def _get_steam_api_key() -> str:
    key = os.getenv("STEAM_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Steam API key not configured. "
                "Add STEAM_API_KEY to panel/.env and restart the panel service."
            ),
        )
    return key


@router.get("/mods/steam/search")
async def steam_search(
    q: str = Query(..., min_length=1, max_length=256),
    user: User = require_perm(P_MODS_VIEW),
) -> Any:
    api_key = _get_steam_api_key()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                _STEAM_QUERY_URL,
                params={
                    "key": api_key,
                    "appid": _STEAM_WORKSHOP_APP_ID,
                    "search_text": q,
                    "numperpage": 20,
                    "return_metadata": True,
                    "return_previews": True,
                },
            )
            r.raise_for_status()
            try:
                return r.json()
            except (ValueError, KeyError):
                raise HTTPException(status_code=502, detail="Invalid JSON response from Steam API.")
    except httpx.HTTPStatusError as exc:
        logger.error("Steam API search error: %s", exc)
        raise HTTPException(status_code=502, detail="Steam API returned an error.")
    except httpx.RequestError as exc:
        logger.error("Steam API request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Steam API.")


@router.get("/mods/steam/{mod_id}/with-deps")
async def steam_mod_with_deps(
    mod_id: str,
    user: User = require_perm(P_MODS_VIEW),
) -> Any:
    if not re.fullmatch(r"\d+", mod_id):
        raise HTTPException(status_code=400, detail="Invalid mod_id.")
    resolution = await _resolve_mod_dependencies(mod_id)
    if resolution.status != "verified" or resolution.mod_detail is None:
        raise HTTPException(
            status_code=502,
            detail=resolution.message or "Dependencies could not be verified automatically.",
        )
    return {"mod": resolution.mod_detail, "dependencies": resolution.dependencies}


@router.get("/mods/steam/{mod_id}")
async def steam_mod_details(
    mod_id: str,
    user: User = require_perm(P_MODS_VIEW),
) -> Any:
    if not re.fullmatch(r"\d+", mod_id):
        raise HTTPException(status_code=400, detail="Invalid mod_id.")
    try:
        details = await _fetch_published_file_details([mod_id])
    except RuntimeError as exc:
        logger.error("Steam API details error for %s: %s", mod_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"response": {"publishedfiledetails": details}}
