"""`propose_database_change` — was das PostgreSQL-Studio schreibt, als KI-Vorschlag.

Drei Formen, genau eine je Aufruf, jede mit dem Recht der gleichnamigen
Studio-Route (`routers/postgres_studio.py`):

- ``operation`` — Struktur (Tabelle, Index, View, Funktion, Trigger,
  Erweiterung, Rolle, Parameter …). Dieselbe Spezifikation wie im Studio,
  kompiliert von `postgres_ddl.compile_operation`; das Recht kommt aus dem
  Plan (`plan.permission`).
- ``rows`` — Daten-Grid: einfügen, ändern, löschen, importieren
  (``server.databases.write``).
- ``sql`` — freies SQL wie im SQL-Editor (``server.databases.admin``).

Beim Vorschlag wird eine Operation nicht nur kompiliert, sondern geprobt: der
Plan läuft in einer Transaktion, die verworfen wird. So scheitert ein kaputter
Funktionskörper am Vorschlag und nicht nach dem Klick. Freies SQL wird **nicht**
geprobt — es kann außerhalb der Transaktion wirken, und die Zusage ist, dass es
erst nach dem Klick läuft.

Gefragt wird (``always_confirm``), was Daten vernichtet: jeder Plan mit
``destructive``, jedes Löschen von Zeilen und jedes freie SQL. Ein
Rollenpasswort nimmt die KI nicht an — es stünde im Chat.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from models import Server, User
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_proposals.base import _Ausgefuehrt, _AusfuehrungsRahmen

WRITE = "server.databases.write"
ADMIN = "server.databases.admin"

_ROW_REQUESTS = {
    "insert": "StudioRowInsertRequest",
    "update": "StudioRowUpdateRequest",
    "delete": "StudioRowDeleteRequest",
    "import": "StudioImportRequest",
}


def _require(db: Session, user: User, server_id: int, key: str) -> None:
    if not permission_service.has_server_permission(db, user, server_id, key):
        raise AiActionValidationError(f"Dafür fehlt das Recht {key} auf diesem Server.")


def _kein_passwort(raw: dict) -> None:
    attribute = raw.get("attributes")
    if isinstance(attribute, dict) and attribute.get("password"):
        raise AiActionValidationError(
            "Ein Rollenpasswort nimmt die KI nicht an — der Benutzer setzt es im Studio unter Rollen."
        )


def _row_request(raw: object) -> tuple[str, Any]:
    from pydantic import ValidationError

    from schemas import postgres_studio as s

    if not isinstance(raw, dict) or raw.get("action") not in _ROW_REQUESTS:
        raise AiActionValidationError("rows braucht action: insert, update, delete oder import.")
    action = raw["action"]
    felder = {k: v for k, v in raw.items() if k != "action"}
    if "schema" in felder:
        felder["schema_name"] = felder.pop("schema")
    try:
        return action, getattr(s, _ROW_REQUESTS[action])(**felder)
    except (ValidationError, TypeError) as exc:
        text = exc.errors()[0].get("msg", "") if isinstance(exc, ValidationError) else str(exc)
        raise AiActionValidationError(f"rows/{action} ungültig: {text}") from exc


def _sql_request(raw: dict) -> Any:
    from pydantic import ValidationError

    from schemas.postgres_studio import StudioSqlRequest

    try:
        return StudioSqlRequest(
            sql=raw.get("sql") or "",
            rollback=bool(raw.get("rollback")),
            autocommit=bool(raw.get("autocommit")),
            row_limit=100,
        )
    except ValidationError as exc:
        raise AiActionValidationError(f"sql ungültig: {exc.errors()[0].get('msg', '')}") from exc


def _studio_fehler(exc: Exception) -> AiActionValidationError:
    """Der Studio-Fehler als Text fürs Modell — es soll ihn korrigieren können."""
    return AiActionValidationError(f"PostgreSQL lehnt ab: {exc}")


def _database_change_payload(db: Session, user: User, server: Server, arguments: dict) -> tuple[dict, dict]:
    from services import postgres_studio_service as studio
    from services.ai_tools.database_tools import resolve_database, validate_operation

    erlaubt = {"database", "operation", "rows", "sql", "rollback", "autocommit"}
    fremd = set(arguments) - erlaubt
    if fremd:
        raise AiActionValidationError(f"Unbekannte Argumente: {', '.join(sorted(fremd))}")
    formen = [f for f in ("operation", "rows", "sql") if arguments.get(f)]
    if len(formen) != 1:
        raise AiActionValidationError("Genau eines von operation, rows oder sql angeben.")
    form = formen[0]
    database = resolve_database(db, server, arguments.get("database"))
    k = studio.kontext(db, server.id, database.id)
    payload: dict[str, Any] = {"database_id": database.id, "form": form}
    # `operation` ist der Name, den die Karte in Kopf und Frage setzt; `diff`
    # zeigt sie als Codeblock — dort steht, was laufen wird.
    preview: dict[str, Any] = {
        "server_name": server.name,
        "database": database.name,
        "kind": "dedicated" if k.dedicated else "shared",
    }

    if form == "operation":
        raw = arguments["operation"]
        if isinstance(raw, dict):
            _kein_passwort(raw)
        op = validate_operation(raw)
        # Reihenfolge wie an der Studio-Route: erst das Recht aus dem Plan,
        # dann laufen lassen — die Probe führt den Plan wirklich aus.
        try:
            plan = studio.plan_for(db, k, op)
        except Exception as exc:  # noqa: BLE001
            raise _studio_fehler(exc) from exc
        _require(db, user, server.id, plan.permission)
        try:
            probe = studio.execute(db, k, op, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            raise _studio_fehler(exc) from exc
        payload["operation"] = op.model_dump(mode="json")
        preview.update(
            operation=op.op,
            diff=_sql_text(plan.preview()),
            scope=plan.scope,
            probed=probe.get("probed", False),
        )
        if plan.destructive:
            preview["destructive"] = True
            preview["always_confirm"] = True
    elif form == "rows":
        _require(db, user, server.id, WRITE)
        action, req = _row_request(arguments["rows"])
        payload["rows"] = {"action": action, **req.model_dump(mode="json")}
        daten = req.model_dump(mode="json", exclude={"schema_name", "table"})
        if action == "import" and len(req.rows) > 20:
            daten["rows"] = [*req.rows[:20], f"… {len(req.rows) - 20} weitere"]
        preview.update(
            operation=f"rows_{action}",
            table=f"{req.schema_name}.{req.table}",
            diff=json.dumps(daten, ensure_ascii=False, indent=2, default=str),
        )
        if action == "delete":
            preview["destructive"] = True
            preview["always_confirm"] = True
    else:
        _require(db, user, server.id, ADMIN)
        req = _sql_request(arguments)
        payload["sql"] = {"sql": req.sql, "rollback": req.rollback, "autocommit": req.autocommit}
        preview.update(
            operation="sql",
            diff=req.sql if len(req.sql) <= 20_000 else req.sql[:20_000] + "\n…",
            rollback=req.rollback,
            always_confirm=True,
        )
    # Die Vorschau liegt unverschlüsselt in der Vorschlagszeile (die Nutzlast
    # nicht): ein Passwort in einer Zeile oder im SQL gehört nicht hinein.
    preview["diff"] = redact_sensitive_text(preview["diff"])
    return payload, preview


def _sql_text(statements: list[str]) -> str:
    return "\n\n".join(text.rstrip().rstrip(";") + ";" for text in statements)


def _ausfuehren_database_change(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Führt aus, was bestätigt wurde — neu geprüft, nie aus der Vorschau."""
    from routers.databases import _audit_db
    from services import postgres_studio_service as studio
    from services.ai_tools.database_tools import validate_operation

    payload = rahmen.payload
    server_id = int(rahmen.server_id)
    user = rahmen.active_user
    k = studio.kontext(db, server_id, int(payload["database_id"]))
    form = payload.get("form")
    details: dict[str, Any] = {"database_id": k.database.id, "via": "ai"}
    if form == "operation":
        op = validate_operation(payload["operation"])
        plan = studio.plan_for(db, k, op)
        _require(db, user, server_id, plan.permission)
        result = studio.execute(db, k, op)
        action = "postgres.studio.execute"
        details.update(
            operation=op.op, destructive=plan.destructive, scope=plan.scope, statements=len(plan.statements)
        )
    elif form == "rows":
        _require(db, user, server_id, WRITE)
        action_name, req = _row_request(payload["rows"])
        fn = {
            "insert": studio.insert_row,
            "update": studio.update_row,
            "delete": studio.delete_rows,
            "import": studio.import_rows,
        }[action_name]
        result = fn(db, k, req)
        action = f"postgres.studio.rows_{action_name}"
        details.update(table=f"{req.schema_name}.{req.table}")
    elif form == "sql":
        _require(db, user, server_id, ADMIN)
        result = studio.run_sql(db, k, _sql_request(payload["sql"]))
        action = "postgres.studio.sql"
        details.update(rolled_back=bool(result.get("rolled_back")))
    else:
        raise AiActionValidationError("Unbekannte Datenbankänderung")
    # Kein SQL im Audit — wie an der Studio-Route.
    _audit_db(db, user, action=action, server_id=server_id, details=details)
    return _Ausgefuehrt(result={"database": k.database.name, **result})
