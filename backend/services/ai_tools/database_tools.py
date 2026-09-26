"""PostgreSQL-Studio für die KI — dieselben Funktionen wie im Panel, kein Zweitweg.

Lesen (`read_database`) ruft genau die Funktionen, die auch die Studio-Reiter
rufen (`services.postgres_studio_service`), mit denselben Rechten wie die
Studio-Routen. Schreiben (`propose_database_change`, siehe
`ai_proposals.database_proposals`) nimmt dieselbe Operation wie die
SQL-Vorschau im Studio und kompiliert sie mit demselben
`postgres_ddl.compile_operation`. Eine zweite Implementierung von Tabellen,
Funktionen, Triggern oder Erweiterungen gibt es für die KI nicht.

Welche Operationen es gibt und wie sie aussehen, liest das Modell aus dem
Schema selbst (`view: "operations"` / `"operation_schema"`): erzeugt aus den
Pydantic-Klassen, also nie veraltet und nicht im Werkzeugkatalog, der keinen
Platz mehr hat.
"""

from __future__ import annotations

import typing
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from models import PostgresDatabase, Server, User
from services import permission_service
from services.ai_action_errors import AiActionValidationError

READ = "server.databases.read"
ADMIN = "server.databases.admin"

#: Was `read_database` zeigen kann und welches Recht es verlangt — dieselben
#: Rechte wie die Studio-Routen (`routers/postgres_studio.py`).
ANSICHTEN: dict[str, str] = {
    "overview": READ,
    "objects": READ,
    "table": READ,
    "function": READ,
    "extensions": READ,
    "roles": READ,
    "grants": READ,
    "health": READ,
    "parameters": ADMIN,
    "sessions": ADMIN,
    "locks": ADMIN,
    "rows": READ,
    "operations": READ,
    "operation_schema": READ,
}


def operation_classes() -> dict[str, type]:
    """`op`-Name → Pydantic-Klasse, abgeleitet aus der Studio-Union."""
    from schemas.postgres_studio import StudioOperation

    union = typing.get_args(StudioOperation)[0]
    klassen: dict[str, type] = {}
    for klasse in typing.get_args(union):
        # Eine Klasse kann mehrere Namen tragen (`GrantOp`: grant und revoke).
        for name in typing.get_args(klasse.model_fields["op"].annotation):
            klassen[name] = klasse
    return klassen


def validate_operation(raw: object) -> Any:
    """Prüft eine Operation wie die Studio-Route — mit einer Meldung, aus der
    das Modell sich selbst korrigieren kann."""
    from pydantic import ValidationError

    from schemas.postgres_studio import StudioOperation

    if not isinstance(raw, dict) or not raw.get("op"):
        raise AiActionValidationError(
            "operation braucht ein Objekt mit 'op'. Liste: read_database view=operations."
        )
    if raw["op"] not in operation_classes():
        raise AiActionValidationError(
            f"Unbekannte Operation '{raw['op']}'. Liste: read_database view=operations."
        )
    try:
        return TypeAdapter(StudioOperation).validate_python(raw)
    except ValidationError as exc:
        fehler = "; ".join(
            f"{'.'.join(str(teil) for teil in f['loc'][1:])}: {f['msg']}" for f in exc.errors()[:5]
        )
        raise AiActionValidationError(
            f"Operation '{raw['op']}' ungültig: {fehler}. Schema: read_database view=operation_schema name={raw['op']}."
        ) from exc


def resolve_database(db: Session, server: Server, name: object) -> PostgresDatabase:
    """Die Datenbank des Servers per Name; bei genau einer darf er fehlen."""
    rows = (
        db.query(PostgresDatabase)
        .filter(PostgresDatabase.server_id == server.id)
        .order_by(PostgresDatabase.id)
        .all()
    )
    if not rows:
        raise AiActionValidationError("Dieser Server hat keine PostgreSQL-Datenbank.")
    if name in (None, ""):
        if len(rows) == 1:
            return rows[0]
        raise AiActionValidationError(
            "Mehrere Datenbanken — nenne 'database': " + ", ".join(r.name for r in rows)
        )
    for row in rows:
        if row.name == name:
            return row
    raise AiActionValidationError(
        f"Datenbank '{name}' gibt es auf diesem Server nicht: " + ", ".join(r.name for r in rows)
    )


def _require(db: Session, user: User, server: Server, key: str) -> None:
    if not permission_service.has_server_permission(db, user, server.id, key):
        raise AiActionValidationError(f"Dafür fehlt das Recht {key} auf diesem Server.")


def _rows_request(schema: str, arguments: dict) -> Any:
    from pydantic import ValidationError

    from schemas.postgres_studio import StudioRowsRequest

    if not arguments.get("name"):
        raise AiActionValidationError("view=rows braucht name (Tabelle).")
    try:
        return StudioRowsRequest(
            schema_name=schema,
            table=str(arguments["name"]),
            filters=arguments.get("filters") or [],
            sort=arguments.get("sort") or [],
            limit=arguments.get("limit") or 50,
            offset=arguments.get("offset") or 0,
        )
    except ValidationError as exc:
        raise AiActionValidationError(f"Ungültige Zeilenabfrage: {exc.errors()[0].get('msg', '')}") from exc


def read_database(db: Session, *, user: User, server: Server, arguments: dict) -> dict:
    from services import postgres_studio_service as studio

    erlaubt = {"database", "view", "schema", "name", "oid", "filters", "sort", "limit", "offset"}
    fremd = set(arguments) - erlaubt
    if fremd:
        raise AiActionValidationError(f"Unbekannte Argumente: {', '.join(sorted(fremd))}")
    view = arguments.get("view") or "overview"
    if view not in ANSICHTEN:
        raise AiActionValidationError(f"view muss eines sein von: {', '.join(ANSICHTEN)}")
    _require(db, user, server, ANSICHTEN[view])

    if view == "operations":
        # Je Operation ihre Pflichtfelder — das volle Schema kostet je
        # Operation ein paar hundert Zeichen und kommt einzeln.
        return {
            "operations": {
                name: [feld for feld, info in klasse.model_fields.items() if info.is_required() and feld != "op"]
                for name, klasse in operation_classes().items()
            },
            "hint": "Pflichtfelder je op. Alle Felder: view=operation_schema name=<op>.",
        }
    if view == "operation_schema":
        klasse = operation_classes().get(str(arguments.get("name") or ""))
        if klasse is None:
            raise AiActionValidationError("name muss eine Operation aus view=operations sein.")
        return {"operation": arguments["name"], "schema": klasse.model_json_schema()}

    database = resolve_database(db, server, arguments.get("database"))
    k = studio.kontext(db, server.id, database.id)
    schema = str(arguments.get("schema") or "public")
    try:
        if view == "overview":
            daten = studio.overview(db, k)
        elif view == "objects":
            daten = studio.schema_objects(db, k, schema)
        elif view == "table":
            if not arguments.get("name"):
                raise AiActionValidationError("view=table braucht name (Tabelle).")
            daten = studio.table_details(db, k, schema, str(arguments["name"]))
        elif view == "function":
            oid = arguments.get("oid")
            if not isinstance(oid, int) or isinstance(oid, bool):
                raise AiActionValidationError("view=function braucht oid (aus view=objects).")
            daten = studio.function_definition(db, k, oid)
        elif view == "extensions":
            daten = studio.extensions(db, k)
        elif view == "roles":
            daten = studio.roles(db, k)
        elif view == "grants":
            daten = studio.schema_grants(db, k, schema)
        elif view == "health":
            daten = studio.health(db, k)
        elif view == "parameters":
            daten = studio.parameters(db, k)
        elif view == "sessions":
            daten = studio.sessions(db, k)
        elif view == "locks":
            daten = studio.locks(db, k)
        else:
            daten = studio.rows(db, k, _rows_request(schema, arguments))
    except AiActionValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 — Studiofehler gehen als Text ans Modell
        raise AiActionValidationError(f"Datenbank nicht lesbar: {exc}") from exc
    return {
        "server_id": server.id,
        "database": database.name,
        "kind": "dedicated" if k.dedicated else "shared",
        "view": view,
        "data": daten,
    }
