"""Setup-Endpoints fuer Backup-Cloud-Redesign (Schritt 10).

Diese Endpoints gehoeren zum Cloud-Restore- und Auto-Migrations-Workflow
(Plan 3.7 + 3.10). Sie werden vom Frontend nach dem Login aufgerufen,
wenn entweder:
  - Cloud-Storage Backups enthaelt (Fresh-Install-Restore-Banner)
  - Eine Auto-Migration laeuft oder abgeschlossen werden kann
  - Der User die pending-Restore-Liste verwerfen will

Endpoints (alle auth-required, panel.settings.* permissions):
  GET  /api/setup/pending-restores        - listet orphan Cloud-Backups
  POST /api/setup/pending-restores/discard - setzt Pending-Flag auf 0
  POST /api/setup/restore-orphan/{idx}     - startet Restore fuer Eintrag
  GET  /api/setup/migration-status         - Live-Status der Migration
  POST /api/setup/migration-cancel         - bricht laufende Migration ab

Sicherheit:
- Cloud-Credentials NIEMALS in Responses (nur Metadaten, keine Tokens)
- Provider-Fehlertexte werden sanitized (kein Pfad-Leak, kein Token-Leak)
- Restore-Operation ist server-scoped: User braucht panel.settings.read
  fuer GETs, panel.settings.write fuer mutierende Operationen.
  Server.create wird NICHT separat geprueft, weil restore-orphan
  ein Recovery-Use-Case ist (Backup-gehoerte-vorher-dem-User) — der
  Owner ist die einzige Person mit Restore-Recht.

Hintergrund-Tasks:
- restore-orphan kickt einen asyncio.to_thread-Task (blockt nicht).
- Live-Progress via set_active_backup_status() (Schritt 7).
"""
import asyncio
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal, get_db
from dependencies import require_global, verify_csrf
from services.backup_migration_service import (
    MigrationStatus,
    get_migration_service,
)
from services.backup_provider import (
    BackupMetadata,
    probe_cloud_backups,
)
from services.port_allocation_service import (
    PortConflictError,
    allocate_ports,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])


# ── Response-Schemas ────────────────────────────────────────────────────


class PendingRestoreItem(BaseModel):
    """Ein einzelner Eintrag in der pending-Restore-Liste."""

    remote_key: str
    server_id: int  # Original-Server-ID aus Metadata
    server_name: str
    game_type: str
    created_at: str
    panel_version: str
    cpu_limit_percent: int | None
    ram_limit_mb: int | None
    disk_limit_gb: int | None
    size_mb: int | None
    ports: list[dict]


class PendingRestoresResponse(BaseModel):
    """Response von GET /pending-restores."""

    pending: bool  # True wenn Cloud-Storage Backups hat
    items: list[PendingRestoreItem]
    error: str | None = None  # sanitized, kein Token/Pfad
    # Welcher Provider fuer die Liste verantwortlich war (fuer UI-Label)
    provider: str


class RestoreOrphanAccepted(BaseModel):
    """Response von POST /restore-orphan/{idx} (202 Accepted)."""

    server_id: int
    backup_id: int
    server_name: str
    status: str = "creating"
    message: str = "Restore gestartet, Live-Status unter /api/backups/{server_id}/status"


class MigrationStatusResponse(BaseModel):
    """Response von GET /migration-status."""

    status: str  # idle | running | completed | failed | cancelled
    total: int
    migrated: int
    failed: int
    current_server_id: int | None
    current_filename: str | None
    started_at: str | None
    finished_at: str | None
    last_error: str | None
    target_provider: str


class SimpleOkResponse(BaseModel):
    """Generic OK response."""

    ok: bool
    message: str


# ── Service-Helper ──────────────────────────────────────────────────────


ENV_PATH = Path(os.getenv("MSM_ENV_PATH", "/opt/msm/backend/.env"))


def _read_env_flag(flag: str) -> str:
    """Liest einen einzelnen MSM_*-Flag-Wert aus .env. Sanitized.

    Wir lesen direkt aus der Datei (nicht via os.environ), weil install.sh
    die .env schreibt BEVOR der Backend-Prozess startet, und Settings in
    Python bei Prozessstart geladen werden. Ein spaeterer Patch der .env
    kommt nicht automatisch im settings-Objekt an.
    """
    if not ENV_PATH.exists():
        return ""
    try:
        content = ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"^{re.escape(flag)}=(.*)$", content, re.MULTILINE)
    if not match:
        return ""
    val = match.group(1).strip().strip('"').strip("'")
    return val


def _set_env_flag(flag: str, value: str) -> None:
    """Setzt einen MSM_*-Flag in .env (in-place Edit, atomar via tmp+rename).

    Beibehalten des Rest-Files (andere Variablen unberuehrt). Wenn der Flag
    nicht existiert, wird er angehaengt. Wenn er existiert, wird nur
    die Zeile ersetzt.

    Sanitization: ``value`` MUSS ein vertrauenswuerdiger String sein
    (literal "0", "1", oder vom Code kontrolliert). NIE User-Input
    ungeprueft hier reinpipen — das wuerde Config-Injection ermoeglichen.
    """
    # Sanitize: nur alphanumerisch + _ - .
    if not re.match(r"^[A-Za-z0-9_.-]+$", value):
        raise ValueError(f"Unsafe env-flag value: {value!r}")
    if not re.match(r"^MSM_[A-Z0-9_]+$", flag):
        raise ValueError(f"Unsafe env-flag name: {flag!r}")

    try:
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        if ENV_PATH.exists():
            content = ENV_PATH.read_text(encoding="utf-8")
        else:
            content = ""
        pattern = rf"^{re.escape(flag)}=.*$"
        new_line = f"{flag}={value}"
        if re.search(pattern, content, re.MULTILINE):
            new_content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
        else:
            # Hinten anhaengen, mit Newline-Separator
            sep = "\n" if content and not content.endswith("\n") else ""
            new_content = content + sep + new_line + "\n"

        tmp = ENV_PATH.with_suffix(".env.tmp")
        tmp.write_text(new_content, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, ENV_PATH)
    except OSError as e:
        logger.warning(
            "Konnte %s in .env nicht setzen (%s)", flag, type(e).__name__
        )


def _list_pending_restores() -> tuple[bool, list[BackupMetadata], str | None]:
    """Listet Cloud-Backup-Metadaten. Sanitized Errors.

    Returns:
        (has_backups, items, error_message)
    """
    provider_name = (settings.backup_provider or "local").lower()
    if provider_name == "local":
        # Lokaler Provider kann keine "orphans" haben — wir geben False zurueck
        return False, [], None

    try:
        # probe_cloud_backups nutzt schon list_metadata() mit sanitized Errors
        items = probe_cloud_backups()
        return (bool(items), items, None)
    except Exception as e:  # noqa: BLE001
        # Letzte Verteidigungslinie: nichts an Caller propagieren was Tokens
        # enthalten koennte. probe_cloud_backups schluckt bereits
        # ProviderError intern und returnt []; dieser except faengt
        # nur unerwartete Fehler.
        return False, [], f"Provider-Fehler: {type(e).__name__}"


def _metadata_to_response(meta: BackupMetadata) -> PendingRestoreItem:
    """BackupMetadata -> PendingRestoreItem (sanitized, kein Path-Leak)."""
    return PendingRestoreItem(
        remote_key=meta.remote_key or "",
        server_id=meta.server_id,
        server_name=meta.server_name or "",
        game_type=meta.game_type or "",
        created_at=meta.created_at or "",
        panel_version=meta.panel_version or "",
        cpu_limit_percent=meta.cpu_limit_percent,
        ram_limit_mb=meta.ram_limit_mb,
        disk_limit_gb=meta.disk_limit_gb,
        size_mb=meta.size_mb,
        ports=list(meta.ports or []),
    )


def _create_server_and_backup(
    db: Session,
    meta: BackupMetadata,
) -> tuple[int, int]:
    """Legt Server- und Backup-Row aus Cloud-Metadata an.

    Returns:
        (server_id, backup_id)

    Server:
    - name: aus metadata
    - game_type: aus metadata
    - install_dir: /opt/msm/servers/<server_id>  (auto-dir, container_name-prefixed)
    - status: "creating" (Restore laeuft, Frontend pollt /api/backups/status)
    - status_message: "Restore aus Cloud-Backup laeuft..."
    - cpu/ram/disk: aus metadata (limits, public_bind_ip IGNORIERT)
    - ports: werden in _apply_ports_from_metadata zugewiesen

    Backup:
    - provider: settings.backup_provider
    - remote_key: metadata.remote_key
    - filename: remote_key (NOT NULL constraint, mirror)
    - metadata_json: snapshot fuer spaetere restore_backups

    Hinweis: Server und Backup werden VOR dem eigentlichen Download angelegt,
    damit der UI-Status sofort sichtbar ist. Bei Download-Fehler wird der
    Server-Status auf "error" gesetzt (cleanup im Background-Task).
    """
    from models import Backup, Server

    # Server install_dir generieren (eindeutig per ID)
    # Wir nutzen den Namen + timestamp, NICHT den Namen pur (sonst koennte
    # ein Path-Traversal-Versuch im Namen Probleme machen).
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", (meta.server_name or "server").lower())
    install_dir = f"/opt/msm/servers/{safe_name}_{int(datetime.now(timezone.utc).timestamp())}"

    server = Server(
        name=meta.server_name or f"restored-{meta.server_id}",
        game_type=meta.game_type or "unknown",
        install_dir=install_dir,
        status="creating",
        status_message="Restore aus Cloud-Backup wird vorbereitet",
        container_name=f"msm-srv-restored-{int(datetime.now(timezone.utc).timestamp())}",
        cpu_limit_percent=meta.cpu_limit_percent,
        ram_limit_mb=meta.ram_limit_mb,
        disk_limit_gb=meta.disk_limit_gb,
        # public_bind_ip wird IGNORIERT (passt auf neuem Host moeglicherweise nicht)
        public_bind_ip=None,
        backup_retention_count=5,
    )
    db.add(server)
    db.flush()  # server.id verfuegbar

    # Backup-Row anlegen
    target_provider = (settings.backup_provider or "local").lower()
    # filename ist NOT NULL (mirror von remote_key, analog zu backup_service.run_backup).
    # Falls remote_key None ist (sehr alte Metadata), generieren wir einen
    # Platzhalter, damit der Insert nicht scheitert. Provider-Calls nutzen
    # ohnehin remote_key, nicht filename.
    backup_filename = meta.remote_key or f"{meta.server_id}/unknown.tar.gz"
    backup = Backup(
        server_id=server.id,
        filename=backup_filename,  # NOT NULL constraint, mirror
        size_mb=meta.size_mb,
        provider=target_provider,
        remote_key=meta.remote_key,
        metadata_json=meta.to_json() if hasattr(meta, "to_json") else None,
    )
    db.add(backup)
    db.commit()
    db.refresh(server)
    db.refresh(backup)

    return server.id, backup.id


def _apply_ports_from_metadata(db: Session, server_id: int, meta: BackupMetadata) -> None:
    """Weist Ports aus Metadata zu (Rollen aus metadata, Nummern frisch)."""
    from models import ServerPort, Server

    if not meta.ports:
        return

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        return

    port_roles = []
    protocols = {}
    for p in meta.ports:
        role = p.get("role")
        if role and role not in port_roles:
            port_roles.append(role)
            protocols[role] = p.get("protocol", "udp")

    if not port_roles:
        return

    # Versuche den Game-Port aus Metadata zu bevorzugen (falls noch frei)
    requested_game = None
    for p in meta.ports:
        if p.get("role") == "game" and p.get("port"):
            requested_game = p["port"]
            break

    try:
        allocated = allocate_ports(db, requested_game_port=requested_game)
        allocated_map = {
            "game": allocated[0] if len(allocated) > 0 else None,
            "query": allocated[1] if len(allocated) > 1 else None,
            "rcon": allocated[2] if len(allocated) > 2 else None,
        }
        for role in port_roles:
            new_port = allocated_map.get(role)
            if new_port is None:
                continue
            # Existierende ServerPort-Row updaten oder neu anlegen
            existing = next(
                (p for p in server.ports if p.role == role), None
            )
            if existing:
                existing.port = new_port
            else:
                server.ports.append(
                    ServerPort(
                        server_id=server.id,
                        role=role,
                        port=new_port,
                        protocol=protocols.get(role, "udp"),
                    )
                )
        db.commit()
    except PortConflictError as e:
        # Port-Allokation gescheitert — kein Hard-Fail, Server laeuft ohne
        # zugewiesene Ports. User kann sie manuell nachpflegen.
        logger.warning(
            "Port-Reallocation fehlgeschlagen fuer restored server %s: %s",
            server_id,
            e,
        )


def _do_restore_in_background(server_id: int, backup_id: int) -> None:
    """Background-Task: provider.download + decrypt + extract.

    Laeuft in eigenem DB-Session (die HTTP-Request-Session ist da schon zu).
    Bei Fehlern wird der Server-Status auf "error" gesetzt (sanitized).
    Idempotenz: nicht noetig (jeder Restore ist ein neuer Server+Backup).
    """
    from services.backup_service import restore_backup

    task_db = SessionLocal()
    try:
        # restore_backup() setzt _active_backups, restored Ports, etc.
        restore_backup(server_id, backup_id, task_db)
        # Server-Status auf "stopped" (User drueckt manuell Start)
        from models import Server

        server = task_db.query(Server).filter(Server.id == server_id).first()
        if server:
            server.status = "stopped"
            server.status_message = None
            task_db.commit()
    except FileNotFoundError as e:
        from models import Server

        server = task_db.query(Server).filter(Server.id == server_id).first()
        if server:
            server.status = "error"
            server.status_message = "Wiederherstellung fehlgeschlagen: Quelldatei fehlt"
            task_db.commit()
        logger.warning(
            "Restore fehlgeschlagen (FileNotFound) fuer server %s: %s",
            server_id,
            type(e).__name__,
        )
    except Exception as e:  # noqa: BLE001
        from models import Server

        server = task_db.query(Server).filter(Server.id == server_id).first()
        if server:
            server.status = "error"
            # Sanitized: nur Typ-Name, keine Exception-Message (Token-Leak-Schutz)
            server.status_message = f"Wiederherstellung fehlgeschlagen: {type(e).__name__}"
            task_db.commit()
        logger.warning(
            "Restore fehlgeschlagen fuer server %s: %s",
            server_id,
            type(e).__name__,
        )
    finally:
        task_db.close()


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/pending-restores", response_model=PendingRestoresResponse)
def get_pending_restores(
    db: Session = Depends(get_db),
    _user=Depends(require_global("panel.settings.read")),
) -> PendingRestoresResponse:
    """Listet orphan Cloud-Backups fuer das CloudRestoreBanner.

    Sicherheit:
    - Liest NUR list_metadata() (kein Download).
    - Error-Texte werden sanitized (kein Token, kein Pfad).
    - Permissions: panel.settings.read (Owner + Operator).

    Performance:
    - Probe kann bei grossem Bucket (>1000 Backups) ein paar Sekunden
      dauern. Bei Timeout (Provider-spezifisch) wird ein leerer Response
      mit error-Message zurueckgegeben — das Frontend zeigt dann keinen
      Banner und der User merkt nichts.
    """
    has_backups, items, error = _list_pending_restores()
    provider_name = (settings.backup_provider or "local").lower()
    return PendingRestoresResponse(
        pending=has_backups,
        items=[_metadata_to_response(m) for m in items],
        error=error,
        provider=provider_name,
    )


@router.post(
    "/pending-restores/discard",
    response_model=SimpleOkResponse,
    status_code=200,
)
def discard_pending_restores(
    _user=Depends(require_global("panel.settings.write")),
    _csrf=Depends(verify_csrf),
) -> SimpleOkResponse:
    """Verwirft die pending-Restore-Liste (kein Cloud-Delete!).

    Setzt ``MSM_PENDING_CLOUD_RESTORE=0`` in der .env. Cloud-Backups
    bleiben unangetastet — User kann sie jederzeit manuell oder durch
    Re-Aktivierung des Banners wieder herstellen.

    Sicherheit: nur ``panel.settings.write`` (Owner). CSRF-required.
    """
    _set_env_flag("MSM_PENDING_CLOUD_RESTORE", "0")
    return SimpleOkResponse(
        ok=True,
        message="Pending-Restore-Liste verworfen. Cloud-Backups bleiben erhalten.",
    )


@router.post(
    "/restore-orphan/{idx}",
    response_model=RestoreOrphanAccepted,
    status_code=202,
)
def restore_orphan(
    idx: int,
    _user=Depends(require_global("panel.settings.write")),
    _csrf=Depends(verify_csrf),
) -> RestoreOrphanAccepted:
    """Startet Restore fuer pending-Restore-Eintrag ``idx``.

    Flow:
    1. Liste erneut frisch vom Provider holen (Konsistenz mit dem was der
       User im Banner sieht).
    2. Server- + Backup-Row anlegen aus Metadata.
    3. Ports zuweisen (Rollen aus metadata, Nummern via port_allocation).
    4. Background-Task kicken: provider.download + decrypt + extract.
    5. 202 Accepted + server_id zurueckgeben (Frontend navigiert).

    Edge cases:
    - idx out of range -> 404
    - Server-Erstellung schlaegt fehl -> 500 (sanitized)
    - Background-Task schlaegt fehl -> Server-Status "error" mit sanitized
      status_message, kein Server- oder Backup-Delete (User kann in der
      Server-Liste entscheiden was er tut).
    """
    if idx < 0:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")

    has_backups, items, error = _list_pending_restores()
    if error:
        raise HTTPException(
            status_code=503, detail=f"Provider-Liste fehlgeschlagen: {error}"
        )
    if not has_backups or idx >= len(items):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")

    meta = items[idx]

    # Server- + Backup-Row anlegen
    db = SessionLocal()
    try:
        server_id, backup_id = _create_server_and_backup(db, meta)

        # Ports zuweisen (Rollen aus metadata, Nummern frisch)
        _apply_ports_from_metadata(db, server_id, meta)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Server-Erstellung fehlgeschlagen: {type(e).__name__}",
        )
    finally:
        db.close()

    # Background-Task: provider.download + decrypt + extract
    # asyncio.to_thread schiebt in Default-Thread-Pool, loop laeuft weiter.
    # Restore ist Multi-GB -> kann Minuten dauern.
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            asyncio.to_thread(_do_restore_in_background, server_id, backup_id)
        )
    except RuntimeError:
        # Kein laufender Loop (z.B. in Tests): synchron ausfuehren
        # (wird dort vom Test gemockt / ueberprueft)
        threading.Thread(
            target=_do_restore_in_background,
            args=(server_id, backup_id),
            daemon=True,
        ).start()

    return RestoreOrphanAccepted(
        server_id=server_id,
        backup_id=backup_id,
        server_name=meta.server_name or f"restored-{meta.server_id}",
    )


@router.get("/migration-status", response_model=MigrationStatusResponse)
def get_migration_status(
    _user=Depends(require_global("panel.settings.read")),
) -> MigrationStatusResponse:
    """Live-Status der Auto-Migration (Schritt 9.2 + 9.4).

    Wird vom Frontend (CloudMigrationBanner, Schritt 12) regelmaessig
    gepollt. Wenn status=running, wird die UI Live-Progress zeigen.
    """
    svc = get_migration_service()
    progress = svc.progress()
    return MigrationStatusResponse(
        status=progress.status,
        total=progress.total,
        migrated=progress.migrated,
        failed=progress.failed,
        current_server_id=progress.current_server_id,
        current_filename=progress.current_filename,
        started_at=progress.started_at,
        finished_at=progress.finished_at,
        last_error=progress.last_error,
        target_provider=(settings.backup_provider or "local").lower(),
    )


@router.post(
    "/migration-cancel",
    response_model=SimpleOkResponse,
    status_code=200,
)
def cancel_migration(
    _user=Depends(require_global("panel.settings.write")),
    _csrf=Depends(verify_csrf),
) -> SimpleOkResponse:
    """Bricht eine laufende Auto-Migration ab.

    Idempotent: wenn keine Migration laeuft, ist der Call ein No-Op.
    Nach Cancel: state.cloud_migration_done bleibt FALSE, naechster
    Startup bietet die Migration wieder an (idempotent ueber DB).
    """
    svc = get_migration_service()
    svc.cancel()
    return SimpleOkResponse(
        ok=True,
        message="Cancel-Signal gesendet. Laufende Backups werden noch fertig gestellt.",
    )
