"""Übersetzt Studio-Spezifikationen in PostgreSQL-Anweisungen.

Vorschau und Ausführung rufen dieselbe Funktion: was die Vorschau zeigt, läuft.
Einzige Ausnahme ist ein Rollenpasswort — die Vorschau zeigt es maskiert
(``display``), ausgeführt wird ``statements``.

Namen werden immer gequotet, Werte immer als Literal gesetzt. Frei
geschriebene Ausdrücke (DEFAULT, CHECK, USING, WHERE, View-Abfrage,
Funktionskörper) laufen nur mit der Owner-Identität — dieselbe Macht wie der
SQL-Editor, den dasselbe Recht öffnet. Pläne mit Admin-Identität bestehen nur
aus Namen und Literalen.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from schemas import postgres_studio as s

ADMIN_PERMISSION = "server.databases.admin"
WRITE_PERMISSION = "server.databases.write"

_TYPE_RE = re.compile(
    r"""^(?:
        (?:[A-Za-z_][A-Za-z0-9_$]*\.)?[A-Za-z_][A-Za-z0-9_$ ]*
        (?:\(\s*\d+(?:\s*,\s*\d+)?\s*\))?
        (?:\s+with(?:out)?\s+time\s+zone)?
      |
        "[^"\x00]+"(?:\."[^"\x00]+")?
    )(?:\s*\[\s*\d*\s*\])*$""",
    re.VERBOSE | re.IGNORECASE,
)
_PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?$")
# Freitext-Signaturen (Argumentlisten, Rückgabetyp): keine zweite Anweisung,
# kein Kommentar, der den Rest verschluckt.
_SIGNATURE_FORBIDDEN = re.compile(r";|--|/\*|\x00")

# Parameter, die Code ausführen, Dateien lesen/schreiben oder die Anbindung
# des Panels an die Instanz brechen. Bleiben in der Hand des Panels.
BLOCKED_PARAMETERS = frozenset(
    {
        "archive_command",
        "archive_library",
        "archive_cleanup_command",
        "restore_command",
        "recovery_end_command",
        "shared_preload_libraries",
        "local_preload_libraries",
        "session_preload_libraries",
        "dynamic_library_path",
        "jit_provider",
        "hba_file",
        "ident_file",
        "config_file",
        "data_directory",
        "external_pid_file",
        "port",
        "listen_addresses",
        "unix_socket_directories",
        "unix_socket_group",
        "unix_socket_permissions",
        "log_directory",
        "log_filename",
        "krb_server_keyfile",
        "password_encryption",
        "ssl",
        "primary_conninfo",
    }
)
BLOCKED_PARAMETER_PREFIXES = ("ssl_", "log_file_mode")

# Mitgliedschaften in Systemrollen, die das Panel erlaubt. Die übrigen
# (``pg_read_server_files``, ``pg_execute_server_program`` …) öffnen Dateien
# oder Programme im Container.
ALLOWED_SYSTEM_ROLES = frozenset(
    {
        "pg_read_all_data",
        "pg_write_all_data",
        "pg_monitor",
        "pg_read_all_settings",
        "pg_read_all_stats",
        "pg_stat_scan_tables",
        "pg_signal_backend",
        "pg_checkpoint",
        "pg_maintain",
        "pg_use_reserved_connections",
        "pg_create_subscription",
    }
)

PRIVILEGES: dict[str, frozenset[str]] = {
    "table": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN", "ALL"}),
    "sequence": frozenset({"USAGE", "SELECT", "UPDATE", "ALL"}),
    "schema": frozenset({"USAGE", "CREATE", "ALL"}),
    "function": frozenset({"EXECUTE", "ALL"}),
}

_FK_ACTION = {
    "no_action": "NO ACTION",
    "restrict": "RESTRICT",
    "cascade": "CASCADE",
    "set_null": "SET NULL",
    "set_default": "SET DEFAULT",
}


@dataclass
class Plan:
    statements: list[str]
    mode: str = "tx"  # tx | autocommit
    identity: str = "owner"  # owner | admin
    scope: str = "database"  # database | instance (nur eigene Instanz)
    destructive: bool = False
    permission: str = ADMIN_PERMISSION
    display: list[str] = field(default_factory=list)
    # Rollen, die der Plan benennt: der Dienst prüft sie gegen die erlaubten.
    roles: list[str] = field(default_factory=list)
    # Fuer set/reset_parameter: der Name, den der Dienst gegen pg_settings prüft.
    parameter: str | None = None

    def preview(self) -> list[str]:
        return self.display or self.statements


# ── Bausteine ──────────────────────────────────────────────────────────────


def ident(name: str) -> str:
    if not name or "\x00" in name:
        raise ValueError("Ungültiger Name.")
    if len(name.encode("utf-8")) > 63:
        raise ValueError(f"Name ist länger als 63 Byte: {name[:20]}…")
    return '"' + name.replace('"', '""') + '"'


def qualified(schema_name: str | None, name: str) -> str:
    return f"{ident(schema_name)}.{ident(name)}" if schema_name else ident(name)


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if "\x00" in text:
        raise ValueError("NUL-Zeichen sind in PostgreSQL-Werten nicht erlaubt.")
    return "'" + text.replace("'", "''") + "'"


def type_name(value: str) -> str:
    value = value.strip()
    if not _TYPE_RE.match(value):
        raise ValueError(f"Ungültiger Datentyp: {value[:40]}")
    return value


def expression(value: str) -> str:
    value = value.strip()
    if not value or "\x00" in value:
        raise ValueError("Leerer oder ungültiger Ausdruck.")
    return value


def signature(value: str, what: str) -> str:
    value = value.strip()
    if _SIGNATURE_FORBIDDEN.search(value):
        raise ValueError(f"{what}: ';', Kommentare und NUL sind nicht erlaubt.")
    if value.count("(") != value.count(")"):
        raise ValueError(f"{what}: Klammern sind nicht ausgeglichen.")
    return value


def dollar_quote(body: str) -> str:
    tag = "$fn$"
    while tag in body:
        tag = f"$fn_{secrets.token_hex(3)}$"
    return f"{tag}{body}{tag}"


def _cols(names: list[str]) -> str:
    return ", ".join(ident(n) for n in names)


def _constraint_prefix(name: str | None) -> str:
    return f"CONSTRAINT {ident(name)} " if name else ""


def _column_def(col: s.ColumnSpec) -> str:
    parts = [ident(col.name), type_name(col.type)]
    if col.identity and col.default:
        raise ValueError(f"Spalte {col.name}: Identity und Standardwert schließen sich aus.")
    if col.identity:
        parts.append("GENERATED ALWAYS AS IDENTITY" if col.identity == "always" else "GENERATED BY DEFAULT AS IDENTITY")
    if not col.nullable:
        parts.append("NOT NULL")
    if col.default:
        parts.append(f"DEFAULT {expression(col.default)}")
    return " ".join(parts)


def _pk_def(pk: s.PrimaryKeySpec) -> str:
    return f"{_constraint_prefix(pk.name)}PRIMARY KEY ({_cols(pk.columns)})"


def _unique_def(u: s.UniqueSpec) -> str:
    nnd = " NULLS NOT DISTINCT" if u.nulls_not_distinct else ""
    return f"{_constraint_prefix(u.name)}UNIQUE{nnd} ({_cols(u.columns)})"


def _check_def(c: s.CheckSpec) -> str:
    return f"{_constraint_prefix(c.name)}CHECK ({expression(c.expression)})"


def _fk_def(fk: s.ForeignKeySpec) -> str:
    if len(fk.columns) != len(fk.ref_columns):
        raise ValueError("Fremdschlüssel: Spaltenzahl und Zielspalten müssen gleich sein.")
    text = (
        f"{_constraint_prefix(fk.name)}FOREIGN KEY ({_cols(fk.columns)}) "
        f"REFERENCES {qualified(fk.ref_schema, fk.ref_table)} ({_cols(fk.ref_columns)}) "
        f"ON DELETE {_FK_ACTION[fk.on_delete]} ON UPDATE {_FK_ACTION[fk.on_update]}"
    )
    if fk.deferrable:
        text += " DEFERRABLE INITIALLY DEFERRED"
    return text


def _comment(target: str, comment: str | None) -> str:
    return f"COMMENT ON {target} IS {literal(comment) if comment else 'NULL'}"


def _cascade(flag: bool) -> str:
    return " CASCADE" if flag else ""


def _roles(names: list[str]) -> str:
    return ", ".join("PUBLIC" if n.upper() == "PUBLIC" else ident(n) for n in names)


# ── Übersetzung ────────────────────────────────────────────────────────────


def compile_operation(op: Any, *, dedicated: bool) -> Plan:
    handler = _HANDLERS.get(op.op)
    if handler is None:
        raise ValueError(f"Unbekannte Operation: {op.op}")
    plan = handler(op, dedicated)
    for statement in plan.statements:
        if "\x00" in statement:
            raise ValueError("NUL-Zeichen im Ergebnis.")
    return plan


def _create_schema(op: s.CreateSchemaOp, _d: bool) -> Plan:
    return Plan([f"CREATE SCHEMA {ident(op.name)}"])


def _drop_schema(op: s.DropSchemaOp, _d: bool) -> Plan:
    return Plan([f"DROP SCHEMA {ident(op.name)}{_cascade(op.cascade)}"], destructive=True)


def _rename_schema(op: s.RenameSchemaOp, _d: bool) -> Plan:
    return Plan([f"ALTER SCHEMA {ident(op.name)} RENAME TO {ident(op.new_name)}"])


def _create_table(op: s.CreateTableOp, _d: bool) -> Plan:
    table = qualified(op.schema_name, op.name)
    lines = [_column_def(c) for c in op.columns]
    if op.primary_key:
        lines.append(_pk_def(op.primary_key))
    lines += [_unique_def(u) for u in op.uniques]
    lines += [_check_def(c) for c in op.checks]
    lines += [_fk_def(fk) for fk in op.foreign_keys]
    body = ",\n  ".join(lines)
    create = f"CREATE TABLE {table} (\n  {body}\n)"
    if op.partition_by:
        create += f" PARTITION BY {op.partition_by.strategy.upper()} ({_cols(op.partition_by.columns)})"
    statements = [create]
    if op.comment:
        statements.append(_comment(f"TABLE {table}", op.comment))
    for col in op.columns:
        if col.comment:
            statements.append(_comment(f"COLUMN {table}.{ident(col.name)}", col.comment))
    return Plan(statements)


def _drop_table(op: s.DropTableOp, _d: bool) -> Plan:
    return Plan([f"DROP TABLE {qualified(op.schema_name, op.name)}{_cascade(op.cascade)}"], destructive=True)


def _alter_table(op: s.AlterTableOp, _d: bool) -> Plan:
    table = qualified(op.schema_name, op.name)
    statements: list[str] = []
    destructive = False
    for index, action in enumerate(op.actions):
        kind = action.action
        if kind == "rename_table" and index != len(op.actions) - 1:
            raise ValueError("Umbenennen der Tabelle muss die letzte Änderung sein.")
        if kind == "add_column":
            statements.append(f"ALTER TABLE {table} ADD COLUMN {_column_def(action.column)}")
            if action.column.comment:
                statements.append(_comment(f"COLUMN {table}.{ident(action.column.name)}", action.column.comment))
        elif kind == "drop_column":
            destructive = True
            statements.append(f"ALTER TABLE {table} DROP COLUMN {ident(action.column)}{_cascade(action.cascade)}")
        elif kind == "rename_column":
            statements.append(f"ALTER TABLE {table} RENAME COLUMN {ident(action.column)} TO {ident(action.new_name)}")
        elif kind == "alter_column_type":
            destructive = True
            using = f" USING {expression(action.using)}" if action.using else ""
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN {ident(action.column)} TYPE {type_name(action.type)}{using}"
            )
        elif kind == "set_default":
            if action.default:
                statements.append(
                    f"ALTER TABLE {table} ALTER COLUMN {ident(action.column)} SET DEFAULT {expression(action.default)}"
                )
            else:
                statements.append(f"ALTER TABLE {table} ALTER COLUMN {ident(action.column)} DROP DEFAULT")
        elif kind == "set_nullable":
            verb = "DROP NOT NULL" if action.nullable else "SET NOT NULL"
            statements.append(f"ALTER TABLE {table} ALTER COLUMN {ident(action.column)} {verb}")
        elif kind == "add_primary_key":
            statements.append(f"ALTER TABLE {table} ADD {_pk_def(action.constraint)}")
        elif kind == "add_unique":
            statements.append(f"ALTER TABLE {table} ADD {_unique_def(action.constraint)}")
        elif kind == "add_check":
            statements.append(f"ALTER TABLE {table} ADD {_check_def(action.constraint)}")
        elif kind == "add_foreign_key":
            statements.append(f"ALTER TABLE {table} ADD {_fk_def(action.constraint)}")
        elif kind == "drop_constraint":
            destructive = True
            statements.append(f"ALTER TABLE {table} DROP CONSTRAINT {ident(action.name)}{_cascade(action.cascade)}")
        elif kind == "rename_table":
            statements.append(f"ALTER TABLE {table} RENAME TO {ident(action.new_name)}")
        elif kind == "set_comment":
            target = f"COLUMN {table}.{ident(action.column)}" if action.column else f"TABLE {table}"
            statements.append(_comment(target, action.comment))
    return Plan(statements, destructive=destructive)


def _create_partition(op: s.CreatePartitionOp, _d: bool) -> Plan:
    b = op.bounds
    if b.kind == "default":
        bound = "DEFAULT"
    elif b.kind == "range":
        if not b.from_values or len(b.from_values) != len(b.to_values):
            raise ValueError("Bereichspartition braucht gleich viele Von- und Bis-Werte.")
        low = ", ".join("MINVALUE" if v is None else literal(v) for v in b.from_values)
        high = ", ".join("MAXVALUE" if v is None else literal(v) for v in b.to_values)
        bound = f"FOR VALUES FROM ({low}) TO ({high})"
    elif b.kind == "list":
        if not b.in_values:
            raise ValueError("Listenpartition braucht mindestens einen Wert.")
        bound = f"FOR VALUES IN ({', '.join(literal(v) for v in b.in_values)})"
    else:
        if b.modulus is None or b.remainder is None or b.remainder >= b.modulus:
            raise ValueError("Hashpartition braucht Modulus und einen kleineren Rest.")
        bound = f"FOR VALUES WITH (MODULUS {int(b.modulus)}, REMAINDER {int(b.remainder)})"
    return Plan(
        [f"CREATE TABLE {qualified(op.schema_name, op.name)} PARTITION OF {qualified(op.schema_name, op.parent)} {bound}"]
    )


def _detach_partition(op: s.DetachPartitionOp, _d: bool) -> Plan:
    return Plan(
        [f"ALTER TABLE {qualified(op.schema_name, op.parent)} DETACH PARTITION {qualified(op.schema_name, op.name)}"],
        destructive=True,
    )


def _create_index(op: s.CreateIndexOp, _d: bool) -> Plan:
    parts = []
    for col in op.columns:
        if bool(col.column) == bool(col.expression):
            raise ValueError("Indexspalte: genau eine Spalte oder ein Ausdruck.")
        item = ident(col.column) if col.column else f"({expression(col.expression or '')})"
        if col.descending:
            item += " DESC"
        if col.nulls:
            item += f" NULLS {col.nulls.upper()}"
        parts.append(item)
    unique = "UNIQUE " if op.unique else ""
    concurrently = "CONCURRENTLY " if op.concurrently else ""
    text = (
        f"CREATE {unique}INDEX {concurrently}{ident(op.name)} ON {qualified(op.schema_name, op.table)} "
        f"USING {op.method} ({', '.join(parts)})"
    )
    if op.include:
        text += f" INCLUDE ({_cols(op.include)})"
    if op.where:
        text += f" WHERE {expression(op.where)}"
    return Plan([text], mode="autocommit" if op.concurrently else "tx")


def _drop_index(op: s.DropIndexOp, _d: bool) -> Plan:
    if op.concurrently and op.cascade:
        raise ValueError("DROP INDEX CONCURRENTLY kennt kein CASCADE.")
    concurrently = "CONCURRENTLY " if op.concurrently else ""
    return Plan(
        [f"DROP INDEX {concurrently}{qualified(op.schema_name, op.name)}{_cascade(op.cascade)}"],
        mode="autocommit" if op.concurrently else "tx",
        destructive=True,
    )


def _create_view(op: s.CreateViewOp, _d: bool) -> Plan:
    query = expression(op.query).rstrip().rstrip(";").rstrip()
    view = qualified(op.schema_name, op.name)
    if op.materialized:
        if op.or_replace:
            raise ValueError("Materialisierte Views kennen kein OR REPLACE.")
        data = "WITH DATA" if op.with_data else "WITH NO DATA"
        return Plan([f"CREATE MATERIALIZED VIEW {view} AS\n{query}\n{data}"])
    replace = "OR REPLACE " if op.or_replace else ""
    return Plan([f"CREATE {replace}VIEW {view} AS\n{query}"])


def _drop_view(op: s.DropViewOp, _d: bool) -> Plan:
    kind = "MATERIALIZED VIEW" if op.materialized else "VIEW"
    return Plan([f"DROP {kind} {qualified(op.schema_name, op.name)}{_cascade(op.cascade)}"], destructive=True)


def _refresh_matview(op: s.RefreshMaterializedViewOp, _d: bool) -> Plan:
    concurrently = "CONCURRENTLY " if op.concurrently else ""
    return Plan(
        [f"REFRESH MATERIALIZED VIEW {concurrently}{qualified(op.schema_name, op.name)}"],
        permission=WRITE_PERMISSION,
    )


def _create_sequence(op: s.CreateSequenceOp, _d: bool) -> Plan:
    text = f"CREATE SEQUENCE {qualified(op.schema_name, op.name)}"
    if op.increment is not None:
        text += f" INCREMENT BY {int(op.increment)}"
    if op.min_value is not None:
        text += f" MINVALUE {int(op.min_value)}"
    if op.max_value is not None:
        text += f" MAXVALUE {int(op.max_value)}"
    if op.start is not None:
        text += f" START WITH {int(op.start)}"
    if op.cycle:
        text += " CYCLE"
    return Plan([text])


def _restart_sequence(op: s.RestartSequenceOp, _d: bool) -> Plan:
    value = f" WITH {int(op.value)}" if op.value is not None else ""
    return Plan([f"ALTER SEQUENCE {qualified(op.schema_name, op.name)} RESTART{value}"], destructive=True)


def _drop_sequence(op: s.DropSequenceOp, _d: bool) -> Plan:
    return Plan([f"DROP SEQUENCE {qualified(op.schema_name, op.name)}{_cascade(op.cascade)}"], destructive=True)


def _create_enum(op: s.CreateEnumOp, _d: bool) -> Plan:
    if len(set(op.values)) != len(op.values):
        raise ValueError("Enum-Werte müssen eindeutig sein.")
    values = ", ".join(literal(v) for v in op.values)
    return Plan([f"CREATE TYPE {qualified(op.schema_name, op.name)} AS ENUM ({values})"])


def _add_enum_value(op: s.AddEnumValueOp, _d: bool) -> Plan:
    if op.before and op.after:
        raise ValueError("Nur BEFORE oder AFTER, nicht beides.")
    text = f"ALTER TYPE {qualified(op.schema_name, op.name)} ADD VALUE IF NOT EXISTS {literal(op.value)}"
    if op.before:
        text += f" BEFORE {literal(op.before)}"
    elif op.after:
        text += f" AFTER {literal(op.after)}"
    return Plan([text])


def _rename_enum_value(op: s.RenameEnumValueOp, _d: bool) -> Plan:
    return Plan(
        [f"ALTER TYPE {qualified(op.schema_name, op.name)} RENAME VALUE {literal(op.value)} TO {literal(op.new_value)}"]
    )


def _drop_type(op: s.DropTypeOp, _d: bool) -> Plan:
    return Plan([f"DROP TYPE {qualified(op.schema_name, op.name)}{_cascade(op.cascade)}"], destructive=True)


def _create_function(op: s.CreateFunctionOp, _d: bool) -> Plan:
    args = signature(op.arguments, "Argumente")
    returns = signature(op.returns, "Rückgabetyp")
    replace = "OR REPLACE " if op.or_replace else ""
    lines = [
        f"CREATE {replace}FUNCTION {qualified(op.schema_name, op.name)}({args})",
        f"RETURNS {returns}",
        f"LANGUAGE {op.language}",
        op.volatility.upper(),
    ]
    if op.strict:
        lines.append("STRICT")
    if op.security_definer:
        # Ohne festen Suchpfad könnte ein Aufrufer eigene Objekte unterschieben.
        lines.append("SECURITY DEFINER")
        lines.append(f"SET search_path = {ident(op.schema_name)}, pg_temp")
    if "\x00" in op.body:
        raise ValueError("NUL-Zeichen im Funktionskörper.")
    lines.append(f"AS {dollar_quote(op.body)}")
    return Plan(["\n".join(lines)])


def _drop_function(op: s.DropFunctionOp, _d: bool) -> Plan:
    args = signature(op.arguments, "Argumente")
    return Plan(
        [f"DROP FUNCTION {qualified(op.schema_name, op.name)}({args}){_cascade(op.cascade)}"],
        destructive=True,
    )


def _create_trigger(op: s.CreateTriggerOp, _d: bool) -> Plan:
    if len(set(op.events)) != len(op.events):
        raise ValueError("Ereignisse doppelt.")
    events = []
    for event in op.events:
        if event == "update" and op.update_columns:
            events.append(f"UPDATE OF {_cols(op.update_columns)}")
        else:
            events.append(event.upper())
    timing = {"before": "BEFORE", "after": "AFTER", "instead_of": "INSTEAD OF"}[op.timing]
    text = (
        f"CREATE TRIGGER {ident(op.name)} {timing} {' OR '.join(events)} "
        f"ON {qualified(op.schema_name, op.table)} FOR EACH {op.for_each.upper()}"
    )
    if op.when:
        text += f" WHEN ({expression(op.when)})"
    text += f" EXECUTE FUNCTION {qualified(op.function_schema, op.function_name)}()"
    return Plan([text])


def _drop_trigger(op: s.DropTriggerOp, _d: bool) -> Plan:
    return Plan([f"DROP TRIGGER {ident(op.name)} ON {qualified(op.schema_name, op.table)}"], destructive=True)


def _set_trigger_enabled(op: s.SetTriggerEnabledOp, _d: bool) -> Plan:
    verb = "ENABLE" if op.enabled else "DISABLE"
    return Plan([f"ALTER TABLE {qualified(op.schema_name, op.table)} {verb} TRIGGER {ident(op.name)}"])


def _extension_identity(dedicated: bool) -> str:
    # Eigene Instanz: der Admin darf alles, was verfügbar ist. Geteilter
    # Cluster: der Owner, und PostgreSQL lässt ihn nur „trusted" Extensions.
    return "admin" if dedicated else "owner"


def _create_extension(op: s.CreateExtensionOp, dedicated: bool) -> Plan:
    text = f"CREATE EXTENSION IF NOT EXISTS {ident(op.name)}"
    if op.schema_name:
        text += f" SCHEMA {ident(op.schema_name)}"
    return Plan([text], identity=_extension_identity(dedicated))


def _drop_extension(op: s.DropExtensionOp, dedicated: bool) -> Plan:
    return Plan(
        [f"DROP EXTENSION {ident(op.name)}{_cascade(op.cascade)}"],
        identity=_extension_identity(dedicated),
        destructive=True,
    )


def _update_extension(op: s.UpdateExtensionOp, dedicated: bool) -> Plan:
    return Plan([f"ALTER EXTENSION {ident(op.name)} UPDATE"], identity=_extension_identity(dedicated))


def _set_rls(op: s.SetRowLevelSecurityOp, _d: bool) -> Plan:
    table = qualified(op.schema_name, op.table)
    statements = [f"ALTER TABLE {table} {'ENABLE' if op.enabled else 'DISABLE'} ROW LEVEL SECURITY"]
    statements.append(f"ALTER TABLE {table} {'FORCE' if op.forced else 'NO FORCE'} ROW LEVEL SECURITY")
    return Plan(statements, destructive=not op.enabled)


def _create_policy(op: s.CreatePolicyOp, _d: bool) -> Plan:
    if op.command == "insert" and op.using:
        raise ValueError("INSERT-Richtlinien kennen nur WITH CHECK.")
    if op.command in {"select", "delete"} and op.with_check:
        raise ValueError(f"{op.command.upper()}-Richtlinien kennen nur USING.")
    if not op.using and not op.with_check:
        raise ValueError("Richtlinie braucht USING oder WITH CHECK.")
    roles = op.roles or ["PUBLIC"]
    text = (
        f"CREATE POLICY {ident(op.name)} ON {qualified(op.schema_name, op.table)} "
        f"AS {'PERMISSIVE' if op.permissive else 'RESTRICTIVE'} FOR {op.command.upper()} TO {_roles(roles)}"
    )
    if op.using:
        text += f" USING ({expression(op.using)})"
    if op.with_check:
        text += f" WITH CHECK ({expression(op.with_check)})"
    return Plan([text], roles=[r for r in roles if r.upper() != "PUBLIC"])


def _drop_policy(op: s.DropPolicyOp, _d: bool) -> Plan:
    return Plan([f"DROP POLICY {ident(op.name)} ON {qualified(op.schema_name, op.table)}"], destructive=True)


def _grant_object(object_type: str, schema_name: str, obj: str) -> str:
    if object_type != "function":
        return qualified(schema_name, obj)
    match = re.fullmatch(r"\s*([^()]+?)\s*\((.*)\)\s*", obj, re.DOTALL)
    if not match:
        raise ValueError("Funktion mit Signatur angeben: name(argumente).")
    return f"{qualified(schema_name, match.group(1))}({signature(match.group(2), 'Argumente')})"


def _grant(op: s.GrantOp, _d: bool) -> Plan:
    allowed = PRIVILEGES[op.object_type]
    privileges = [p.strip().upper() for p in op.privileges]
    unknown = [p for p in privileges if p not in allowed]
    if unknown:
        raise ValueError(f"Recht passt nicht zu {op.object_type}: {', '.join(unknown)}")
    if "ALL" in privileges:
        privileges = ["ALL PRIVILEGES"]
    if op.object_type == "schema":
        target = f"SCHEMA {ident(op.schema_name)}"
    else:
        if not op.objects:
            raise ValueError("Mindestens ein Objekt angeben.")
        objects = ", ".join(_grant_object(op.object_type, op.schema_name, o) for o in op.objects)
        target = f"{op.object_type.upper()} {objects}"
    roles = _roles(op.roles)
    if op.op == "grant":
        text = f"GRANT {', '.join(privileges)} ON {target} TO {roles}"
        if op.grant_option:
            text += " WITH GRANT OPTION"
        destructive = False
    else:
        prefix = "GRANT OPTION FOR " if op.grant_option else ""
        text = f"REVOKE {prefix}{', '.join(privileges)} ON {target} FROM {roles}"
        destructive = True
    return Plan([text], destructive=destructive, roles=[r for r in op.roles if r.upper() != "PUBLIC"])


def _role_options(attrs: s.RoleAttributes) -> tuple[str, str]:
    """Liefert (ausgeführt, angezeigt) — nur das Passwort unterscheidet sie."""
    parts: list[str] = []
    for flag, name in (
        (attrs.login, "LOGIN"),
        (attrs.inherit, "INHERIT"),
        (attrs.createdb, "CREATEDB"),
        (attrs.createrole, "CREATEROLE"),
        (attrs.bypassrls, "BYPASSRLS"),
    ):
        if flag is not None:
            parts.append(name if flag else f"NO{name}")
    if attrs.connection_limit is not None:
        parts.append(f"CONNECTION LIMIT {int(attrs.connection_limit)}")
    if attrs.valid_until:
        parts.append(f"VALID UNTIL {literal(attrs.valid_until)}")
    shown = list(parts)
    if attrs.password:
        parts.append(f"PASSWORD {literal(attrs.password)}")
        shown.append("PASSWORD '********'")
    return " ".join(parts), " ".join(shown)


def _instance_plan(statements: list[str], display: list[str], **kwargs: Any) -> Plan:
    return Plan(
        statements,
        identity="admin",
        scope="instance",
        display=display if display != statements else [],
        **kwargs,
    )


def _create_role(op: s.CreateRoleOp, _d: bool) -> Plan:
    run, shown = _role_options(op.attributes)
    base = f"CREATE ROLE {ident(op.name)}"
    statements = [f"{base} WITH {run}" if run else base]
    display = [f"{base} WITH {shown}" if shown else base]
    for member in op.member_of:
        grant = f"GRANT {ident(member)} TO {ident(op.name)}"
        statements.append(grant)
        display.append(grant)
    return _instance_plan(statements, display, roles=[op.name, *op.member_of])


def _alter_role(op: s.AlterRoleOp, _d: bool) -> Plan:
    run, shown = _role_options(op.attributes)
    statements: list[str] = []
    display: list[str] = []
    if run:
        statements.append(f"ALTER ROLE {ident(op.name)} WITH {run}")
        display.append(f"ALTER ROLE {ident(op.name)} WITH {shown}")
    for member in op.grant_membership:
        statements.append(f"GRANT {ident(member)} TO {ident(op.name)}")
    for member in op.revoke_membership:
        statements.append(f"REVOKE {ident(member)} FROM {ident(op.name)}")
    display += statements[len(display) :]
    if not statements:
        raise ValueError("Keine Änderung angegeben.")
    return _instance_plan(
        statements,
        display,
        roles=[op.name, *op.grant_membership, *op.revoke_membership],
        destructive=bool(op.revoke_membership),
    )


def _drop_role(op: s.DropRoleOp, _d: bool) -> Plan:
    statement = f"DROP ROLE {ident(op.name)}"
    return _instance_plan([statement], [statement], roles=[op.name], destructive=True)


def parameter_blocked(name: str) -> bool:
    return name in BLOCKED_PARAMETERS or name.startswith(BLOCKED_PARAMETER_PREFIXES)


def _parameter_name(name: str) -> str:
    name = name.strip().lower()
    if not _PARAM_NAME_RE.match(name):
        raise ValueError("Ungültiger Parametername.")
    if parameter_blocked(name):
        raise ValueError(f"{name} verwaltet das Panel.")
    return name


def _set_parameter(op: s.SetParameterOp, _d: bool) -> Plan:
    name = _parameter_name(op.name)
    statements = [f"ALTER SYSTEM SET {name} = {literal(op.value)}", "SELECT pg_reload_conf()"]
    return _instance_plan(statements, statements, mode="autocommit", parameter=name)


def _reset_parameter(op: s.ResetParameterOp, _d: bool) -> Plan:
    name = _parameter_name(op.name)
    statements = [f"ALTER SYSTEM RESET {name}", "SELECT pg_reload_conf()"]
    return _instance_plan(statements, statements, mode="autocommit", parameter=name)


def _maintenance_target(schema_name: str | None, table: str | None) -> str:
    if table:
        return " " + qualified(schema_name or "public", table)
    return ""


def _vacuum(op: s.VacuumOp, _d: bool) -> Plan:
    options = []
    if op.full:
        options.append("FULL")
    if op.analyze:
        options.append("ANALYZE")
    opts = f" ({', '.join(options)})" if options else ""
    return Plan(
        [f"VACUUM{opts}{_maintenance_target(op.schema_name, op.table)}"],
        mode="autocommit",
        permission=ADMIN_PERMISSION if op.full else WRITE_PERMISSION,
    )


def _analyze(op: s.AnalyzeOp, _d: bool) -> Plan:
    return Plan([f"ANALYZE{_maintenance_target(op.schema_name, op.table)}"], permission=WRITE_PERMISSION)


def _reindex(op: s.ReindexOp, _d: bool) -> Plan:
    concurrently = " CONCURRENTLY" if op.concurrently else ""
    return Plan(
        [f"REINDEX {op.target.upper()}{concurrently} {qualified(op.schema_name, op.name)}"],
        mode="autocommit" if op.concurrently else "tx",
        permission=WRITE_PERMISSION,
    )


_HANDLERS: dict[str, Any] = {
    "create_schema": _create_schema,
    "drop_schema": _drop_schema,
    "rename_schema": _rename_schema,
    "create_table": _create_table,
    "drop_table": _drop_table,
    "alter_table": _alter_table,
    "create_partition": _create_partition,
    "detach_partition": _detach_partition,
    "create_index": _create_index,
    "drop_index": _drop_index,
    "create_view": _create_view,
    "drop_view": _drop_view,
    "refresh_materialized_view": _refresh_matview,
    "create_sequence": _create_sequence,
    "restart_sequence": _restart_sequence,
    "drop_sequence": _drop_sequence,
    "create_enum": _create_enum,
    "add_enum_value": _add_enum_value,
    "rename_enum_value": _rename_enum_value,
    "drop_type": _drop_type,
    "create_function": _create_function,
    "drop_function": _drop_function,
    "create_trigger": _create_trigger,
    "drop_trigger": _drop_trigger,
    "set_trigger_enabled": _set_trigger_enabled,
    "create_extension": _create_extension,
    "drop_extension": _drop_extension,
    "update_extension": _update_extension,
    "set_rls": _set_rls,
    "create_policy": _create_policy,
    "drop_policy": _drop_policy,
    "grant": _grant,
    "revoke": _grant,
    "create_role": _create_role,
    "alter_role": _alter_role,
    "drop_role": _drop_role,
    "set_parameter": _set_parameter,
    "reset_parameter": _reset_parameter,
    "vacuum": _vacuum,
    "analyze": _analyze,
    "reindex": _reindex,
}
