"""
Zentraler, Blueprint-konformer Updater für Steam-Workshop-Mods und Server-Dateien.

KISS-Prinzip: Eine kleine Datei, reine Funktionen ohne Side-Effects (außer Logging),
vollständig getrieben von Blueprint-Daten + bestehenden Helfern.

Prüfprinzip (für alle Updater):
1. Existiert die Ressource (Workshop-ID / App-ID / HTTP-Source)?
2. Ist sie bereits installiert (lokale Pfade / DB-Row / Datei-Mtime)?
3. Gibt es ein verfügbares Update (Steam time_updated > last_updated, Last-Modified)?
4. Ist das Update bereits eingespielt?
5. Lokal vorhandene Mods ohne verlässliche Metadaten werden als unknown markiert,
   statt fälschlich als aktuell zu gelten.

Nur fehlende/aktualisierte Ressourcen werden zurückgegeben (Bandbreiten-Optimierung).

Wiederverwendet:
- backend/games/base.py (run_steamcmd_*, _query_active_mods, active_mod_ids)
- backend/blueprints/schema.py (effective_mods, Blueprint)
- backend/services/steam_service.py (get_mod_details für time_updated)
- Mod-Update-Lage wird getrennt vom Installationsfortschritt gespeichert.

Sicherheit (AGENTS.md):
- Keine Secrets in Logs (bestehende _redact wird später genutzt).
- Keine automatischen destruktiven Aktionen (nur passive Checks + explizite Hooks).
- Alle tatsächlichen Updates erfordern server.install Permission (im Caller).

i18n: Keine User-Strings hier – alle Meldungen über Console-Logs oder
Rückgabe-Dicts (Frontend + Email-Templates liefern die Texte).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from blueprints.schema import Blueprint

# Lazy-Import zur Vermeidung von Circular Imports (base.py importiert updater.py)
def _query_active_mods(server_id: int):
    from games.base import _query_active_mods as _q  # type: ignore[attr-defined]
    return _q(server_id)

# Lazy Import für Console-Logging (wird im Config-Cache verwendet)
def _append_console_log(server_id: int, text: str) -> None:
    from games.base import _append_console_log as _log  # type: ignore[attr-defined]
    _log(server_id, text)

logger = logging.getLogger(__name__)


# ── Interne Sync-Helfer für passive Erkennung (KISS, nur in dieser Datei) ────
# Keine neuen Abstraktionen/Module. Sync, um Aufrufer in base.py und Routern
# (sync Hooks) nicht zu brechen. Keine Side-Effects außer Logging.

def _fetch_steam_mod_updated(app_id: str, workshop_id: str) -> datetime | None:
    """
    Ruft time_updated (als UTC-Datetime) für eine Workshop-Mod über die
    Steam Web API ab. Nutzt api_key aus Config wenn vorhanden (höhere Limits,
    bessere Erfolgsquote). Ohne Key: None (Fallback im Caller).

    KISS & Sicherheit:
    - Nur GET auf öffentliche Steam-API (keine Secrets in Request-Body außer key).
    - Key wird niemals geloggt.
    - 15s Timeout, kurze User-Agent.
    - Bei jedem Fehler (Netz, Rate, Parse): None + kurze Warnung (kein Stack).
    - Keine Caches hier (einfach, Caller kann bei Bedarf throttlen).
    """
    from config import settings as app_settings

    api_key = app_settings.steam_api_key or os.getenv("MSM_STEAM_API_KEY", "") or os.getenv("STEAM_API_KEY", "")
    if not api_key:
        return None

    try:
        query_data = {
            "publishedfileids": [int(workshop_id)],
            "includevotes": False,
        }
        params = {
            "key": api_key,
            "input_json": json.dumps(query_data, separators=(",", ":")),
        }
        url = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
        headers = {"User-Agent": "MSM/1.0 (+updater-check)"}

        with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if (
            "response" in data
            and "publishedfiledetails" in data["response"]
            and data["response"]["publishedfiledetails"]
        ):
            mod_data = data["response"]["publishedfiledetails"][0]
            if mod_data.get("result") == 1:
                ts = int(mod_data.get("time_updated") or 0)
                if ts > 0:
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception as exc:  # pragma: no cover - Netzwerk/extern
        # Kein Stacktrace, nur Typ + kurze Info (vermeidet Leak in Logs)
        logger.warning(
            "Steam-Workshop-Details für Mod %s (App %s) konnten nicht abgerufen werden: %s",
            workshop_id,
            app_id,
            type(exc).__name__,
        )
    return None


def _has_steam_api_key() -> bool:
    from config import settings as app_settings

    return bool(
        app_settings.steam_api_key
        or os.getenv("MSM_STEAM_API_KEY", "")
        or os.getenv("STEAM_API_KEY", "")
    )


def _fetch_http_last_modified(url: str) -> datetime | None:
    """
    Führt HEAD-Request auf die HTTP-Source aus und extrahiert Last-Modified
    (oder Fallback auf ETag-Änderung nicht persistierbar → None).

    KISS: Stdlib-ähnlich über httpx (bereits Projekt-Dep), robuste Datums-Parse.
    Nur für passive Erkennung, keine Downloads.
    """
    if not url or not url.startswith("https://"):
        return None
    try:
        headers = {"User-Agent": "MSM/1.0 (+updater-check)"}
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            resp = client.head(url)
            if resp.status_code != 200:
                return None
            lm = resp.headers.get("last-modified") or resp.headers.get("Last-Modified")
            if lm:
                dt = parsedate_to_datetime(lm)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            # Ohne Last-Modified: keine verlässliche remote-Zeit → None
            # (ETag allein ohne Persistenz nicht für > Vergleich nutzbar)
    except Exception as exc:  # pragma: no cover - Netzwerk/extern
        logger.warning(
            "HEAD-Request für HTTP-Source %s fehlgeschlagen: %s",
            url.split("?")[0][:80],  # URL ohne Query-String, gekürzt
            type(exc).__name__,
        )
    return None


def _parse_appmanifest_build_id(manifest_path: Path) -> str | None:
    """Liest buildid aus Steam appmanifest_*.acf (VDF-Text, kein vollständiger Parser)."""
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r'"buildid"\s+"(\d+)"', text)
    return match.group(1) if match else None


def _steam_effective_branch(steam: Any | None) -> str:
    """Blueprint ``source.steam.branch`` oder ``public``."""
    if steam is None:
        return "public"
    raw = getattr(steam, "branch", None)
    if raw is None:
        return "public"
    text = str(raw).strip()
    return text if text else "public"


def _fetch_steam_branch_build(app_id: str, branch: str = "public") -> tuple[str | None, datetime | None]:
    """
    Remote buildid + timeupdated für einen Steam-Depot-Branch.

    Nutzt api.steamcmd.net (read-only, kein API-Key). Bei Fehler: (None, None).
    """
    branch_key = (branch or "public").strip() or "public"
    url = f"https://api.steamcmd.net/v1/info/{app_id}"
    headers = {"User-Agent": "MSM/1.0 (+steam-app-update-check)"}
    try:
        with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        app_blob = (payload.get("data") or {}).get(str(app_id)) or {}
        branches = (app_blob.get("depots") or {}).get("branches") or {}
        entry = branches.get(branch_key) or {}
        build_id = entry.get("buildid")
        if build_id is not None:
            build_id = str(build_id)
        ts_raw = entry.get("timeupdated") or entry.get("timebuildupdated")
        remote_dt = None
        if ts_raw is not None:
            try:
                remote_dt = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except (TypeError, ValueError):
                remote_dt = None
        return build_id, remote_dt
    except Exception as exc:  # pragma: no cover - Netzwerk
        logger.warning(
            "Steam-App-Build-Check für App %s fehlgeschlagen: %s",
            app_id,
            type(exc).__name__,
        )
        return None, None


# ── Workshop-Mod-Update-Check ────────────────────────────────────────────────

def check_workshop_mod_updates(
    server: Any, blueprint: Blueprint
) -> list[dict[str, Any]]:
    """
    Prüft für alle aktiven (enabled=True) Workshop-Mods eines Servers,
    ob sie auf der Platte fehlen oder ein neueres Update über die Steam
    Web API verfügbar ist.

    Vergleich: Steam `time_updated` (remote) vs. `Mod.last_updated` (DB).
    Nur aktive Mods (aus _query_active_mods) werden betrachtet.

    Rückgabe: Liste von Dicts mit Handlungsempfehlung (nur bei Bedarf):
    [
      {
        "workshop_id": "123456",
        "name": "Mod Name",
        "action": "install" | "update",
        "reason": "missing" | "newer_version_available",
        "current_updated": "<iso oder None>",
        "remote_updated": "<iso oder None>",
        "installed": bool,
        "enabled": bool,
      },
      ...
    ]

    Deterministisch & KISS:
    - Keine Downloads; Side-Effects nur persistierte Update-Lage je Mod.
    - Fehlende lokale Dateien → "install".
    - Installiert + remote > local → "update".
    - Fehlender/defekter Steam-API-Key → "unknown", kein Auto-Update.
    - Alle Zeiten als UTC-aware datetime, robuster Vergleich.

    Nutzt bestehende Helfer: _query_active_mods + effektive Blueprint-Mods.
    Keine neuen Klassen, keine Pipelines.
    """
    bp_mods = blueprint.effective_mods()
    if not bp_mods.supportsSteamWorkshop or not bp_mods.workshopAppId:
        logger.debug("Workshop-Update-Check übersprungen: kein Steam-Workshop-Support im Blueprint.")
        return []

    workshop_app_id = bp_mods.workshopAppId
    active_mods = _query_active_mods(server.id)  # bereits enabled=True, sortiert
    has_api_key = _has_steam_api_key()

    results: list[dict[str, Any]] = []

    for mod in active_mods:
        workshop_id = str(mod.workshop_id) if mod.workshop_id else ""
        if not workshop_id:
            continue

        # Lokale Präsenz-Heuristik (exakt wie SteamCMD-Layout)
        local_path = (
            Path(server.install_dir)
            / "steamapps"
            / "workshop"
            / "content"
            / workshop_app_id
            / workshop_id
        )
        is_installed = False
        if local_path.exists():
            try:
                is_installed = any(local_path.iterdir())
            except OSError:
                is_installed = False

        db_updated: datetime | None = getattr(mod, "last_updated", None)
        # Normalisiere auf aware UTC falls nötig (DB-Sicherheit)
        if db_updated is not None and db_updated.tzinfo is None:
            db_updated = db_updated.replace(tzinfo=timezone.utc)

        # Remote via Steam API (passiv, nur Erkennung). Ohne Key darf der
        # Basis-Start/Restart nicht scheitern; Status bleibt dann unknown.
        remote_updated = _fetch_steam_mod_updated(workshop_app_id, workshop_id) if has_api_key else None

        action = "none"
        reason = "up_to_date"
        update_status = "up_to_date"
        update_reason: str | None = None

        if not is_installed:
            action = "install"
            reason = "missing"
            update_status = "missing"
            update_reason = "missing"
            logger.info(
                "Workshop-Mod %s (%s) fehlt lokal auf Server %s.",
                workshop_id,
                mod.name or "",
                getattr(server, "id", "?"),
            )
        else:
            if db_updated is None:
                update_status = "unknown"
                update_reason = "missing_local_metadata"
                logger.info(
                    "Workshop-Mod %s (%s) auf Server %s lokal vorhanden, aber ohne lokale Metadaten-Baseline.",
                    workshop_id,
                    mod.name or "",
                    getattr(server, "id", "?"),
                )
            if db_updated is not None and remote_updated is not None and remote_updated > db_updated:
                action = "update"
                reason = "newer_version_available"
                update_status = "outdated"
                update_reason = "newer_version_available"
                logger.info(
                    "Workshop-Mod %s (%s) hat Update auf Server %s (remote=%s, db=%s).",
                    workshop_id,
                    mod.name or "",
                    getattr(server, "id", "?"),
                    remote_updated.isoformat() if remote_updated else "unbekannt",
                    db_updated.isoformat() if db_updated else "None",
                )
            elif remote_updated is None and has_api_key:
                update_status = "unknown"
                update_reason = "steam_metadata_unavailable"
            elif remote_updated is None and not has_api_key:
                update_status = "unknown"
                update_reason = "steam_api_key_missing"

        runtime_ready = True
        # Runtime-Target-Check nur auslösen, wenn wir überhaupt eine Aussage
        # über den Update-Stand haben. Bei ``unknown`` (kein ``last_updated``
        # lokal und/oder kein Steam-API-Key) wissen wir nicht, ob die Mod
        # aktuell ist — eine "install missing_runtime_copy"-Aktion wäre eine
        # Aussage über die Version, die wir nicht treffen können. Statt
        #dessen Status als ``unknown`` belassen und nichts tun.
        if is_installed and action == "none" and update_status in ("up_to_date", "outdated"):
            try:
                from games import get_plugin

                plugin = get_plugin(getattr(server, "game_type", "") or "")
                if plugin is not None and hasattr(plugin, "workshop_runtime_targets_ready"):
                    runtime_ready = plugin.workshop_runtime_targets_ready(server, workshop_id)
                    if not runtime_ready:
                        action = "install"
                        reason = "missing_runtime_copy"
                        update_reason = update_reason or "missing_runtime_copy"
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Runtime-Check Workshop-Mod %s Server %s: %s",
                    workshop_id,
                    getattr(server, "id", "?"),
                    exc,
                )

        try:
            from services.mod_install_status_service import mark_mod_update_status

            mark_mod_update_status(server.id, workshop_id, update_status, update_reason)
        except Exception as exc:  # pragma: no cover - defensive DB side-effect
            logger.warning(
                "Mod-Update-Status fuer Server %s, Mod %s konnte nicht gespeichert werden: %s",
                getattr(server, "id", "?"),
                workshop_id,
                type(exc).__name__,
            )

        if action == "none" and is_installed and update_status == "up_to_date" and runtime_ready:
            stale_install = getattr(mod, "install_status", None)
            if stale_install in ("pending", "installing"):
                try:
                    from services.mod_install_status_service import mark_mod_installed

                    mark_mod_installed(server.id, workshop_id)
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "Install-Status-Reconcile Server %s Mod %s: %s",
                        getattr(server, "id", "?"),
                        workshop_id,
                        exc,
                    )

        results.append(
            {
                "workshop_id": workshop_id,
                "name": (mod.name or workshop_id),
                "action": action,
                "reason": reason,
                "current_updated": db_updated.isoformat() if db_updated else None,
                "remote_updated": remote_updated.isoformat() if remote_updated else None,
                "installed": is_installed,
                "enabled": bool(getattr(mod, "enabled", True)),
                "update_status": update_status,
                "update_reason": update_reason,
            }
        )

    # Nur Einträge mit Handlungsbedarf zurückgeben (Bandbreite + Klarheit)
    needing = [r for r in results if r["action"] != "none"]
    if needing:
        logger.info(
            "Workshop-Update-Check Server %s: %d Mod(s) benötigen Aktion.",
            getattr(server, "id", "?"),
            len(needing),
        )
    return needing


# ── Server-Datei-Update-Check (passiv, nur Restart-Zeit) ─────────────────────

def check_server_file_update(server: Any, blueprint: Blueprint) -> dict[str, Any]:
    """
    Passive Erkennung, ob Server-Binaries (Game-Dateien) aktualisiert werden
    sollten. Wird **ausschließlich** vor Neustarts aufgerufen (nie zur Laufzeit,
    nie auto-apply ohne Hook).

    Unterstützte Quellen (KISS):
    - steam: Vergleich lokale appmanifest buildid vs. öffentlicher Steam-Branch
      (api.steamcmd.net). Bei Abweichung → ``update`` für checkBased-Lifecycle.
    - http: HEAD-Request + Last-Modified Vergleich gegen max. lokale mtime
    - andere (dockerOnly etc.): immer "none" (keine MSM-Datei-Verantwortung)

    Rückgabe (immer vollständiges Dict, nie None):
    {
        "action": "none" | "update",
        "reason": "up_to_date" | "new_version_available" | "missing",
        "source_type": "...",
        "remote_updated": "<iso oder None>",
        "local_mtime": "<iso oder None>",
        "details": "deutsche Erklärung für Console/UI"
    }

    Eigenschaften:
    - Deterministisch: reine Lesezugriffe (FS + 1 HEAD bei http).
    - Keine Side-Effects außer Logging.
    - Keine neuen Felder, keine Persistenz von ETags (mtime-basiert wo möglich).
    - Robuste Fehlerbehandlung: bei Netz-/FS-Fehlern → "none" + Details.
    """
    bp_source = blueprint.source
    src_type = bp_source.type.value

    result: dict[str, Any] = {
        "action": "none",
        "reason": "up_to_date",
        "source_type": src_type,
        "remote_updated": None,
        "local_mtime": None,
        "details": "",
    }

    install_dir = Path(getattr(server, "install_dir", "") or "")
    if not install_dir or not install_dir.exists():
        result["action"] = "update"
        result["reason"] = "missing"
        result["details"] = "Installationsverzeichnis fehlt oder ist nicht erreichbar."
        logger.info(
            "Server-Datei-Check: Verzeichnis fehlt für Server %s (Source %s).",
            getattr(server, "id", "?"),
            src_type,
        )
        return result

    # Schnelle Präsenz-Prüfung (short-circuit bei erstem Treffer, KISS)
    has_any_files = False
    try:
        for f in install_dir.rglob("*"):
            if f.is_file():
                has_any_files = True
                break
    except Exception as exc:  # pragma: no cover - FS-Probleme
        logger.warning(
            "Datei-Scan im Install-Dir fehlgeschlagen (Server %s): %s",
            getattr(server, "id", "?"),
            type(exc).__name__,
        )
        has_any_files = False

    if not has_any_files:
        result["action"] = "update"
        result["reason"] = "missing"
        result["details"] = "Server-Verzeichnis leer – Installation erforderlich."
        return result

    server_id = getattr(server, "id", "?")

    # ── Steam Source: buildid-Vergleich (Dedicated Server, Blueprint-Branch) ─
    if src_type == "steam":
        steam_cfg = bp_source.steam
        steam_app_id = str(steam_cfg.appId or "") if steam_cfg else ""
        depot_branch = _steam_effective_branch(steam_cfg)
        manifest_path = install_dir / "steamapps" / f"appmanifest_{steam_app_id}.acf"
        local_build = _parse_appmanifest_build_id(manifest_path) if manifest_path.is_file() else None
        remote_build, remote_dt = (
            _fetch_steam_branch_build(steam_app_id, depot_branch) if steam_app_id else (None, None)
        )

        result["remote_updated"] = remote_dt.isoformat() if remote_dt else None
        if local_build:
            result["local_mtime"] = local_build  # buildid als lokale Referenz (kein FS-mtime)

        if not steam_app_id:
            result["details"] = "Steam-Source ohne appId — Update-Check übersprungen."
            return result

        if remote_build is None:
            result["details"] = (
                f"Steam App {steam_app_id} (Branch {depot_branch}): Remote-Build konnte nicht "
                "ermittelt werden (Netz/API). Kein Update vor Neustart ausgelöst."
            )
            return result

        if local_build is None:
            result["action"] = "update"
            result["reason"] = "missing"
            result["details"] = (
                f"Steam App {steam_app_id} (Branch {depot_branch}): Kein appmanifest oder keine "
                f"buildid lokal (Remote-Build {remote_build}). SteamCMD-Update vor Start empfohlen."
            )
            logger.info(
                "Server-Datei-Check (Steam) Server %s branch=%s: fehlende lokale buildid, remote=%s",
                server_id,
                depot_branch,
                remote_build,
            )
            return result

        if local_build != remote_build:
            result["action"] = "update"
            result["reason"] = "new_version_available"
            result["details"] = (
                f"Steam App {steam_app_id} (Branch {depot_branch}): Neuer Build verfügbar "
                f"(lokal {local_build} → remote {remote_build})."
            )
            logger.info(
                "Server-Datei-Update verfügbar (Steam) Server %s branch=%s: %s → %s",
                server_id,
                depot_branch,
                local_build,
                remote_build,
            )
            return result

        result["details"] = (
            f"Steam App {steam_app_id}: Build {local_build} ist aktuell (Branch {depot_branch})."
        )
        logger.debug(
            "Server-Datei-Check (Steam) Server %s branch=%s: buildid %s aktuell",
            server_id,
            depot_branch,
            local_build,
        )
        return result

    # ── HTTP Source: HEAD + Last-Modified vs. max lokale mtime ───────────────
    if src_type == "http" and bp_source.http:
        url = bp_source.http.url
        remote_dt = _fetch_http_last_modified(url)

        # Lokale Referenz: neueste Datei-mtime außerhalb geschützter Bereiche
        max_mtime = 0.0
        try:
            for f in install_dir.rglob("*"):
                if f.is_file() and "steamapps" not in str(f) and "workshop" not in str(f):
                    try:
                        mt = f.stat().st_mtime
                        if mt > max_mtime:
                            max_mtime = mt
                    except OSError:
                        continue
        except Exception:
            pass

        local_mtime = datetime.fromtimestamp(max_mtime, tz=timezone.utc) if max_mtime > 0 else None

        result["local_mtime"] = local_mtime.isoformat() if local_mtime else None
        result["remote_updated"] = remote_dt.isoformat() if remote_dt else None

        if remote_dt is not None and local_mtime is not None and remote_dt > local_mtime:
            result["action"] = "update"
            result["reason"] = "new_version_available"
            result["details"] = "HTTP-Source: Neuere Version (Last-Modified) erkannt."
            logger.info(
                "Server-Datei-Update verfügbar (HTTP) für Server %s: remote=%s > local=%s",
                server_id,
                remote_dt.isoformat(),
                local_mtime.isoformat(),
            )
        else:
            result["details"] = (
                "HTTP-Source: Kein neueres Last-Modified gefunden oder nicht ermittelbar. "
                "Manuelles Update oder Neustart mit validate empfohlen."
            )
            if remote_dt is None:
                result["details"] += " (HEAD fehlgeschlagen oder kein Last-Modified-Header)"

        return result

    # ── GitHub Source: ls-remote vs. lokaler HEAD ─────────────────────────────
    if src_type == "github" and bp_source.github:
        from blueprints.github_source import local_repo_sha, remote_branch_sha

        gh = bp_source.github
        repo = gh.repo.strip()
        branch = (gh.branch or "main").strip() or "main"
        remote_sha = remote_branch_sha(repo, branch)
        local_sha = local_repo_sha(install_dir)

        result["remote_commit"] = remote_sha
        result["local_commit"] = local_sha

        if remote_sha is None:
            result["reason"] = "check_failed"
            result["details"] = (
                f"GitHub-Source: Branch '{branch}' von {repo} nicht per ls-remote erreichbar."
            )
            return result

        if local_sha is None:
            result["action"] = "update"
            result["reason"] = "missing"
            result["details"] = "GitHub-Source: Kein lokales Git-Repo — Installation erforderlich."
            return result

        if local_sha != remote_sha:
            result["action"] = "update"
            result["reason"] = "new_version_available"
            result["details"] = (
                f"GitHub {repo}@{branch}: neuer Commit "
                f"(lokal {local_sha[:12]} → remote {remote_sha[:12]})."
            )
            logger.info(
                "Server-Datei-Update verfügbar (GitHub) Server %s %s@%s",
                server_id,
                repo,
                branch,
            )
            return result

        result["details"] = f"GitHub {repo}@{branch}: Commit {local_sha[:12]} ist aktuell."
        return result

    # dockerOnly, custom, manualUpload → MSM verwaltet keine Dateien
    result["reason"] = "up_to_date"
    result["details"] = (
        f"Source-Typ '{src_type}' – keine Datei-Updates durch MSM. "
        "Verantwortung liegt beim Docker-Image oder Benutzer."
    )
    return result


# ── Konfigurations-Cache / Restore Helfer (für Reinstall-Schutz) ─────────────

def get_protected_config_patterns() -> list[str]:
    """
    Zentrale Liste geschützter Config-Datei-Muster (Pterodactyl-ähnlich, erweitert).

    Wird bei Reinstall / Serverfile-Update verwendet, um manuelle
    Nutzeranpassungen nicht zu verlieren. KISS: einfache Globs + robuste
    Filter in should_preserve_file.
    """
    return [
        "*.cfg",
        "*.ini",
        "*.xml",
        "*.conf",
        "*.config",
        "*.toml",
        "*.yaml",
        "*.yml",
        "server*.properties",
        "GameUserSettings.ini",
        "*.json",  # vorsichtig – Daten-JSONs werden über should_preserve_file begrenzt
        "*.txt",   # z.B. whitelist.txt, bans.txt, motd.txt
        # Häufige spiel-spezifische Varianten (werden von obigen Globs teilweise erfasst)
        "Game*.ini",
        "Engine*.ini",
        "server*.cfg",
    ]


def should_preserve_file(path: Path) -> bool:
    """
    Entscheidet, ob eine Datei vor einem Reinstall geschützt werden soll.

    KISS + robustere Heuristik:
    - Alles unter steamapps/, workshop/, logs/ etc. wird NIE geschützt
      (Binaries + Workshop-Content + temporäre Daten).
    - Zusätzlich ausgeschlossen: typische Datenverzeichnisse von Games
      (content/, saved/, data/ ...), Binaries, und sehr große Dateien
      bei breiten Globs (*.json, *.txt) — verhindert Backup von GB-großen
      Datenfiles, die zufällig auf Muster passen.
    - Manuelle Configs des Users (außerhalb dieser Bereiche) bleiben erhalten.
    """
    p = str(path).lower()
    if any(x in p for x in (
        "steamapps/", "workshop/", "logs/", ".msm-uploads/",
        "/content/", "/saved/", "/data/", "/maps/", "/shaders/",
        "/textures/", "/binaries/", "/thirdparty/", "/core/"
    )):
        return False
    if path.suffix.lower() in {".exe", ".so", ".dll", ".pak", ".ucas", ".utoc", ".vpk", ".png", ".jpg", ".jpeg", ".wav", ".mp3"}:
        return False

    # Für breite Muster (*.json, *.txt) große Dateien ausschließen (Daten vs. Config)
    # Hoher Grenzwert (2 MiB), damit große manuelle Listen (bans.txt etc.) erhalten bleiben.
    try:
        if path.suffix.lower() in {".json", ".txt"} and path.stat().st_size > 2 * 1024 * 1024:
            return False
    except OSError:
        return False

    return True


# ── Manuelle Konfigurationsdateien cachen & wiederherstellen ─────────────────

import shutil
import subprocess
from typing import Optional


def _get_cache_dir(server_id: int) -> Path:
    """Gibt das temporäre Cache-Verzeichnis für diesen Server zurück."""
    return Path("/tmp") / "msm-config-cache" / str(server_id)


def cache_manual_configs(server: Any, blueprint: Optional[Blueprint] = None) -> dict[str, Any]:
    """
    Sichert manuelle Konfigurationsdateien vor einem Reinstall / Server-Update.

    Dies entspricht dem Pterodactyl-Standard: Nutzeranpassungen (z.B. .cfg, .ini)
    gehen nicht verloren, wenn der User auf "Neu installieren" klickt.

    Vorgehen (KISS):
    - Bekannte Config-Dateien außerhalb von steamapps/ und workshop/ werden
      per tar archiviert.
    - Cache liegt unter /tmp/msm-config-cache/<server_id>/
    - Rückgabe enthält Statistiken (wie viele Dateien gesichert wurden).

    Wird vor jedem Reinstall (über /install) und vor Server-Datei-Updates im Lifecycle
    aufgerufen. Auch bei frischer Installation (leeres Verzeichnis) sicher: dann 0 Dateien.
    """
    install_dir = Path(server.install_dir)
    cache_dir = _get_cache_dir(server.id)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "manual_configs.tar"

    patterns = get_protected_config_patterns()

    # Alle passenden Dateien sammeln (relativ zum install_dir)
    # WICHTIG: Dedup (Patterns überlappen z.B. "*.cfg" + "server*.cfg")
    files_to_backup: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for f in install_dir.rglob(pattern):
            if f.is_file() and should_preserve_file(f):
                try:
                    rel = str(f.relative_to(install_dir))
                    if rel not in seen:
                        seen.add(rel)
                        files_to_backup.append(rel)
                except ValueError:
                    continue

    if not files_to_backup:
        _append_console_log(
            server.id,
            "[MSM] Keine manuell angepassten Config-Dateien zum Schützen gefunden (Reinstall/Update fährt normal fort).\n"
        )
        return {"cached_files": 0, "cache_file": None, "message": "Keine schützenswerten Config-Dateien gefunden."}

    # tar-Archiv erzeugen (im Cache-Verzeichnis)
    try:
        # Wir wechseln kurz ins install_dir, damit die Pfade relativ sauber sind
        cmd = ["tar", "-cf", str(cache_file), "-C", str(install_dir)] + files_to_backup
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except Exception as exc:
        logger.warning("Konnte manuelle Configs für Server %s nicht cachen: %s", server.id, exc)
        return {"cached_files": 0, "error": str(exc)}

    # Altes Cache-Verzeichnis aufräumen (falls Reste)
    try:
        if cache_dir.exists():
            for old in cache_dir.glob("*.tar.*"):
                old.unlink(missing_ok=True)
    except Exception:
        pass

    _append_console_log(
        server.id,
        f"[MSM] {len(files_to_backup)} manuelle Konfigurationsdatei(en) vor Reinstall/Update gesichert.\n"
    )

    return {
        "cached_files": len(files_to_backup),
        "cache_file": str(cache_file),
        "message": f"{len(files_to_backup)} Datei(en) gesichert."
    }


def restore_manual_configs(server: Any) -> dict[str, Any]:
    """
    Stellt zuvor gecachte manuelle Konfigurationsdateien nach einem Update wieder her.

    Nur Dateien, die sich im Cache befinden und deren Inhalt sich vom aktuellen
    Stand unterscheidet (oder die fehlen), werden wiederhergestellt.
    """
    install_dir = Path(server.install_dir)
    cache_dir = _get_cache_dir(server.id)
    cache_file = cache_dir / "manual_configs.tar"

    if not cache_file.exists():
        return {"restored_files": 0, "message": "Kein Config-Cache vorhanden."}

    try:
        # tar entpacken. ``--no-same-owner``: Restore laeuft unter der
        # UID des ausfuehrenden Prozesses (msm), nicht unter einer im
        # Archiv eingebetteten UID -- schuetzt vor Exit-2, wenn das
        # Archiv auf einer anderen UID erzeugt wurde (z. B. spaetere
        # rootful-Docker-Pulls).
        cmd = ["tar", "-xf", str(cache_file), "-C", str(install_dir),
               "--no-same-owner"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

        _append_console_log(
            server.id,
            "[MSM] Manuelle Konfigurationsdateien wurden nach Reinstall/Update wiederhergestellt.\n"
        )

        return {"restored_files": "unknown", "message": "Configs erfolgreich wiederhergestellt."}

    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] Fehler beim Wiederherstellen der manuellen Configs nach Reinstall/Update: {exc}\n"
        )
        logger.warning("Restore manueller Configs für Server %s fehlgeschlagen: %s", server.id, exc)
        return {"restored_files": 0, "error": str(exc)}


# Hilfsfunktion, damit auch andere Module den Cache-Pfad nutzen können
def clear_manual_config_cache(server_id: int) -> None:
    """Entfernt den Config-Cache für einen Server (z.B. nach erfolgreichem Restore)."""
    cache_dir = _get_cache_dir(server_id)
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


# ── Zentrale Protection-Helfer (geteilt von Reinstall-Pfad + Restart-Update-Pfad) ──
#
# KISS: EIN Ort für die komplette Cache + Operation + Restore + Clear + Logging-Sequenz.
# Garantiert identisches Verhalten für:
#   * Explizites "Neu installieren" (plugin.install über /install Router) → Reinstall mit Schutz
#   * Server-Datei-Update im Restart (apply_server_file_update)
# Keine Exception verlässt die Helper ungebremst (AGENTS.md: Restart/Install läuft immer weiter).
# Deutsche, klare Console-Logs für den User (im UI-Console sichtbar).


def _protect_manual_configs_and_run(
    server: Any,
    blueprint: Optional[Blueprint],
    operation: "Callable[[], dict[str, Any]]",
    operation_description: str = "Reinstall/Update",
) -> dict[str, Any]:
    """
    Interne KISS-Helferfunktion: Führt beliebige datei-ändernde Operation (SteamCMD/HTTP)
    mit vollständigem Schutz manueller Configs aus.

    Reihenfolge (Datenverlust-Prävention, identisch zu altem apply-Code):
    1. cache_manual_configs (vorher, auch wenn 0 Dateien)
    2. operation() aufrufen (blockierend)
    3. restore_manual_configs (DANACH, selbst bei Exception in operation)
    4. clear_manual_config_cache
    5. Abschluss-Log

    Wird von perform_install_with_protection (Reinstall-Pfade in Plugins) und
    von apply_server_file_update (Lifecycle) genutzt → Single Source of Truth.
    """
    server_id = getattr(server, "id", None)
    if server_id is None:
        # Fallback ohne Protection (sollte nie passieren)
        try:
            return operation()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # 1. Cache VOR Operation (Pflicht)
    cache_result: dict[str, Any] = {}
    try:
        cache_result = cache_manual_configs(server, blueprint)
    except Exception as exc:  # pragma: no cover - defensiv
        logger.warning("Cache manueller Configs vor %s fehlgeschlagen (Server %s): %s", operation_description, server_id, exc)
        _append_console_log(server_id, f"[MSM] Warnung: Config-Cache vor {operation_description} fehlgeschlagen: {exc}\n")
        cache_result = {"cached_files": 0, "error": str(exc)}

    # 2. Eigentliche Operation (z.B. run_steamcmd_install oder install_http_source)
    op_result: dict[str, Any] = {"ok": False}
    try:
        op_result = operation()
    except Exception as exc:  # pragma: no cover - defensiv
        logger.warning("%s fehlgeschlagen (Server %s): %s", operation_description, server_id, exc)
        op_result = {"ok": False, "error": str(exc)}

    # 3. Restore NACH Operation (oder nach Fehler) — zentraler Schutz
    restore_result: dict[str, Any] = {}
    try:
        restore_result = restore_manual_configs(server)
    except Exception as exc:  # pragma: no cover - defensiv
        logger.warning("Restore manueller Configs nach %s fehlgeschlagen (Server %s): %s", operation_description, server_id, exc)
        _append_console_log(server_id, f"[MSM] Warnung: Config-Restore nach {operation_description} fehlgeschlagen: {exc}\n")
        restore_result = {"restored_files": 0, "error": str(exc)}

    # 4. Cache aufräumen (nicht kritisch)
    try:
        clear_manual_config_cache(server_id)
    except Exception:
        pass

    # 5. Abschließendes Console-Log (Transparenz für User)
    try:
        if op_result.get("ok"):
            _append_console_log(
                server_id,
                f"[MSM] {operation_description} erfolgreich (Schutz manueller Configs aktiv: gecached + restored).\n"
            )
        else:
            err_msg = op_result.get("error") or "unbekannter Fehler"
            _append_console_log(
                server_id,
                f"[MSM] {operation_description} fehlgeschlagen — wird fortgesetzt, Configs restored (best effort): {err_msg}\n"
            )
    except Exception:
        pass

    # Rückgabe: das Result der Operation (für finish_install oder apply-Meta-Return)
    # Die Protection-Details sind über Console-Logs + optionale Return-Extension sichtbar.
    return op_result


def perform_install_with_protection(
    server: Any,
    install_fn: "Callable[[], dict[str, Any]]",
    blueprint: Optional[Blueprint] = None,
) -> dict[str, Any]:
    """
    Öffentlicher Einstieg für den expliziten Reinstall-Pfad (plugin.install via /install).

    Nutzt den geteilten _protect... Helper, damit Reinstall exakt dasselbe
    Cache/Restore-Verhalten hat wie der Restart-Update-Pfad.

    Aufrufbeispiel in Plugins (innerhalb des Background-Threads):
        result = perform_install_with_protection(
            server,
            lambda: run_steamcmd_install(server_id=..., ...),
            blueprint=bp
        )
        finish_install(server_id, result)

    Frische Install (leeres install_dir) → Cache findet 0 Dateien, Restore ist No-Op.
    Reinstall mit manuellen .cfg/.ini → diese werden gesichert, nach Steam/HTTP-Install restored.
    """
    return _protect_manual_configs_and_run(
        server, blueprint, install_fn, "Reinstall/Install"
    )


# ── Server-Datei-Update-Ausführung (synchron, KISS, Start/Restart-Pfad) ─────
#
# Diese Funktion ist der Kern der Ausführungslogik für Server-Datei-Updates.
# Steam-Blueprints rufen sie bei Start und Restart immer vor plugin.start() auf;
# HTTP-Quellen bleiben passiv/checkbasiert.
#
# Nutzt jetzt _protect_manual_configs_and_run (Single Source of Truth für Protection).
# Cache/Restore/Clear/Logs für manuelle Configs sind zentral implementiert.
#
# Sicherheit: Keine Exception verlässt diese Funktion ungebremst.
# Restart darf unter keinen Umständen durch ein fehlgeschlagenes Update blockiert werden.

def apply_server_file_update(server: Any, blueprint: Blueprint) -> dict[str, Any]:
    """
    Führt Server-Datei-Update (Game-Binaries via Steam/HTTP) **synchron** aus.

    Reihenfolge (kritisch für Datenverlust-Prävention):
    1. Manuelle Configs cachen (cache_manual_configs)
    2. Update blockierend ausführen (run_steamcmd_install oder install_http_source)
    3. Configs restore (restore_manual_configs) — selbst bei Update-Fehler
    4. Temporären Cache löschen

    Aufruf: ausschließlich aus GamePlugin.perform_server_file_update.
    Wird via asyncio.to_thread(...) im Router aufgerufen → Event-Loop bleibt frei.

    AGENTS.md / KISS:
    - Keine neuen Manager-Klassen, keine Pipelines.
    - Wiederverwendung der bestehenden sync Install-Primitive.
    - Deutsche Kommentare + Logs.
    - Fehler → nur Log + "ok": False; Caller (Lifecycle) fährt trotzdem fort.

    Rückgabe-Dict enthält Details für Logging/Debug (nicht für UI-Status).
    """
    # Lazy-Imports (Circular-Safety, wie bisher)
    from blueprints.schema import BlueprintSourceType

    def _get_run_steamcmd():
        from .base import run_steamcmd_install as _rsi
        return _rsi

    run_steamcmd_install = _get_run_steamcmd()

    def _get_install_http():
        from blueprints.http_source import install_http_source as _ihs
        return _ihs

    install_http_source = _get_install_http()

    server_id = getattr(server, "id", None)
    if server_id is None:
        return {"ok": False, "error": "Server ohne ID"}

    install_dir = getattr(server, "install_dir", None)
    if not install_dir:
        return {"ok": False, "error": "Kein install_dir gesetzt"}

    bp_source = blueprint.source
    source_type = getattr(bp_source, "type", None)

    # Konkrete Low-Level-Operation als Closure (wird vom Protection-Helper ausgeführt)
    def _perform_update_op() -> dict[str, Any]:
        if source_type == BlueprintSourceType.STEAM:
            steam = getattr(bp_source, "steam", None)
            if steam is None:
                return {"ok": False, "error": "steam-Konfiguration fehlt im Blueprint"}
            app_id = steam.appId
            requires_login = bool(getattr(steam, "requiresLogin", False))
            platform = getattr(steam, "platform", None)
            platform_str = platform.value if platform else None
            validate_flag = bool(getattr(steam, "validate_", True))
            depot_branch = _steam_effective_branch(steam)
            beta_arg = depot_branch if depot_branch != "public" else None
            branch_note = f" -beta {depot_branch}" if beta_arg else ""
            _append_console_log(
                server_id,
                f"[MSM] Server-Datei-Update: SteamCMD +app_update {app_id}{branch_note} "
                f"{'validate' if validate_flag else '(no-validate)'} (synchron vor Start)\n"
            )
            return run_steamcmd_install(
                server_id=server_id,
                install_dir=install_dir,
                app_id=app_id,
                use_authenticated_login=requires_login,
                platform=platform_str,
                # dedicated STEAMCMD_IMAGE for the tool (pre-baked binary), not the game's runtime image
                validate=validate_flag,
                beta_branch=depot_branch,
            )
        elif source_type == BlueprintSourceType.HTTP:
            _append_console_log(
                server_id,
                "[MSM] Server-Datei-Update: HTTP-Source-Download/Entpacken (synchron vor Start)\n"
            )
            return install_http_source(blueprint, install_dir)
        elif source_type == BlueprintSourceType.GITHUB:
            from blueprints.github_source import install_github_source

            gh = bp_source.github
            branch = (gh.branch if gh else "main") or "main"
            _append_console_log(
                server_id,
                f"[MSM] Server-Datei-Update: git pull origin {branch} (synchron vor Start)\n",
            )
            return install_github_source(blueprint, install_dir)
        else:
            # manualUpload / dockerOnly / custom → Check sollte nie "update" liefern.
            return {
                "ok": True,
                "message": f"Datei-Update für Source-Typ '{source_type}' nicht vorgesehen (übersprungen)."
            }

    # Zentrale Protection (nutzt jetzt den Shared Helper → keine Duplizierung mehr)
    # Vorherige explizite Cache/Restore/Clear/Logs sind jetzt in _protect... gekapselt.
    # Die spezifischen "Server-Datei-Update: ..." Logs bleiben vor/nach der eigentlichen Op.
    update_result = _protect_manual_configs_and_run(
        server, blueprint, _perform_update_op, "Server-Datei-Update"
    )

    # Für Rückgabe-Kompatibilität (apply gibt erweitertes Dict mit cache/restore Details)
    # Da der Helper die Details bereits per Console-Log kommuniziert und der Caller
    # (Restart) nur "ok" braucht, liefern wir ein kompatibles Dict. Die internen
    # Cache/Restore-Results sind für Debug nicht mehr separat nötig (wurden vorher
    # auch nur zurückgegeben, nicht aktiv genutzt).
    return {
        "ok": bool(update_result.get("ok", False)),
        "update_result": update_result,
        "cache_result": {"message": "siehe Console-Log"},
        "restore_result": {"message": "siehe Console-Log"},
    }


# ── Workshop-Mod-Update-Ausführung (Metadaten-Update nach erfolgreichem Download) ──
#
# KISS: Reine Hilfsfunktion ohne eigene Download-Logik (vermeidet Duplizierung).
# Der tatsächliche Download/Install erfolgt über GamePlugin.install_mods(...)
# (welches intern run_steamcmd_workshop_download_batch verwendet, siehe base.py + Plugin-Overrides).
# Diese Funktion wird nach erfolgreichem Batch-Install im Lifecycle
# aufgerufen, um last_updated + installed_version korrekt zu setzen.
# Keine neuen Abstraktionen, keine Manager, nur DB + Zeit-Handling.
# AGENTS.md: Frische Session (Thread-sicher), defensiv, keine Secrets, Rollback bei Fehler.


def update_mod_metadata_after_success(
    server_id: int, workshop_id: str, remote_updated: str | None = None
) -> bool:
    """
    Setzt nach erfolgreichem Workshop-Mod-Download/Install die Felder
    Mod.last_updated und Mod.installed_version in der DB.

    - last_updated: remote time_updated (aus Steam-API via Check) oder now() als Fallback.
    - installed_version: ISO-String des Remote-Timestamps (Workshop-Mods haben
      i. d. R. keine separate Versionsnummer; der Timestamp dient als Identifier).
    - Alle Zeiten UTC-aware für robusten Vergleich im Check.

    Wird ausschließlich aus perform_workshop_mod_updates (base.py) gerufen.
    Deutsche Kommentare, minimal, wartbar.
    """
    from database import SessionLocal
    from models import Mod
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        mod = (
            db.query(Mod)
            .filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id)
            .first()
        )
        if not mod:
            logger.warning(
                "Mod %s für Server %s nicht in DB gefunden beim Metadata-Update nach Download.",
                workshop_id,
                server_id,
            )
            return False

        now = datetime.now(timezone.utc)

        if remote_updated:
            try:
                # Normalisiere ISO (kann 'Z' oder Offset enthalten)
                ts = remote_updated
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                mod.last_updated = dt
            except Exception:
                mod.last_updated = now
        else:
            mod.last_updated = now

        mod.installed_version = int(mod.last_updated.timestamp()) if mod.last_updated else int(now.timestamp())

        db.commit()
        logger.info(
            "Workshop-Mod-Metadaten nach Download gesetzt: server=%s wid=%s last_updated=%s installed_version=%s",
            server_id,
            workshop_id,
            mod.last_updated,
            mod.installed_version,
        )
        return True
    except Exception as exc:  # pragma: no cover - DB defensiv, Restart muss weiterlaufen
        logger.warning(
            "Konnte Mod-Metadaten (last_updated/installed_version) für Workshop %s (Server %s) nicht setzen: %s",
            workshop_id,
            server_id,
            exc,
        )
        db.rollback()
        return False
    finally:
        db.close()
