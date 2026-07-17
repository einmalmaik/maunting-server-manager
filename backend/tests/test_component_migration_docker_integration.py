"""Opt-in PostgreSQL cutover integration for the component migration.

Run explicitly with:
    MSM_RUN_DOCKER_INTEGRATION=1 pytest -q \
        tests/test_component_migration_docker_integration.py
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from scripts.prepare_component_migration import merge_target_environment


pytestmark = pytest.mark.skipif(
    os.getenv("MSM_RUN_DOCKER_INTEGRATION") != "1",
    reason="set MSM_RUN_DOCKER_INTEGRATION=1 for the Docker cutover test",
)


def _docker(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=True,
    )


def _start_postgres(name: str) -> None:
    _docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        name,
        "--env",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        "--env",
        "POSTGRES_USER=msm",
        "--env",
        "POSTGRES_DB=msm",
        "postgres:17-alpine",
    )
    for _ in range(45):
        ready = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-U", "msm", "-d", "msm"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ready.returncode == 0:
            return
        import time

        time.sleep(1)
    raise RuntimeError(f"PostgreSQL container {name} did not become ready")


def _psql(name: str, sql: str) -> str:
    result = _docker(
        "exec",
        name,
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "-U",
        "msm",
        "-d",
        "msm",
        "-c",
        sql,
    )
    return result.stdout.decode("utf-8").strip()


def test_real_postgres_dump_restore_and_environment_cutover(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    suffix = uuid.uuid4().hex[:10]
    source = f"msm-migration-source-{suffix}"
    target = f"msm-migration-target-{suffix}"

    try:
        _start_postgres(source)
        _start_postgres(target)
        _psql(
            source,
            """
            CREATE TABLE migration_probe (id integer PRIMARY KEY, kind text NOT NULL, payload text NOT NULL);
            INSERT INTO migration_probe VALUES
              (1, 'owner', 'owner-account-and-settings'),
              (2, 'node', 'encrypted-node-token-metadata'),
              (3, 'server', 'mods-workshop-backup-blueprint-metadata');
            """,
        )
        _psql(
            target,
            "CREATE TABLE migration_probe (id integer PRIMARY KEY, kind text, payload text); "
            "INSERT INTO migration_probe VALUES (99, 'stale', 'must-be-replaced');",
        )

        failed_restore = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                target,
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--role=msm",
                "-U",
                "msm",
                "--dbname=msm",
            ],
            input=b"not-a-postgresql-dump",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        assert failed_restore.returncode != 0
        assert _psql(target, "SELECT payload FROM migration_probe WHERE id=99;") == "must-be-replaced"
        assert _psql(source, "SELECT count(*) FROM migration_probe;") == "3"

        dump = _docker(
            "exec",
            source,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "-U",
            "msm",
            "-d",
            "msm",
        ).stdout
        assert len(dump) > 100

        _docker(
            "exec",
            "--interactive",
            target,
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--role=msm",
            "-U",
            "msm",
            "--dbname=msm",
            input_bytes=dump,
        )
        rows = _psql(target, "SELECT id || ':' || kind || ':' || payload FROM migration_probe ORDER BY id;")
        assert rows.splitlines() == [
            "1:owner:owner-account-and-settings",
            "2:node:encrypted-node-token-metadata",
            "3:server:mods-workshop-backup-blueprint-metadata",
        ]
        assert "must-be-replaced" not in rows

        source_env = tmp_path / "source.env"
        target_env = tmp_path / "target.env"
        merged_env = tmp_path / "merged.env"
        dis_env = tmp_path / "dis.env"
        source_env.write_text(
            "\n".join(
                [
                    'MSM_SECRET_KEY="integration-source-secret-value"',
                    'MSM_DIS_SALT="integration-source-dis-salt-value"',
                    'MSM_DIS_SIDECAR_TOKEN="integration-source-sidecar-token"',
                    'MSM_DATABASE_URL="postgresql+psycopg2://msm@source/msm"',
                    'MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://msm@source/msm"',
                    "MSM_LOCAL_AGENT_ENABLED=true",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        target_env.write_text(
            "\n".join(
                [
                    'MSM_DATABASE_URL="postgresql+psycopg2://msm@target/msm"',
                    'MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://msm@target/msm"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        merge_target_environment(
            source_env=source_env,
            target_env=target_env,
            output_env=merged_env,
            dis_output=dis_env,
            api_origin="https://api.example.com",
            frontend_origin="https://panel.example.net",
        )
        merged = merged_env.read_text(encoding="utf-8")
        dis = dis_env.read_text(encoding="utf-8")
        assert "@target/msm" in merged
        assert "@source/msm" not in merged
        assert "integration-source-secret-value" in merged and "integration-source-secret-value" in dis
        assert 'MSM_PANEL_URL="https://panel.example.net"' in merged
        assert 'MSM_API_URL="https://api.example.com"' in merged
        assert "MSM_LOCAL_AGENT_ENABLED=false" in merged
    finally:
        subprocess.run(
            ["docker", "rm", "--force", source, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
