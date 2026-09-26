"""DDL-Compiler des Studios: Namen gequotet, Werte als Literal, Grenzen hart."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas.postgres_studio import StudioOperation
from services import postgres_ddl

_OP = TypeAdapter(StudioOperation)


def plan(spec: dict, *, dedicated: bool = False) -> postgres_ddl.Plan:
    return postgres_ddl.compile_operation(_OP.validate_python(spec), dedicated=dedicated)


def test_namen_werden_immer_gequotet():
    p = plan({"op": "create_table", "schema_name": "public", "name": 'x"; DROP TABLE y; --',
              "columns": [{"name": "id", "type": "bigint", "nullable": False}]})
    assert p.statements[0].startswith('CREATE TABLE "public"."x""; DROP TABLE y; --" (')


def test_datentyp_ist_kein_freitext():
    with pytest.raises(ValueError, match="Datentyp"):
        plan({"op": "create_table", "name": "t",
              "columns": [{"name": "a", "type": "int); DROP TABLE t; --"}]})
    ok = plan({"op": "create_table", "name": "t", "columns": [
        {"name": "a", "type": "character varying(255)"},
        {"name": "b", "type": "numeric(10, 2)[]"},
        {"name": "c", "type": "timestamp with time zone"},
        {"name": "d", "type": '"Stimmung"'},
    ]})
    assert "numeric(10, 2)[]" in ok.statements[0]


def test_tabelle_mit_allen_bausteinen():
    p = plan({
        "op": "create_table", "schema_name": "app", "name": "orders",
        "columns": [
            {"name": "id", "type": "bigint", "identity": "always", "nullable": False},
            {"name": "customer_id", "type": "bigint", "nullable": False, "comment": "Kunde"},
            {"name": "total", "type": "numeric(10,2)", "default": "0"},
        ],
        "primary_key": {"columns": ["id"]},
        "uniques": [{"name": "orders_u", "columns": ["customer_id", "total"], "nulls_not_distinct": True}],
        "checks": [{"name": "total_pos", "expression": "total >= 0"}],
        "foreign_keys": [{"columns": ["customer_id"], "ref_schema": "app", "ref_table": "customers",
                          "ref_columns": ["id"], "on_delete": "cascade", "on_update": "set_null"}],
        "comment": "Bestellungen",
    })
    create = p.statements[0]
    assert '"id" bigint GENERATED ALWAYS AS IDENTITY NOT NULL' in create
    assert 'CONSTRAINT "orders_u" UNIQUE NULLS NOT DISTINCT ("customer_id", "total")' in create
    assert 'CONSTRAINT "total_pos" CHECK (total >= 0)' in create
    assert 'REFERENCES "app"."customers" ("id") ON DELETE CASCADE ON UPDATE SET NULL' in create
    assert p.statements[1] == 'COMMENT ON TABLE "app"."orders" IS \'Bestellungen\''
    assert p.statements[2] == 'COMMENT ON COLUMN "app"."orders"."customer_id" IS \'Kunde\''
    assert p.identity == "owner" and p.mode == "tx" and not p.destructive


def test_identity_und_default_schliessen_sich_aus():
    with pytest.raises(ValueError, match="Identity"):
        plan({"op": "create_table", "name": "t", "columns": [
            {"name": "id", "type": "int", "identity": "always", "default": "1"}]})


def test_umbenennen_nur_als_letzte_aenderung():
    with pytest.raises(ValueError, match="letzte"):
        plan({"op": "alter_table", "name": "t", "actions": [
            {"action": "rename_table", "new_name": "u"},
            {"action": "drop_column", "column": "a"},
        ]})


def test_spalte_loeschen_ist_destruktiv():
    p = plan({"op": "alter_table", "name": "t", "actions": [
        {"action": "add_column", "column": {"name": "n", "type": "text"}},
        {"action": "drop_column", "column": "a", "cascade": True},
    ]})
    assert p.destructive
    assert p.statements[1] == 'ALTER TABLE "public"."t" DROP COLUMN "a" CASCADE'


def test_partitionsgrenzen_sind_literale():
    p = plan({"op": "create_partition", "parent": "m", "name": "m_2026",
              "bounds": {"kind": "range", "from_values": ["2026-01-01"], "to_values": ["2027'); DROP TABLE m; --"]}})
    assert "TO ('2027''); DROP TABLE m; --')" in p.statements[0]
    p = plan({"op": "create_partition", "parent": "m", "name": "m_min",
              "bounds": {"kind": "range", "from_values": [None], "to_values": ["2020-01-01"]}})
    assert "FROM (MINVALUE) TO ('2020-01-01')" in p.statements[0]
    with pytest.raises(ValueError):
        plan({"op": "create_partition", "parent": "m", "name": "h",
              "bounds": {"kind": "hash", "modulus": 4, "remainder": 4}})


def test_index_concurrently_laeuft_ohne_transaktion():
    p = plan({"op": "create_index", "table": "t", "name": "t_idx", "concurrently": True, "method": "gin",
              "columns": [{"expression": "lower(name)"}], "where": "deleted_at IS NULL"})
    assert p.mode == "autocommit"
    assert p.statements[0] == (
        'CREATE INDEX CONCURRENTLY "t_idx" ON "public"."t" USING gin ((lower(name))) WHERE deleted_at IS NULL'
    )
    with pytest.raises(ValueError):
        plan({"op": "drop_index", "name": "i", "concurrently": True, "cascade": True})


def test_funktionskoerper_bekommt_freien_dollar_tag():
    p = plan({"op": "create_function", "name": "f", "returns": "trigger",
              "body": "BEGIN RETURN $fn$x$fn$; END", "security_definer": True})
    text = p.statements[0]
    assert "$fn$BEGIN" not in text  # der Tag im Körper wird nicht wiederverwendet
    assert 'SET search_path = "public", pg_temp' in text
    with pytest.raises(ValueError):
        plan({"op": "create_function", "name": "f", "arguments": "a int); DROP TABLE t; --",
              "returns": "int", "body": "SELECT 1"})


def test_richtlinienregeln():
    with pytest.raises(ValueError):
        plan({"op": "create_policy", "table": "t", "name": "p", "command": "insert", "using": "true"})
    p = plan({"op": "create_policy", "table": "t", "name": "p", "command": "select",
              "roles": ["leser", "PUBLIC"], "using": "owner = current_user"})
    assert 'TO "leser", PUBLIC USING (owner = current_user)' in p.statements[0]
    assert p.roles == ["leser"]


def test_rechte_passen_zum_objekttyp():
    with pytest.raises(ValueError, match="Recht"):
        plan({"op": "grant", "object_type": "schema", "privileges": ["SELECT"], "roles": ["r"]})
    p = plan({"op": "revoke", "object_type": "function", "objects": ["f(integer, text)"],
              "privileges": ["execute"], "roles": ["r"]})
    assert p.statements[0] == 'REVOKE EXECUTE ON FUNCTION "public"."f"(integer, text) FROM "r"'
    assert p.destructive


def test_rollenpasswort_nur_maskiert_in_der_vorschau():
    geheim = "rollen-" + "passwort-1"
    p = plan({"op": "create_role", "name": "leser",
              "attributes": {"login": True, "password": geheim, "connection_limit": 5},
              "member_of": ["pg_read_all_data"]}, dedicated=True)
    assert geheim in p.statements[0]
    assert all(geheim not in line for line in p.preview())
    assert "PASSWORD '********'" in p.preview()[0]
    assert p.identity == "admin" and p.scope == "instance"
    assert p.statements[1] == 'GRANT "pg_read_all_data" TO "leser"'


def test_rolle_kennt_kein_superuser():
    with pytest.raises(ValidationError):
        _OP.validate_python({"op": "create_role", "name": "x", "attributes": {"superuser": True}})


@pytest.mark.parametrize("name", ["archive_command", "shared_preload_libraries", "ssl_key_file", "port", "hba_file"])
def test_gefaehrliche_parameter_bleiben_beim_panel(name):
    with pytest.raises(ValueError, match="verwaltet"):
        plan({"op": "set_parameter", "name": name, "value": "x"}, dedicated=True)


def test_parameter_wert_ist_literal():
    p = plan({"op": "set_parameter", "name": "work_mem", "value": "64MB'; DROP"}, dedicated=True)
    assert p.statements == ["ALTER SYSTEM SET work_mem = '64MB''; DROP'", "SELECT pg_reload_conf()"]
    assert p.mode == "autocommit" and p.parameter == "work_mem"


def test_extension_identitaet_folgt_der_instanz():
    assert plan({"op": "create_extension", "name": "pg_trgm"}).identity == "owner"
    assert plan({"op": "create_extension", "name": "pg_trgm"}, dedicated=True).identity == "admin"


def test_wartung_braucht_nur_schreibrecht_ausser_vacuum_full():
    assert plan({"op": "vacuum", "table": "t"}).permission == postgres_ddl.WRITE_PERMISSION
    assert plan({"op": "vacuum", "full": True}).permission == postgres_ddl.ADMIN_PERMISSION
    assert plan({"op": "vacuum"}).mode == "autocommit"
