"""PostgreSQL-Studio — je Datenbank eines Servers und für die Panel-Datenbank.

Die Endpunkte stehen einmal (`_studio_routes`) und werden für zwei Ziele
gebaut. Was sich unterscheidet, trägt der `Zugriff`: wo die Datenbank liegt
(`postgres_studio_service.Kontext`) und welches Recht eine Stufe verlangt.

- **Server** (``/api/servers/{id}/databases/{db}/studio``): lesen
  ``server.databases.read``, Daten ``write``, Struktur, SQL, Sitzungen und
  Parameter ``admin``.
- **Panel** (``/api/panel/database/studio``): lesen ``panel.database.read``,
  alles Schreibende ``panel.database.admin``. Die Struktur gehört den
  Migrationen (`postgres_studio_service.PANEL_OPS`), Sicherung macht
  „Panel-Backups“.

Welches Recht eine Strukturänderung braucht, sagt ihr Plan
(``services.postgres_ddl``) — Vorschau und Ausführung prüfen dasselbe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models import User
from routers.databases import _audit_db, _service_error
from schemas.postgres_studio import (
    StudioDumpRequest,
    StudioExecuteResponse,
    StudioExplainRequest,
    StudioExportRequest,
    StudioImportRequest,
    StudioOperationRequest,
    StudioPlanResponse,
    StudioRestoreRequest,
    StudioRowDeleteRequest,
    StudioRowInsertRequest,
    StudioRowsRequest,
    StudioRowUpdateRequest,
    StudioSessionActionRequest,
    StudioSqlRequest,
    StudioSqlResponse,
)
from services import audit_service
from services import postgres_studio_service as studio
from services.permission_service import has_global_permission, has_server_permission

SERVER_RECHTE = {
    "read": "server.databases.read",
    "write": "server.databases.write",
    "admin": "server.databases.admin",
}
PANEL_RECHTE = {
    "read": "panel.database.read",
    "write": "panel.database.admin",
    "admin": "panel.database.admin",
}


@dataclass
class Zugriff:
    db: Session
    user: User
    k: studio.Kontext
    darf: Callable[[str], bool]
    pruefe: Callable[[str], None]
    audit: Callable[[str, dict[str, Any]], None]
    # Panel: auch Datenänderungen und SQL ins Audit — sie treffen Benutzer,
    # Rechte und Server des Panels selbst.
    daten_ins_audit: bool = False


def _stufe(permission: str) -> str:
    """``server.databases.admin`` → ``admin`` — der Plan spricht Server-Rechte."""
    return permission.rsplit(".", 1)[-1]


def _server_zugang(stufe: str) -> Callable[..., Zugriff]:
    def dep(
        server_id: int,
        database_id: int,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> Zugriff:
        require_server_permission(user, server_id, db, SERVER_RECHTE[stufe])
        try:
            k = studio.kontext(db, server_id, database_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def audit(action: str, details: dict[str, Any]) -> None:
            _audit_db(db, user, action=action, server_id=server_id, details={"database_id": database_id, **details})

        return Zugriff(
            db=db,
            user=user,
            k=k,
            darf=lambda s: has_server_permission(db, user, server_id, SERVER_RECHTE[s]),
            pruefe=lambda s: require_server_permission(user, server_id, db, SERVER_RECHTE[s]),
            audit=audit,
        )

    return dep


def _panel_zugang(stufe: str) -> Callable[..., Zugriff]:
    def dep(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Zugriff:
        def darf(s: str) -> bool:
            return has_global_permission(db, user, PANEL_RECHTE[s])

        def pruefe(s: str) -> None:
            if not darf(s):
                raise HTTPException(status_code=403, detail="Keine Berechtigung")

        pruefe(stufe)
        try:
            k = studio.panel_kontext()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def audit(action: str, details: dict[str, Any]) -> None:
            audit_service.record_privileged_action(
                db, user_id=user.id, action=action, target_type="panel_database", details=details, commit=True
            )

        return Zugriff(db=db, user=user, k=k, darf=darf, pruefe=pruefe, audit=audit, daten_ins_audit=True)

    return dep


def _call(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:
        raise _service_error(exc) from exc


def _studio_routes(router: APIRouter, zugang: Callable[[str], Callable[..., Zugriff]]) -> None:
    lesen = Depends(zugang("read"))
    schreiben = Depends(zugang("write"))
    verwalten = Depends(zugang("admin"))

    def daten_audit(z: Zugriff, action: str, details: dict[str, Any]) -> None:
        if z.daten_ins_audit:
            z.audit(action, details)

    # ── Übersicht und Katalog ──────────────────────────────────────────

    @router.get("")
    def overview(z: Zugriff = lesen):
        result = _call(studio.overview, z.db, z.k)
        result["permissions"] = {"read": True, "write": z.darf("write"), "admin": z.darf("admin")}
        return result

    @router.get("/objects")
    def schema_objects(
        schema_name: str = Query("public", alias="schema", min_length=1, max_length=63), z: Zugriff = lesen
    ):
        return _call(studio.schema_objects, z.db, z.k, schema_name)

    @router.get("/table")
    def table_details(
        schema_name: str = Query("public", alias="schema", min_length=1, max_length=63),
        table: str = Query(..., min_length=1, max_length=63),
        z: Zugriff = lesen,
    ):
        return _call(studio.table_details, z.db, z.k, schema_name, table)

    @router.get("/functions/{oid}")
    def function_definition(oid: int, z: Zugriff = lesen):
        return _call(studio.function_definition, z.db, z.k, oid)

    @router.get("/extensions")
    def extensions(z: Zugriff = lesen):
        return _call(studio.extensions, z.db, z.k)

    @router.get("/roles")
    def roles(z: Zugriff = lesen):
        return _call(studio.roles, z.db, z.k)

    @router.get("/grants")
    def grants(schema_name: str = Query("public", alias="schema", min_length=1, max_length=63), z: Zugriff = lesen):
        return _call(studio.schema_grants, z.db, z.k, schema_name)

    @router.get("/health")
    def health(z: Zugriff = lesen):
        return _call(studio.health, z.db, z.k)

    # ── Instanz und Überwachung (admin) ────────────────────────────────

    @router.get("/parameters")
    def parameters(z: Zugriff = verwalten):
        return _call(studio.parameters, z.db, z.k)

    @router.get("/sessions")
    def sessions(z: Zugriff = verwalten):
        return _call(studio.sessions, z.db, z.k)

    @router.get("/locks")
    def locks(z: Zugriff = verwalten):
        return _call(studio.locks, z.db, z.k)

    @router.post("/sessions/{pid}")
    def session_action(
        pid: int, body: StudioSessionActionRequest, z: Zugriff = verwalten, _: None = Depends(verify_csrf)
    ):
        ok = _call(studio.session_action, z.db, z.k, pid, body.action)
        z.audit(f"postgres.studio.session_{body.action}", {"pid": pid, "ok": ok})
        return {"ok": ok}

    # ── SQL, EXPLAIN, Strukturänderungen ───────────────────────────────

    @router.post("/sql", response_model=StudioSqlResponse)
    def run_sql(body: StudioSqlRequest, z: Zugriff = verwalten, _: None = Depends(verify_csrf)):
        result = _call(studio.run_sql, z.db, z.k, body)
        # Kein SQL im Audit — es kann Passwörter tragen.
        daten_audit(z, "postgres.studio.sql", {"statements": len(result.get("results") or []), "rolled_back": body.rollback})
        return result

    @router.post("/explain")
    def explain(body: StudioExplainRequest, z: Zugriff = verwalten, _: None = Depends(verify_csrf)):
        return _call(studio.explain, z.db, z.k, body)

    def _plan(z: Zugriff, body: StudioOperationRequest):
        plan = _call(studio.plan_for, z.db, z.k, body.operation)
        z.pruefe(_stufe(plan.permission))
        return plan

    @router.post("/preview", response_model=StudioPlanResponse)
    def preview(body: StudioOperationRequest, z: Zugriff = lesen, _: None = Depends(verify_csrf)):
        return studio.plan_response(_plan(z, body))

    @router.post("/execute", response_model=StudioExecuteResponse)
    def execute(body: StudioOperationRequest, z: Zugriff = lesen, _: None = Depends(verify_csrf)):
        plan = _plan(z, body)
        result = _call(studio.execute, z.db, z.k, body.operation)
        # Kein SQL im Audit: ein Rollenpasswort stünde sonst darin.
        z.audit(
            "postgres.studio.execute",
            {
                "operation": body.operation.op,
                "destructive": plan.destructive,
                "scope": plan.scope,
                "statements": len(plan.statements),
            },
        )
        return result

    # ── Daten-Grid ─────────────────────────────────────────────────────

    @router.post("/rows")
    def read_rows(body: StudioRowsRequest, z: Zugriff = lesen):
        return _call(studio.rows, z.db, z.k, body)

    @router.post("/rows/insert")
    def insert_row(body: StudioRowInsertRequest, z: Zugriff = schreiben, _: None = Depends(verify_csrf)):
        result = _call(studio.insert_row, z.db, z.k, body)
        daten_audit(z, "postgres.studio.rows_insert", {"table": f"{body.schema_name}.{body.table}"})
        return result

    @router.post("/rows/update")
    def update_row(body: StudioRowUpdateRequest, z: Zugriff = schreiben, _: None = Depends(verify_csrf)):
        result = _call(studio.update_row, z.db, z.k, body)
        daten_audit(
            z, "postgres.studio.rows_update", {"table": f"{body.schema_name}.{body.table}", "columns": sorted(body.changes)}
        )
        return result

    @router.post("/rows/delete")
    def delete_rows(body: StudioRowDeleteRequest, z: Zugriff = schreiben, _: None = Depends(verify_csrf)):
        result = _call(studio.delete_rows, z.db, z.k, body)
        daten_audit(z, "postgres.studio.rows_delete", {"table": f"{body.schema_name}.{body.table}", "rows": result.get("deleted")})
        return result

    @router.post("/rows/import")
    def import_rows(body: StudioImportRequest, z: Zugriff = schreiben, _: None = Depends(verify_csrf)):
        result = _call(studio.import_rows, z.db, z.k, body)
        daten_audit(z, "postgres.studio.rows_import", {"table": f"{body.schema_name}.{body.table}", "rows": len(body.rows)})
        return result

    @router.post("/rows/export")
    def export_rows(body: StudioExportRequest, z: Zugriff = lesen, _: None = Depends(verify_csrf)) -> Response:
        payload, media_type, truncated = _call(studio.export_rows, z.db, z.k, body)
        extension = {"text/csv": "csv", "application/json": "json", "application/sql": "sql"}[media_type]
        # Header sind latin-1: alles andere wird zu "_".
        filename = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{body.schema_name}.{body.table}") + f".{extension}"
        return Response(
            content=payload,
            media_type=f"{media_type}; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-MSM-Export-Truncated": "1" if truncated else "0",
                "Cache-Control": "no-store",
            },
        )

    # ── Sicherung (admin, nur Server) ──────────────────────────────────

    @router.post("/backup/dump")
    def backup_dump(body: StudioDumpRequest, z: Zugriff = verwalten, _: None = Depends(verify_csrf)) -> Response:
        payload, sha = _call(studio.dump, z.db, z.k, body)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        extension = {"plain": "sql", "custom": "dump", "tar": "tar"}[body.format]
        z.audit("postgres.studio.dump", {"format": body.format, "size_bytes": len(payload), "sha256": sha})
        return Response(
            content=payload,
            media_type="application/sql; charset=utf-8" if body.format == "plain" else "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{z.k.database_name}-{stamp}.{extension}"',
                "X-MSM-Dump-SHA256": sha,
                "X-MSM-Dump-Size": str(len(payload)),
                "Cache-Control": "no-store",
            },
        )

    @router.post("/backup/restore")
    def backup_restore(body: StudioRestoreRequest, z: Zugriff = verwalten, _: None = Depends(verify_csrf)):
        result = _call(studio.restore, z.db, z.k, body)
        z.audit("postgres.studio.restore", {"format": body.format, "sha256": result.get("sha256")})
        return result

    @router.get("/backup/pending")
    def backup_pending(z: Zugriff = verwalten):
        return _call(studio.pending, z.db, z.k)

    @router.post("/backup/pending/apply")
    def backup_pending_apply(z: Zugriff = verwalten, _: None = Depends(verify_csrf)):
        result = _call(studio.apply_pending, z.db, z.k)
        z.audit("postgres.studio.restore_pending", {})
        return result

    @router.post("/backup/pending/discard")
    def backup_pending_discard(z: Zugriff = verwalten, _: None = Depends(verify_csrf)):
        _call(studio.discard_pending, z.db, z.k)
        z.audit("postgres.studio.discard_pending", {})
        return {"ok": True}


router = APIRouter(prefix="/api/servers/{server_id}/databases/{database_id}/studio", tags=["databases"])
_studio_routes(router, _server_zugang)

panel_router = APIRouter(prefix="/api/panel/database/studio", tags=["panel-database"])
_studio_routes(panel_router, _panel_zugang)
