"""Zentraler Status fuer Workshop-Mod-Installationen.

Die Statusdaten liegen in der DB, damit der Mod-Manager nach Reloads weiter
anzeigen kann, welche Mods warten, aktiv laden oder fertig sind.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from database import SessionLocal
from models import Mod

INSTALL_PENDING = "pending"
INSTALL_RUNNING = "installing"
INSTALL_DONE = "installed"
INSTALL_ERROR = "error"

UPDATE_MISSING = "missing"
UPDATE_OUTDATED = "outdated"
UPDATE_UP_TO_DATE = "up_to_date"
UPDATE_UNKNOWN = "unknown"
UPDATE_FAILED = "failed"

_PROGRESS_RE = re.compile(r"progress:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_BYTES_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)")
_BRACKET_PERCENT_RE = re.compile(r"\[\s*(\d+)%\]")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_percent(value: float) -> int:
    return max(0, min(99, int(round(value))))


def parse_steamcmd_progress(line: str) -> tuple[int | None, int | None, int | None]:
    """Extrahiert Prozent und optionale Byte-Werte aus SteamCMD-Progress-Zeilen."""
    progress: int | None = None
    current_bytes: int | None = None
    total_bytes: int | None = None

    progress_match = _PROGRESS_RE.search(line)
    if progress_match:
        try:
            progress = _clamp_percent(float(progress_match.group(1)))
        except ValueError:
            progress = None

    if progress is None:
        bracket_match = _BRACKET_PERCENT_RE.search(line)
        if bracket_match:
            try:
                progress = _clamp_percent(float(bracket_match.group(1)))
            except ValueError:
                progress = None

    bytes_match = _BYTES_RE.search(line)
    if bytes_match:
        try:
            current_bytes = int(bytes_match.group(1))
            total_bytes = int(bytes_match.group(2))
        except ValueError:
            current_bytes = None
            total_bytes = None

    return progress, current_bytes, total_bytes


def mark_mod_pending(server_id: int, workshop_id: str, action: str = "install") -> None:
    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None:
            return
        mod.install_status = INSTALL_PENDING
        mod.install_action = action
        mod.install_progress = 0
        mod.install_eta_seconds = None
        mod.install_started_at = None
        mod.install_completed_at = None
        mod.install_error = None
        db.commit()
    finally:
        db.close()


def mark_mod_update_status(
    server_id: int,
    workshop_id: str,
    status: str,
    reason: str | None = None,
) -> None:
    """Persistiert die Update-Lage separat vom Installationsfortschritt."""
    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None:
            return
        mod.update_status = status
        mod.update_reason = reason
        mod.update_checked_at = _now()
        db.commit()
    finally:
        db.close()


def mark_mod_installing(server_id: int, workshop_id: str, action: str = "install") -> None:
    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None:
            return
        mod.install_status = INSTALL_RUNNING
        mod.install_action = action
        mod.install_progress = mod.install_progress or 0
        mod.install_eta_seconds = None
        mod.install_started_at = _now()
        mod.install_completed_at = None
        mod.install_error = None
        db.commit()
    finally:
        db.close()


def record_mod_download_output(server_id: int, workshop_id: str, line: str) -> None:
    progress, current_bytes, total_bytes = parse_steamcmd_progress(line)
    if progress is None:
        return

    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None or mod.install_status != INSTALL_RUNNING:
            return

        now = _now()
        started_at = mod.install_started_at or now
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        old_progress = mod.install_progress or 0
        mod.install_progress = max(old_progress, progress)
        mod.install_eta_seconds = _estimate_eta_seconds(
            now=now,
            started_at=started_at,
            progress=mod.install_progress,
            current_bytes=current_bytes,
            total_bytes=total_bytes,
        )
        db.commit()
    finally:
        db.close()


def mark_mod_installed(server_id: int, workshop_id: str) -> None:
    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None:
            return
        mod.install_status = INSTALL_DONE
        mod.install_action = None
        mod.install_progress = 100
        mod.install_eta_seconds = 0
        mod.install_completed_at = _now()
        mod.install_error = None
        mod.update_status = UPDATE_UP_TO_DATE
        mod.update_reason = None
        mod.update_checked_at = _now()
        db.commit()
    finally:
        db.close()


def mark_mod_failed(server_id: int, workshop_id: str, error: str | None = None) -> None:
    db = SessionLocal()
    try:
        mod = _get_mod(db, server_id, workshop_id)
        if mod is None:
            return
        mod.install_status = INSTALL_ERROR
        mod.install_eta_seconds = None
        mod.install_completed_at = _now()
        # Sanitize: error kann aus SteamCMD/Docker stammen und Newlines oder
        # Steuerzeichen enthalten. Werden in der UI als Text gerendert (nicht
        # als HTML), aber Newlines fuehren zu kaputten Zeilenumbruechen im
        # Mod-Status-Bereich. Whitespace wird normalisiert, 500-Zeichen-Limit
        # bleibt.
        raw = (error or "Installation fehlgeschlagen").replace("\r", "").replace("\n", " ")
        mod.install_error = " ".join(raw.split())[:500]
        mod.update_status = UPDATE_FAILED
        mod.update_reason = "install_failed"
        mod.update_checked_at = _now()
        db.commit()
    finally:
        db.close()


def _estimate_eta_seconds(
    *,
    now: datetime,
    started_at: datetime,
    progress: int,
    current_bytes: int | None,
    total_bytes: int | None,
) -> int | None:
    elapsed = max(1.0, (now - started_at).total_seconds())

    if current_bytes and total_bytes and total_bytes > current_bytes:
        return max(1, int(round(elapsed * (total_bytes - current_bytes) / current_bytes)))

    if progress <= 0 or progress >= 100:
        return None

    return max(1, int(round(elapsed * (100 - progress) / progress)))


def _get_mod(db, server_id: int, workshop_id: str) -> Mod | None:
    return (
        db.query(Mod)
        .filter(Mod.server_id == server_id, Mod.workshop_id == str(workshop_id))
        .first()
    )
