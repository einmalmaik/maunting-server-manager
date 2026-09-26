"""Studio-Ausfuehrung und Instanzziel gegen ein echtes PostgreSQL.

Laeuft nur mit Docker. Der Container heisst wie ein Datenbankserver
(``msm-srv-…``) und lauscht innen auf demselben Port wie aussen — genau so
startet die Blueprint ``postgres`` eine eigene Instanz.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

import pytest

from services.postgres_service import (
    PostgresAgentError,
    dump_database,
    dump_databases,
    restore_database,
    run_statements,
    ziel,
)

ADMIN_PW = "pruef-" + "admin-" + "passwort"
NAME = f"msm-srv-pgtest-{os.getpid()}"


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="module")
def instanz():
    if not _docker_ok():
        pytest.skip("Docker nicht verfuegbar")
    if os.name == "nt":
        # Der Agent kennt sonst nur den Linux-Socket; der Dump spricht das SDK.
        os.environ.setdefault("DOCKER_HOST", "npipe:////./pipe/docker_engine")
    port = _freier_port()
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    started = subprocess.run(
        [
            "docker", "run", "-d", "--name", NAME,
            "-e", "POSTGRES_USER=msm_admin", "-e", f"POSTGRES_PASSWORD={ADMIN_PW}",
            "-e", "POSTGRES_DB=msm_control",
            "-p", f"127.0.0.1:{port}:{port}",
            "postgres:17", "postgres", "-p", str(port),
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"postgres:17 startet nicht: {started.stderr[:200]}")
    target = {"host": "127.0.0.1", "port": port, "container": NAME}
    deadline = time.monotonic() + 90
    while True:
        try:
            with ziel(target):
                run_statements(
                    database_name="msm_control", identity="admin", owner_role="",
                    owner_password="", admin_password=ADMIN_PW, mode="read",
                    statements=[{"sql": "SELECT 1"}], row_limit=1, timeout_ms=2000,
                )
            break
        except Exception:
            if time.monotonic() > deadline:
                subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
                raise
            time.sleep(1)
    yield target
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def _run(target, statements, *, mode="read", rollback=False, identity="admin"):
    with ziel(target):
        return run_statements(
            database_name="msm_control",
            identity=identity,
            owner_role="",
            owner_password="",
            admin_password=ADMIN_PW,
            mode=mode,
            statements=[{"sql": sql, "params": params} for sql, params in statements],
            row_limit=100,
            timeout_ms=5000,
            rollback=rollback,
        )


def test_ziel_nimmt_nur_adressen_dieses_hosts():
    with pytest.raises(PostgresAgentError):
        with ziel({"host": "192.0.2.10", "port": 5432, "container": "msm-srv-1"}):
            pass


def test_ziel_nimmt_nur_servercontainer():
    with pytest.raises(PostgresAgentError):
        with ziel({"host": "127.0.0.1", "port": 5432, "container": "msm-postgres-fremd"}):
            pass


def test_lesemodus_verweigert_schreiben(instanz):
    with pytest.raises(PostgresAgentError, match="25006"):
        _run(instanz, [("CREATE TABLE verboten (id int)", None)])


def test_transaktion_nimmt_alles_zurueck(instanz):
    with pytest.raises(PostgresAgentError, match="Statement 2"):
        _run(
            instanz,
            [("CREATE TABLE halb (id int)", None), ("SELECT * FROM gibt_es_nicht", None)],
            mode="tx",
        )
    rows = _run(instanz, [("SELECT to_regclass('public.halb') IS NULL", None)])["results"][0]["rows"]
    assert rows == [[True]]


def test_rollback_verwirft_trotz_erfolg(instanz):
    _run(instanz, [("CREATE TABLE nur_probe (id int)", None)], mode="tx", rollback=True)
    rows = _run(instanz, [("SELECT to_regclass('public.nur_probe') IS NULL", None)])["results"][0]["rows"]
    assert rows == [[True]]


def test_autocommit_erlaubt_concurrently(instanz):
    _run(instanz, [("CREATE TABLE idx_probe (id int)", None)], mode="tx")
    _run(instanz, [("CREATE INDEX CONCURRENTLY idx_probe_id ON idx_probe (id)", None)], mode="autocommit")
    rows = _run(
        instanz, [("SELECT count(*) FROM pg_indexes WHERE indexname = %s", ["idx_probe_id"])]
    )["results"][0]["rows"]
    assert rows == [[1]]


def test_werte_kommen_ohne_genauigkeitsverlust(instanz):
    result = _run(
        instanz,
        [(
            "SELECT 12345678901234567890.123::numeric, '\\xdead'::bytea, "
            "'2026-09-26 12:00:00+00'::timestamptz, '{\"a\": [1, 2]}'::jsonb, NULL",
            None,
        )],
    )["results"][0]
    zahl, bytes_, zeit, json_, leer = result["rows"][0]
    assert zahl == "12345678901234567890.123"
    assert bytes_ == "\\xdead"
    assert zeit.startswith("2026-09-26T12:00:00")
    assert json_ == {"a": [1, 2]}
    assert leer is None


def test_zeilen_werden_gekappt_und_gemeldet(instanz):
    with ziel(instanz):
        result = run_statements(
            database_name="msm_control", identity="admin", owner_role="", owner_password="",
            admin_password=ADMIN_PW, mode="read",
            statements=[{"sql": "SELECT generate_series(1, 20)"}], row_limit=5, timeout_ms=2000,
        )["results"][0]
    assert len(result["rows"]) == 5
    assert result["truncated"] is True


def test_hinweise_kommen_mit(instanz):
    result = _run(instanz, [("DO $$ BEGIN RAISE NOTICE 'hallo studio'; END $$", None)], mode="tx")
    assert any("hallo studio" in n for n in result["notices"])


def test_dump_laeuft_in_der_eigenen_instanz(instanz):
    _run(instanz, [("CREATE TABLE dump_probe (id int)", None)], mode="tx")
    with ziel(instanz):
        dumps = dump_databases(admin_password=ADMIN_PW, database_names=["msm_control"])
    assert "dump_probe" in dumps["msm_control"]


EIGNER_PW = "eigner-" + "passwort-" + "1"


@pytest.fixture
def quelle_und_ziel(instanz):
    """Quelle mit Daten, leere Ziel-DB, die einem Owner ohne Sonderrechte gehoert."""
    _run(instanz, [("DROP DATABASE IF EXISTS ziel", None)], mode="autocommit")
    _run(instanz, [("DROP DATABASE IF EXISTS quelle", None)], mode="autocommit")
    _run(instanz, [("DROP ROLE IF EXISTS eigner", None)], mode="autocommit")
    _run(instanz, [
        (f"CREATE ROLE eigner LOGIN PASSWORD '{EIGNER_PW}'", None),
    ], mode="autocommit")
    _run(instanz, [("CREATE DATABASE quelle", None)], mode="autocommit")
    _run(instanz, [("CREATE DATABASE ziel OWNER eigner", None)], mode="autocommit")
    with ziel(instanz):
        run_statements(
            database_name="quelle", identity="admin", owner_role="", owner_password="",
            admin_password=ADMIN_PW, mode="tx", row_limit=10, timeout_ms=5000,
            statements=[
                {"sql": 'CREATE TABLE "Kunden %" (id int PRIMARY KEY, name text)'},
                {"sql": "INSERT INTO \"Kunden %\" VALUES (1, 'Anna'), (2, 'Bert')"},
                {"sql": "CREATE TABLE andere (x int)"},
            ],
        )
    return instanz


def _zaehle(target, table):
    with ziel(target):
        return run_statements(
            database_name="ziel", identity="owner", owner_role="eigner", owner_password=EIGNER_PW,
            admin_password="", mode="read", row_limit=10, timeout_ms=5000,
            statements=[{"sql": f"SELECT count(*) FROM {table}"}],
        )["results"][0]["rows"][0][0]


@pytest.mark.parametrize("fmt", ["plain", "custom", "tar"])
def test_dump_und_restore_je_format_als_owner(quelle_und_ziel, fmt):
    target = quelle_und_ziel
    with ziel(target):
        data = dump_database(admin_password=ADMIN_PW, database_name="quelle", fmt=fmt,
                             tables=[("public", "Kunden %")])
        if fmt == "custom":
            assert data.startswith(b"PGDMP")
        restore_database(database_name="ziel", owner_role="eigner", owner_password=EIGNER_PW,
                         fmt=fmt, data=data)
    assert _zaehle(target, '"Kunden %"') == 2
    # Nur die gewaehlte Tabelle kam mit.
    with pytest.raises(PostgresAgentError, match="42P01"):
        _zaehle(target, "andere")


def test_nur_schema_ohne_daten(quelle_und_ziel):
    target = quelle_und_ziel
    with ziel(target):
        data = dump_database(admin_password=ADMIN_PW, database_name="quelle", schema_only=True)
        assert b"Anna" not in data
        restore_database(database_name="ziel", owner_role="eigner", owner_password=EIGNER_PW,
                         fmt="plain", data=data)
    assert _zaehle(target, '"Kunden %"') == 0


def test_restore_ist_alles_oder_nichts_und_ohne_psql_befehle(quelle_und_ziel):
    target = quelle_und_ziel
    with ziel(target):
        with pytest.raises(PostgresAgentError):
            restore_database(database_name="ziel", owner_role="eigner", owner_password=EIGNER_PW, fmt="plain",
                             data=b"CREATE TABLE halb (x int);\nSELECT * FROM gibt_es_nicht;\n")
        with pytest.raises(PostgresAgentError, match="meta-commands"):
            restore_database(database_name="ziel", owner_role="eigner", owner_password=EIGNER_PW, fmt="plain",
                             data=b"\\! id\n")
    with pytest.raises(PostgresAgentError, match="42P01"):
        _zaehle(target, "halb")
