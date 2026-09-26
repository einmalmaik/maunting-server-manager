"""PostgreSQL-Studio gegen ein echtes PostgreSQL 17.

Läuft nur mit Docker. Der Agent wird durch einen lokalen Ausführer ersetzt,
der dieselben Regeln hat (Modus read/tx/autocommit, Rollback, Owner- oder
Admin-Verbindung, ``params=None`` heißt: nicht formatieren). Den echten
Agenten prüft ``msm-agent/tests/test_postgres_run_live.py``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from models import AuditLog, PostgresDatabase, PostgresInstance, PostgresUser, Server  # noqa: E402
from models.server_port import ServerPort  # noqa: E402
from services import postgres_service  # noqa: E402
from services.auth_service import AuthService  # noqa: E402

ADMIN_PW = "studio-" + "admin-" + "pw"
OWNER_PW = "studio-" + "owner-" + "pw"
NAME = f"msm-studio-pgtest-{os.getpid()}"


def _freier_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _connect(port: int, dbname: str, user: str = "msm_admin", password: str = ADMIN_PW):
    return psycopg2.connect(host="127.0.0.1", port=port, dbname=dbname, user=user, password=password,
                            connect_timeout=5)


@pytest.fixture(scope="module")
def pg_port():
    if not _docker_ok():
        pytest.skip("Docker nicht verfuegbar")
    port = _freier_port()
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--name", NAME, "-e", "POSTGRES_USER=msm_admin",
         "-e", f"POSTGRES_PASSWORD={ADMIN_PW}", "-e", "POSTGRES_DB=msm_control",
         "-p", f"127.0.0.1:{port}:5432", "postgres:17"],
        capture_output=True, text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"postgres:17 startet nicht: {started.stderr[:200]}")
    deadline = time.monotonic() + 90
    while True:
        try:
            _connect(port, "msm_control").close()
            break
        except psycopg2.OperationalError:
            if time.monotonic() > deadline:
                subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
                raise
            time.sleep(1)
    # Wie das Panel es anlegt: Owner ohne Sonderrechte, eigene Datenbank.
    conn = _connect(port, "msm_control")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE ROLE app_owner LOGIN PASSWORD '{OWNER_PW}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        cur.execute("CREATE ROLE app_owner_app LOGIN NOSUPERUSER")
        cur.execute("CREATE ROLE fremder_owner LOGIN NOSUPERUSER")
        cur.execute("CREATE DATABASE fremd OWNER fremder_owner")
    conn.close()
    yield port
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


@pytest.fixture
def app_db(pg_port):
    conn = _connect(pg_port, "msm_control")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS app WITH (FORCE)")
        cur.execute("CREATE DATABASE app OWNER app_owner")
        cur.execute("REVOKE CONNECT ON DATABASE app FROM PUBLIC")
        cur.execute("GRANT CONNECT ON DATABASE app TO app_owner, app_owner_app")
    conn.close()
    conn = _connect(pg_port, "app")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("ALTER SCHEMA public OWNER TO app_owner")
    conn.close()
    yield pg_port
    conn = _connect(pg_port, "msm_control")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' AND rolname NOT IN "
            "('msm_admin','app_owner','app_owner_app','fremder_owner')"
        )
        extra = [r[0] for r in cur.fetchall()]
        cur.execute("DROP DATABASE IF EXISTS app WITH (FORCE)")
        for name in extra:
            cur.execute(f'DROP ROLE IF EXISTS "{name}"')
        cur.execute("ALTER SYSTEM RESET ALL")
        cur.execute("SELECT pg_reload_conf()")
    conn.close()


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return str(value)


@pytest.fixture(autouse=True)
def lokaler_agent(request, monkeypatch):
    if "app_db" not in request.fixturenames:
        return
    port = request.getfixturevalue("app_db")
    calls: list[dict[str, Any]] = []

    def run(db, server, *, database_name, statements, identity="owner", database=None,
            mode="read", rollback=False, row_limit=500, timeout_ms=None):
        calls.append({"identity": identity, "mode": mode, "statements": [t for t, _ in statements]})
        if identity == "owner":
            conn = _connect(port, database_name, database.owner_role, postgres_service._owner_password(database))
        else:
            conn = _connect(port, database_name)
        results = []
        try:
            if mode == "autocommit":
                conn.autocommit = True
            elif mode == "read":
                conn.set_session(readonly=True)
            with conn.cursor() as cur:
                for index, (text, params) in enumerate(statements):
                    try:
                        cur.execute(text, params)
                    except psycopg2.Error as exc:
                        if not conn.autocommit:
                            conn.rollback()
                        raise ValueError(f"Statement {index + 1}: [{exc.pgcode}] {exc.diag.message_primary}") from exc
                    entry = {"columns": [], "rows": [], "row_count": cur.rowcount,
                             "status": cur.statusmessage, "truncated": False, "duration_ms": 0}
                    if cur.description:
                        entry["columns"] = [d[0] for d in cur.description]
                        fetched = cur.fetchmany(row_limit + 1)
                        entry["truncated"] = len(fetched) > row_limit
                        entry["rows"] = [[_json(v) for v in row] for row in fetched[:row_limit]]
                    results.append(entry)
            if not conn.autocommit:
                (conn.rollback if rollback or mode == "read" else conn.commit)()
        finally:
            notices = [n.strip() for n in conn.notices]
            conn.close()
        return {"results": results, "notices": notices, "duration_ms": 0}

    monkeypatch.setattr(postgres_service, "run", run)
    return calls


def _server(db, *, dedicated: bool) -> Server:
    server = Server(
        name="Studio-DB" if dedicated else "Spielserver",
        game_type="postgres" if dedicated else "dayz",
        install_dir="/tmp/msm-studio", container_name="msm-srv-studio", status="running",
        public_bind_ip="127.0.0.1",
    )
    db.add(server)
    db.flush()
    if dedicated:
        db.add(ServerPort(server_id=server.id, role="database", port=25432, protocol="tcp"))
        db.add(PostgresInstance(
            server_id=server.id,
            admin_password_encrypted=AuthService.encrypt_secret(ADMIN_PW, aad=postgres_service.instance_aad(server.id)),
            allowed_cidrs="", ssl_required=True,
        ))
    db.add(PostgresDatabase(
        server_id=server.id, name="app", owner_role="app_owner", is_power_user=dedicated,
        owner_password_encrypted=AuthService.encrypt_secret(OWNER_PW, aad="msm:pg:db:owner"),
    ))
    db.add(PostgresUser(server_id=server.id, username="app_owner_app", password_mask="****"))
    db.commit()
    db.refresh(server)
    return server


@pytest.fixture
def dedicated(db, app_db):
    return _server(db, dedicated=True)


@pytest.fixture
def shared(db, app_db):
    return _server(db, dedicated=False)


class Studio:
    def __init__(self, client, cookies, csrf, server):
        self.client, self.cookies, self.server = client, cookies, server
        self.headers = {"X-CSRF-Token": csrf or ""}
        self.base = f"/api/servers/{server.id}/databases/{server.postgres_databases[0].id}/studio"

    def get(self, path, **params):
        return self.client.get(self.base + path, params=params, cookies=self.cookies)

    def post(self, path, body):
        return self.client.post(self.base + path, json=body, cookies=self.cookies, headers=self.headers)

    def ok(self, response):
        assert response.status_code == 200, response.text
        return response.json()

    def do(self, operation):
        preview = self.ok(self.post("/preview", {"operation": operation}))
        result = self.ok(self.post("/execute", {"operation": operation}))
        assert result["plan"] == preview
        return result


@pytest.fixture
def studio(client, owner_cookies, csrf_token, dedicated):
    return Studio(client, owner_cookies, csrf_token, dedicated)


@pytest.fixture
def studio_shared(client, owner_cookies, csrf_token, shared):
    return Studio(client, owner_cookies, csrf_token, shared)


def _tabellen(s: Studio) -> None:
    s.do({"op": "create_table", "name": "kunden", "columns": [
        {"name": "id", "type": "bigint", "identity": "always", "nullable": False},
        {"name": "name", "type": "text", "nullable": False},
        {"name": "daten", "type": "jsonb", "default": "'{}'::jsonb"},
    ], "primary_key": {"columns": ["id"]}, "uniques": [{"columns": ["name"]}]})
    s.do({"op": "create_table", "name": "bestellungen", "columns": [
        {"name": "id", "type": "bigint", "identity": "by_default", "nullable": False},
        {"name": "kunde_id", "type": "bigint", "nullable": False},
        {"name": "summe", "type": "numeric(10,2)", "default": "0"},
    ], "primary_key": {"columns": ["id"]},
        "checks": [{"name": "summe_positiv", "expression": "summe >= 0"}],
        "foreign_keys": [{"columns": ["kunde_id"], "ref_table": "kunden", "ref_columns": ["id"],
                          "on_delete": "cascade"}]})


# ── Struktur ───────────────────────────────────────────────────────────────


def test_tabellen_entwerfen_und_im_katalog_sehen(studio, lokaler_agent):
    _tabellen(studio)
    overview = studio.ok(studio.get(""))
    assert overview["kind"] == "dedicated"
    assert overview["permissions"] == {"read": True, "write": True, "admin": True}
    assert any(sch["name"] == "public" for sch in overview["schemas"])

    objects = studio.ok(studio.get("/objects", schema="public"))
    assert {r["name"] for r in objects["relations"]} == {"kunden", "bestellungen"}
    # Die Identity-Sequenzen gehören zur Spalte, nicht in die Liste.
    assert objects["sequences"] == []

    details = studio.ok(studio.get("/table", schema="public", table="bestellungen"))
    fk = next(c for c in details["constraints"] if c["type"] == "foreign_key")
    assert fk["ref_table"] == "kunden" and fk["on_delete"] == "cascade" and fk["columns"] == ["kunde_id"]
    assert details["primary_key"] == ["id"]
    assert next(c for c in details["columns"] if c["name"] == "id")["identity"] == "by_default"
    kunden = studio.ok(studio.get("/table", schema="public", table="kunden"))
    assert kunden["referenced_by"][0]["table"] == "bestellungen"
    # Strukturänderungen laufen als Owner, nie als Admin.
    assert all(c["identity"] == "owner" for c in lokaler_agent)


def test_strukturaenderung_ist_eine_transaktion(studio):
    _tabellen(studio)
    response = studio.post("/execute", {"operation": {"op": "alter_table", "name": "kunden", "actions": [
        {"action": "add_column", "column": {"name": "email", "type": "text"}},
        {"action": "add_column", "column": {"name": "email", "type": "text"}},
    ]}})
    assert response.status_code == 400
    details = studio.ok(studio.get("/table", schema="public", table="kunden"))
    assert "email" not in {c["name"] for c in details["columns"]}


def test_index_view_sequenz_enum_partition(studio):
    _tabellen(studio)
    studio.do({"op": "create_index", "table": "kunden", "name": "kunden_name_lower", "concurrently": True,
               "columns": [{"expression": "lower(name)"}], "where": "name <> ''"})
    studio.do({"op": "create_view", "name": "grosse", "materialized": True,
               "query": "SELECT kunde_id, sum(summe) AS total FROM bestellungen GROUP BY kunde_id;"})
    studio.do({"op": "refresh_materialized_view", "name": "grosse"})
    studio.do({"op": "create_sequence", "name": "rechnungsnr", "start": 1000})
    studio.do({"op": "restart_sequence", "name": "rechnungsnr", "value": 5000})
    studio.do({"op": "create_enum", "name": "stimmung", "values": ["gut", "schlecht"]})
    studio.do({"op": "add_enum_value", "name": "stimmung", "value": "mittel", "after": "gut"})
    studio.do({"op": "create_table", "name": "messwerte", "columns": [
        {"name": "zeit", "type": "date", "nullable": False}, {"name": "wert", "type": "int"}],
        "partition_by": {"strategy": "range", "columns": ["zeit"]}})
    studio.do({"op": "create_partition", "parent": "messwerte", "name": "messwerte_2026",
               "bounds": {"kind": "range", "from_values": ["2026-01-01"], "to_values": ["2027-01-01"]}})

    objects = studio.ok(studio.get("/objects", schema="public"))
    kinds = {r["name"]: r["kind"] for r in objects["relations"]}
    assert kinds["grosse"] == "materialized_view"
    assert kinds["messwerte"] == "partitioned_table"
    assert next(r for r in objects["relations"] if r["name"] == "messwerte_2026")["parent"] == "messwerte"
    assert objects["enums"] == [{"name": "stimmung", "values": ["gut", "mittel", "schlecht"]}]
    table = studio.ok(studio.get("/table", schema="public", table="messwerte"))
    assert table["partition_key"] == "RANGE (zeit)"
    assert table["partitions"][0]["name"] == "messwerte_2026"
    index = next(i for i in studio.ok(studio.get("/table", schema="public", table="kunden"))["indexes"]
                 if i["name"] == "kunden_name_lower")
    assert index["valid"] and "lower(name)" in index["definition"]


def test_funktion_trigger_und_funktionstest(studio):
    _tabellen(studio)
    studio.do({"op": "alter_table", "name": "kunden", "actions": [
        {"action": "add_column", "column": {"name": "geaendert", "type": "timestamptz"}}]})
    studio.do({"op": "create_function", "name": "stempel", "returns": "trigger",
               "body": "BEGIN NEW.geaendert := now(); RETURN NEW; END;"})
    studio.do({"op": "create_trigger", "table": "kunden", "name": "kunden_stempel", "timing": "before",
               "events": ["insert", "update"], "function_name": "stempel"})
    # Funktionstest mit Rollback: Trigger feuert, danach ist nichts gespeichert.
    result = studio.ok(studio.post("/sql", {
        "sql": "INSERT INTO kunden (name) VALUES ('probe'); SELECT geaendert IS NOT NULL AS gestempelt FROM kunden",
        "rollback": True,
    }))
    assert result["results"][1]["rows"] == [[True]] and result["rolled_back"]
    assert studio.ok(studio.post("/rows", {"table": "kunden"}))["total"] == 0

    objects = studio.ok(studio.get("/objects", schema="public"))
    function = next(f for f in objects["functions"] if f["name"] == "stempel")
    definition = studio.ok(studio.get(f"/functions/{function['oid']}"))
    assert "NEW.geaendert := now()" in definition["definition"]
    assert objects["triggers"][0]["name"] == "kunden_stempel"
    studio.do({"op": "set_trigger_enabled", "table": "kunden", "name": "kunden_stempel", "enabled": False})
    assert studio.ok(studio.get("/objects", schema="public"))["triggers"][0]["enabled"] is False


# ── Daten-Grid ─────────────────────────────────────────────────────────────


def test_datengrid_einfuegen_filtern_bearbeiten_loeschen(studio):
    _tabellen(studio)
    for name in ["Anna", "Bert", "Carla"]:
        studio.ok(studio.post("/rows/insert", {"table": "kunden", "values": {"name": name, "daten": {"stufe": 1}}}))
    page = studio.ok(studio.post("/rows", {
        "table": "kunden", "filters": [{"column": "name", "operator": "ilike", "value": "%a%"}],
        "sort": [{"column": "name", "descending": True}], "limit": 25,
    }))
    assert [r[page["columns"].index("name")] for r in page["rows"]] == ["Carla", "Anna"]
    assert page["total"] == 2 and page["key"] == ["id"] and page["editable"]

    anna_id = page["rows"][1][page["columns"].index("id")]
    updated = studio.ok(studio.post("/rows/update", {
        "table": "kunden", "key": {"values": {"id": anna_id}},
        "changes": {"daten": {"stufe": 2, "tags": ["vip"]}},
    }))
    assert updated["row"][updated["columns"].index("daten")] == {"stufe": 2, "tags": ["vip"]}
    contains = studio.ok(studio.post("/rows", {"table": "kunden", "filters": [
        {"column": "daten", "operator": "contains", "value": {"tags": ["vip"]}}]}))
    assert contains["total"] == 1

    studio.ok(studio.post("/rows/insert", {"table": "bestellungen", "values": {"kunde_id": anna_id, "summe": "12.50"}}))
    bad = studio.post("/rows/insert", {"table": "bestellungen", "values": {"kunde_id": anna_id, "summe": "-1"}})
    assert bad.status_code == 400 and "23514" in bad.text  # CHECK verletzt

    ids = [r[page["columns"].index("id")] for r in page["rows"]]
    deleted = studio.ok(studio.post("/rows/delete", {"table": "kunden", "keys": [{"values": {"id": i}} for i in ids]}))
    assert deleted == {"deleted": 2}
    # ON DELETE CASCADE hat die Bestellung mitgenommen.
    assert studio.ok(studio.post("/rows", {"table": "bestellungen"}))["total"] == 0


def test_tabelle_ohne_schluessel_und_seltsame_namen(studio):
    studio.do({"op": "create_table", "name": "log 100%", "columns": [
        {"name": 'wert "roh"', "type": "text"}, {"name": "prozent%", "type": "int"}]})
    studio.ok(studio.post("/rows/insert", {"table": "log 100%", "values": {'wert "roh"': "a", "prozent%": 5}}))
    page = studio.ok(studio.post("/rows", {"table": "log 100%", "filters": [
        {"column": "prozent%", "operator": "gte", "value": 5}]}))
    assert page["key"] == ["__msm_ctid"] and page["total"] == 1
    ctid = page["rows"][0][page["columns"].index("__msm_ctid")]
    studio.ok(studio.post("/rows/update", {"table": "log 100%", "key": {"values": {"__msm_ctid": ctid}},
                                           "changes": {"prozent%": 7}}))
    page = studio.ok(studio.post("/rows", {"table": "log 100%"}))
    assert page["rows"][0][page["columns"].index("prozent%")] == 7


def test_import_ist_atomar_und_export_in_drei_formaten(studio):
    _tabellen(studio)
    rows = [[f"kunde-{i}", '{"n": %d}' % i] for i in range(1200)]
    result = studio.ok(studio.post("/rows/import", {"table": "kunden", "columns": ["name", "daten"], "rows": rows}))
    assert result == {"inserted": 1200}
    doppelt = studio.post("/rows/import", {"table": "kunden", "columns": ["name", "daten"],
                                           "rows": [["neu-1", "{}"], ["kunde-5", "{}"]]})
    assert doppelt.status_code == 400
    assert studio.ok(studio.post("/rows", {"table": "kunden"}))["total"] == 1200  # neu-1 zurückgerollt

    csv_resp = studio.post("/rows/export", {"table": "kunden", "format": "csv"})
    assert csv_resp.status_code == 200
    lines = csv_resp.text.strip().splitlines()
    assert lines[0] == "id,name,daten" and len(lines) == 1201
    json_resp = studio.post("/rows/export", {"table": "kunden", "format": "json",
                                             "filters": [{"column": "name", "operator": "eq", "value": "kunde-7"}]})
    assert json_resp.json()[0]["daten"] == {"n": 7}
    sql_resp = studio.post("/rows/export", {"table": "kunden", "format": "sql",
                                            "filters": [{"column": "name", "operator": "in", "value": ["kunde-1", "kunde-2"]}]})
    assert sql_resp.text.count('INSERT INTO "public"."kunden"') == 2
    assert sql_resp.headers["x-msm-export-truncated"] == "0"


# ── Abfragen, Überwachung, Wartung ─────────────────────────────────────────


def test_explain_analyze_verwirft_die_wirkung(studio):
    _tabellen(studio)
    studio.ok(studio.post("/rows/insert", {"table": "kunden", "values": {"name": "bleibt"}}))
    plan = studio.ok(studio.post("/explain", {"sql": "DELETE FROM kunden", "analyze": True}))
    assert plan["plan"][0]["Plan"]["Node Type"] == "ModifyTable"
    assert "Actual Rows" in plan["plan"][0]["Plan"]
    assert studio.ok(studio.post("/rows", {"table": "kunden"}))["total"] == 1
    ohne = studio.ok(studio.post("/explain", {"sql": "SELECT * FROM kunden WHERE id = 1"}))
    assert ohne["analyzed"] is False


def test_sitzungen_sehen_und_beenden(studio, app_db):
    blocker = _connect(app_db, "app", "app_owner", OWNER_PW)
    with blocker.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        pid = cur.fetchone()[0]
        cur.execute("CREATE TABLE sperre (x int)")  # offene Transaktion hält die Sperre
    try:
        sessions = studio.ok(studio.get("/sessions"))
        mine = next(s for s in sessions if s["pid"] == pid)
        assert mine["state"] == "idle in transaction" and mine["role"] == "app_owner"
        assert any(lock["pid"] == pid for lock in studio.ok(studio.get("/locks")))
        assert studio.ok(studio.post(f"/sessions/{pid}", {"action": "terminate"})) == {"ok": True}
    finally:
        try:
            blocker.close()
        except psycopg2.Error:
            pass


def test_geteilter_cluster_sieht_keine_fremden_sitzungen(studio_shared, app_db, db):
    fremd = _connect(app_db, "fremd")
    with fremd.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        pid = cur.fetchone()[0]
    try:
        assert all(s["pid"] != pid for s in studio_shared.ok(studio_shared.get("/sessions")))
        response = studio_shared.post(f"/sessions/{pid}", {"action": "terminate"})
        assert response.status_code == 400
        with fremd.cursor() as cur:
            cur.execute("SELECT 1")  # lebt noch
        eintrag = db.query(AuditLog).filter(AuditLog.action.like("postgres.studio.session_%")).count()
        assert eintrag == 0
    finally:
        fremd.close()


def test_gesundheit_und_wartung(studio):
    _tabellen(studio)
    studio.ok(studio.post("/rows/import", {"table": "kunden", "columns": ["name"],
                                           "rows": [[f"n{i}"] for i in range(50)]}))
    studio.do({"op": "vacuum", "table": "kunden", "analyze": True})
    studio.do({"op": "reindex", "target": "table", "name": "kunden", "concurrently": True})
    health = studio.ok(studio.get("/health"))
    assert health["database"]["size_bytes"] > 0
    assert any(t["table"] == "kunden" for t in health["tables"])


# ── Erweiterungen, Sicherheit, Instanz ─────────────────────────────────────


def test_extensions_geteilt_nur_trusted(studio_shared):
    liste = studio_shared.ok(studio_shared.get("/extensions"))
    by_name = {e["name"]: e for e in liste}
    assert by_name["pg_trgm"]["trusted"] and by_name["pg_trgm"]["installable"]
    assert not by_name["pageinspect"]["installable"]
    studio_shared.do({"op": "create_extension", "name": "pg_trgm"})
    assert next(e for e in studio_shared.ok(studio_shared.get("/extensions")) if e["name"] == "pg_trgm")["installed_version"]
    response = studio_shared.post("/execute", {"operation": {"op": "create_extension", "name": "pageinspect"}})
    assert response.status_code == 400


def test_eigene_instanz_installiert_auch_untrusted(studio):
    studio.do({"op": "create_extension", "name": "pageinspect"})


def test_rls_richtlinie_und_rechte(studio_shared):
    s = studio_shared
    _tabellen(s)
    s.do({"op": "set_rls", "table": "kunden", "enabled": True})
    s.do({"op": "create_policy", "table": "kunden", "name": "nur_eigene", "command": "select",
          "roles": ["app_owner_app"], "using": "name = current_user"})
    s.do({"op": "grant", "object_type": "table", "objects": ["kunden"], "privileges": ["SELECT", "INSERT"],
          "roles": ["app_owner_app"]})
    details = s.ok(s.get("/table", schema="public", table="kunden"))
    assert details["rls_enabled"] is True
    assert details["policies"][0] == {"name": "nur_eigene", "command": "select", "permissive": True,
                                      "roles": ["app_owner_app"], "using": "(name = CURRENT_USER)", "with_check": None}
    assert {("app_owner_app", "SELECT"), ("app_owner_app", "INSERT")} <= {(g["grantee"], g["privilege"]) for g in details["grants"]}
    matrix = s.ok(s.get("/grants", schema="public"))
    assert any(o["object"] == "kunden" and o["grantee"] == "app_owner_app" for o in matrix["objects"])
    # Fremde Rollen des geteilten Clusters sind nicht adressierbar.
    fremd = s.post("/execute", {"operation": {"op": "grant", "object_type": "table", "objects": ["kunden"],
                                              "privileges": ["SELECT"], "roles": ["fremder_owner"]}})
    assert fremd.status_code == 400 and "Unbekannte Rolle" in fremd.text
    assert {r["name"] for r in s.ok(s.get("/roles"))} == {"app_owner", "app_owner_app"}


def test_rollen_nur_in_eigener_instanz(studio, studio_shared, app_db):
    geheim = "leser-" + "passwort-9"
    op = {"op": "create_role", "name": "leser", "attributes": {"login": True, "password": geheim},
          "member_of": ["pg_read_all_data"]}
    preview = studio.ok(studio.post("/preview", {"operation": op}))
    assert geheim not in str(preview) and preview["identity"] == "admin"
    result = studio.ok(studio.post("/execute", {"operation": op}))
    assert geheim not in str(result)
    _connect(app_db, "msm_control", "leser", geheim).close()  # Login klappt

    for bad in (
        {"op": "create_role", "name": "x1", "member_of": ["pg_execute_server_program"]},
        {"op": "create_role", "name": "x2", "member_of": ["msm_admin"]},
        {"op": "alter_role", "name": "app_owner", "attributes": {"password": "neu-" + "passwort-1"}},
        {"op": "drop_role", "name": "msm_admin"},
        {"op": "create_role", "name": "msm_hinterher"},
    ):
        assert studio.post("/execute", {"operation": bad}).status_code == 400, bad
    assert studio_shared.post("/execute", {"operation": op}).status_code == 400
    roles = {r["name"]: r for r in studio.ok(studio.get("/roles"))}
    assert roles["leser"]["member_of"] == ["pg_read_all_data"]
    assert roles["app_owner"]["managed"] and roles["msm_admin"]["system"]


def test_parameter_setzen_nur_in_eigener_instanz(studio, studio_shared):
    studio.do({"op": "set_parameter", "name": "work_mem", "value": "12MB"})
    params = {p["name"]: p for p in studio.ok(studio.get("/parameters"))}
    assert params["work_mem"]["setting"] == str(12 * 1024)
    assert params["archive_command"]["editable"] is False
    unbekannt = studio.post("/execute", {"operation": {"op": "set_parameter", "name": "gibt_es_nicht", "value": "1"}})
    assert unbekannt.status_code == 400
    assert studio_shared.get("/parameters").status_code == 400
    studio.do({"op": "reset_parameter", "name": "work_mem"})


def test_rechte_greifen(client, user_cookies, user_csrf_token, dedicated):
    s = Studio(client, user_cookies, user_csrf_token, dedicated)
    assert s.get("").status_code == 404
    assert s.post("/execute", {"operation": {"op": "create_schema", "name": "x"}}).status_code == 404


def test_ausfuehrung_wird_ohne_sql_auditiert(studio, db):
    geheim = "audit-" + "passwort-7"
    studio.do({"op": "create_role", "name": "auditrolle", "attributes": {"password": geheim}})
    eintrag = db.query(AuditLog).filter(AuditLog.action == "postgres.studio.execute").one()
    assert "create_role" in (eintrag.details or "") and geheim not in (eintrag.details or "")
