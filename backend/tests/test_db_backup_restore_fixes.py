"""Tests fuer die M5 Backup-Fixes (VAL-FIX-007, VAL-FIX-008, VAL-FIX-009).

Deckt die drei P1-Issues ab:
- VAL-FIX-007: DB-Dump-Fehler fuehrt zu hartem Backup-Fehlschlag (kein S3-Upload)
- VAL-FIX-008: DB-Restore-Fehler wird an API zurueckgegeben (nicht nur geloggt)
- VAL-FIX-009: Mehrere DBs bekommen separate Dumps, jede wird in korrekte DB restored
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from models import Backup, PostgresDatabase, Server


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_pg_db(db: Session, server_id: int, name: str, *, index: int = 1) -> PostgresDatabase:
    """Erzeugt eine PostgresDatabase-Row ohne echtes Provisioning."""
    pg = PostgresDatabase(
        server_id=server_id,
        name=name,
        owner_role=f"msm_s{server_id}_o{index}",
        owner_password_encrypted=(
            f"test-enc-v1:{'msm:pg:db:owner'.encode().hex()}:{'dummy'.encode().hex()}"
        ),
        is_superuser=False,
    )
    db.add(pg)
    db.commit()
    db.refresh(pg)
    return pg


def _make_fake_tar(archive_path: str, install_dir: str) -> None:
    """Erzeugt ein minimales tar.gz mit install_dir-Inhalt + Manifest."""
    from services.backup_paths import build_manifest, BACKUP_MANIFEST_ARCNAME
    import json

    os.makedirs(os.path.dirname(archive_path) or ".", exist_ok=True)
    manifest = build_manifest("full", server_id=1)
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    with tarfile.open(archive_path, "w:gz") as tar:
        info = tarfile.TarInfo(name=BACKUP_MANIFEST_ARCNAME)
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # Dummy install_dir-Datei
        dummy = b"hello"
        info2 = tarfile.TarInfo(name="world.dat")
        info2.size = len(dummy)
        tar.addfile(info2, io.BytesIO(dummy))


# ── VAL-FIX-007: DB-Dump-Fehler → Backup schlaegt fehl ──────────────────


class TestValFix007DbDumpFailure:
    """DB-Dump-Fehler bei Servern mit aktiven Postgres-DBs fuehrt zu Backup-Fehler."""

    def test_pg_dump_failure_raises_and_no_backup_record(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Wenn backup_pg_dump_for_archive fehlschlaegt, schlaegt das Backup fehl."""
        from services.backup_service import run_backup

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")
        test_server.install_dir = str(install)
        db.commit()

        # Server hat aktive Postgres-DBs
        _make_pg_db(db, test_server.id, "msm_s1_db1")

        before = db.query(Backup).filter(Backup.server_id == test_server.id).count()

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.create_full_backup_tar") as subp, \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.backup_plan_for_server", return_value=MagicMock(scope="full")), \
             patch("services.postgres_service.backup_pg_dump_for_archive",
                   side_effect=RuntimeError("pg_dump fehlgeschlagen")):
            with pytest.raises(RuntimeError, match="Backup fehlgeschlagen"):
                run_backup(test_server.id, db, timeout_seconds=30)

            # create_full_backup_tar darf NICHT aufgerufen worden sein
            subp.assert_not_called()

        after = db.query(Backup).filter(Backup.server_id == test_server.id).count()
        assert after == before, "Kein Backup-Record bei pg_dump-Fehler"

    def test_pg_dump_success_proceeds_normally(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Bei funktionierendem pg_dump wird das Backup normal erstellt."""
        from services.backup_service import run_backup

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")
        test_server.install_dir = str(install)
        db.commit()

        _make_pg_db(db, test_server.id, "msm_s1_db1")

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.create_full_backup_tar") as subp, \
             patch("services.backup_service.os.path.getsize", return_value=1), \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.backup_plan_for_server", return_value=MagicMock(scope="full")), \
             patch("services.postgres_service.backup_pg_dump_for_archive",
                   return_value={"msm_s1_db1": b"-- SQL"}):
            backup = run_backup(test_server.id, db, timeout_seconds=30)
            assert backup is not None
            subp.assert_called_once()

    def test_no_pg_dbs_proceeds_without_dump(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Server ohne Postgres-DBs: Backup funktioniert ohne pg_dump."""
        from services.backup_service import run_backup

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")
        test_server.install_dir = str(install)
        db.commit()

        # Keine PostgresDatabase-Rows → has_pg=False

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.create_full_backup_tar") as subp, \
             patch("services.backup_service.os.path.getsize", return_value=1), \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.backup_plan_for_server", return_value=MagicMock(scope="full")), \
             patch("services.postgres_service.backup_pg_dump_for_archive") as pg_mock:
            backup = run_backup(test_server.id, db, timeout_seconds=30)
            assert backup is not None
            pg_mock.assert_not_called()


# ── VAL-FIX-008: DB-Restore-Fehler → API gibt Fehler zurueck ────────────


class TestValFix008DbRestoreError:
    """DB-Restore-Fehler wird an API gemeldet, Server nicht als erfolgreich markiert."""

    def test_restore_db_failure_returns_error_and_sets_error_status(
        self, client, owner_user, owner_cookies, csrf_token, test_server, db, tmp_path
    ):
        """Bei DB-Restore-Fehler: API gibt 500, Server-Status == error."""
        from models import ServerPermission

        perm = ServerPermission(user_id=owner_user.id, server_id=test_server.id,
                                permission_key="server.backups.restore")
        db.add(perm)
        db.commit()

        install = tmp_path / "install"
        install.mkdir()
        (install / "old.txt").write_text("old")
        test_server.install_dir = str(install)
        test_server.status = "running"
        db.commit()

        backup_file = tmp_path / "backup.tar.gz"
        _make_fake_tar(str(backup_file), str(install))

        b = Backup(server_id=test_server.id, filename=str(backup_file), size_mb=1)
        db.add(b)
        db.commit()
        db.refresh(b)

        with patch("services.docker_service.is_running", return_value=False), \
             patch("services.docker_service.remove"), \
             patch("services.backup_paths.read_pg_dump_from_archive",
                   return_value={"msm_s1_db1": b"-- SQL"}), \
             patch("services.postgres_service.restore_pg_dump_from_archive",
                   side_effect=RuntimeError("Restore fehlgeschlagen: connection refused")):
            resp = client.post(
                f"/api/backups/{test_server.id}/restore/{b.id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert resp.status_code == 500
        db.refresh(test_server)
        assert test_server.status == "error"
        assert test_server.status_message is not None

    def test_restore_db_success_sets_stopped_status(
        self, client, owner_user, owner_cookies, csrf_token, test_server, db, tmp_path
    ):
        """Bei funktionierendem DB-Restore: Server-Status == stopped."""
        from models import ServerPermission

        perm = ServerPermission(user_id=owner_user.id, server_id=test_server.id,
                                permission_key="server.backups.restore")
        db.add(perm)
        db.commit()

        install = tmp_path / "install"
        install.mkdir()
        (install / "old.txt").write_text("old")
        test_server.install_dir = str(install)
        test_server.status = "running"
        db.commit()

        backup_file = tmp_path / "backup.tar.gz"
        _make_fake_tar(str(backup_file), str(install))

        b = Backup(server_id=test_server.id, filename=str(backup_file), size_mb=1)
        db.add(b)
        db.commit()
        db.refresh(b)

        with patch("services.docker_service.is_running", return_value=False), \
             patch("services.docker_service.remove"), \
             patch("services.backup_paths.read_pg_dump_from_archive",
                   return_value={"msm_s1_db1": b"-- SQL"}), \
             patch("services.postgres_service.restore_pg_dump_from_archive",
                   return_value={"ok": True, "databases": ["msm_s1_db1"], "duration_ms": 5}):
            resp = client.post(
                f"/api/backups/{test_server.id}/restore/{b.id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert resp.status_code == 200
        db.refresh(test_server)
        assert test_server.status == "stopped"


# ── VAL-FIX-009: Separate Dumps pro DB, korrekt restored ────────────────


class TestValFix009PerDbDumps:
    """Mehrere DBs bekommen separate Dumps, jede wird in korrekte DB restored."""

    def test_backup_pg_dump_for_archive_returns_per_db_dict(self, db, test_server, tmp_path):
        """backup_pg_dump_for_archive liefert dict[db_name -> bytes] fuer mehrere DBs."""
        from services import postgres_service

        _make_pg_db(db, test_server.id, "db_alpha", index=1)
        _make_pg_db(db, test_server.id, "db_beta", index=2)

        mock_client = MagicMock()
        mock_client.postgres_dump.return_value = {
            "ok": True,
            "dumps": {
                "db_alpha": "-- dump for db_alpha\nCREATE TABLE t_db_alpha();\n",
                "db_beta": "-- dump for db_beta\nCREATE TABLE t_db_beta();\n",
            },
        }

        with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
             patch.object(postgres_service, "_admin_password", return_value="pw"):
            result = postgres_service.backup_pg_dump_for_archive(db, test_server.id)

        assert isinstance(result, dict)
        assert set(result.keys()) == {"db_alpha", "db_beta"}
        assert b"db_alpha" in result["db_alpha"]
        assert b"db_beta" in result["db_beta"]
        # Keine Cross-Kontamination im einzelnen Dump
        assert b"db_beta" not in result["db_alpha"]
        assert b"db_alpha" not in result["db_beta"]

    def test_backup_pg_dump_for_archive_no_dbs_returns_empty_dict(self, db, test_server):
        """Server ohne Postgres-DBs → leeres dict."""
        from services import postgres_service
        result = postgres_service.backup_pg_dump_for_archive(db, test_server.id)
        assert result == {}

    def test_create_full_backup_tar_writes_per_db_files(self, tmp_path):
        """create_full_backup_tar schreibt .msm/postgres/<name>.sql pro DB."""
        from services.backup_paths import create_full_backup_tar, BACKUP_POSTGRES_DIR

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")

        out = str(tmp_path / "backup.tar.gz")
        create_full_backup_tar(
            out,
            str(install),
            pg_dump_dict={"db_alpha": b"-- alpha SQL", "db_beta": b"-- beta SQL"},
            server_id=1,
        )

        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()

        assert f"{BACKUP_POSTGRES_DIR}/db_alpha.sql" in names
        assert f"{BACKUP_POSTGRES_DIR}/db_beta.sql" in names
        # Legacy-Pfad nicht mehr schreiben, wenn dict verwendet wird
        assert ".msm/postgres.sql" not in names

    def test_read_pg_dump_from_archive_returns_per_db_dict(self, tmp_path):
        """read_pg_dump_from_archive liest .msm/postgres/<name>.sql Dateien."""
        from services.backup_paths import create_full_backup_tar, read_pg_dump_from_archive

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")

        out = str(tmp_path / "backup.tar.gz")
        create_full_backup_tar(
            out,
            str(install),
            pg_dump_dict={"db_alpha": b"-- alpha SQL", "db_beta": b"-- beta SQL"},
            server_id=1,
        )

        result = read_pg_dump_from_archive(out)
        assert isinstance(result, dict)
        assert set(result.keys()) == {"db_alpha", "db_beta"}
        assert result["db_alpha"] == b"-- alpha SQL"
        assert result["db_beta"] == b"-- beta SQL"

    def test_read_pg_dump_from_archive_legacy_combined(self, tmp_path):
        """Legacy .msm/postgres.sql (kombinierter Dump mit Sektions-Markern) wird gelesen."""
        from services.backup_paths import read_pg_dump_from_archive, BACKUP_POSTGRES_ARCNAME
        import json

        combined = (
            "-- MSM Postgres Dump\n"
            "-- Server ID: 1\n"
            "\n"
            "-- ===== Database: db_alpha =====\n"
            "CREATE TABLE t_alpha();\n"
            "\n"
            "-- ===== Database: db_beta =====\n"
            "CREATE TABLE t_beta();\n"
        ).encode("utf-8")

        out = str(tmp_path / "legacy.tar.gz")
        with tarfile.open(out, "w:gz") as tar:
            info = tarfile.TarInfo(name=BACKUP_POSTGRES_ARCNAME)
            info.size = len(combined)
            tar.addfile(info, io.BytesIO(combined))

        result = read_pg_dump_from_archive(out)
        assert isinstance(result, dict)
        assert set(result.keys()) == {"db_alpha", "db_beta"}
        assert b"t_alpha" in result["db_alpha"]
        assert b"t_beta" not in result["db_alpha"]
        assert b"t_beta" in result["db_beta"]

    def test_read_pg_dump_from_archive_empty_when_no_pg(self, tmp_path):
        """Archiv ohne Postgres-Dumps → leeres dict."""
        from services.backup_paths import create_full_backup_tar, read_pg_dump_from_archive

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("data")

        out = str(tmp_path / "backup.tar.gz")
        create_full_backup_tar(out, str(install), server_id=1)

        result = read_pg_dump_from_archive(out)
        assert result == {}

    def test_restore_pg_dump_from_archive_restores_each_to_correct_db(self, db, test_server):
        """Jeder Dump wird nur in seine zugehoerige DB restored - keine Cross-Kontamination."""
        from services import postgres_service

        _make_pg_db(db, test_server.id, "db_alpha", index=1)
        _make_pg_db(db, test_server.id, "db_beta", index=2)

        dumps = {
            "db_alpha": b"CREATE TABLE t_alpha();",
            "db_beta": b"CREATE TABLE t_beta();",
        }
        captured: dict = {}

        mock_client = MagicMock()

        def capture_restore(*, admin_password, dumps, owners):
            captured["dumps"] = dict(dumps)
            captured["owners"] = owners
            return {"ok": True, "databases": list(dumps.keys()), "duration_ms": 1}

        mock_client.postgres_restore.side_effect = capture_restore

        with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
             patch.object(postgres_service, "_admin_password", return_value="pw"):
            result = postgres_service.restore_pg_dump_from_archive(
                db, test_server.id, dumps
            )

        assert result.get("ok") is True
        assert "db_alpha" in captured["dumps"]
        assert "db_beta" in captured["dumps"]
        assert "t_alpha" in captured["dumps"]["db_alpha"]
        assert "t_beta" in captured["dumps"]["db_beta"]
        assert "t_alpha" not in captured["dumps"]["db_beta"]
        assert "t_beta" not in captured["dumps"]["db_alpha"]
        assert captured["owners"]["db_alpha"]["owner_role"] == "msm_s1_o1"

    def test_restore_pg_dump_from_archive_skips_unknown_dbs(self, db, test_server):
        """Dumps fuer DBs die nicht mehr existieren werden uebersprungen."""
        from services import postgres_service

        _make_pg_db(db, test_server.id, "db_alpha", index=1)
        # db_beta existiert nicht mehr im Server, ist aber im Dump

        captured: dict = {}
        mock_client = MagicMock()

        def capture_restore(*, admin_password, dumps, owners):
            captured["dumps"] = dict(dumps)
            captured["owners"] = owners
            return {"ok": True, "databases": list(dumps.keys()), "duration_ms": 1}

        mock_client.postgres_restore.side_effect = capture_restore

        dumps = {"db_alpha": b"CREATE TABLE a();", "db_beta": b"CREATE TABLE b();"}

        with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
             patch.object(postgres_service, "_admin_password", return_value="pw"):
            result = postgres_service.restore_pg_dump_from_archive(
                db, test_server.id, dumps
            )

        assert result.get("ok") is True
        assert "db_alpha" in captured["dumps"]
        assert "db_beta" not in captured["dumps"]

    def test_restore_pg_dump_from_archive_empty_dict_skips(self, db, test_server):
        """Leeres dumps-dict → skipped, kein Fehler."""
        from services import postgres_service
        result = postgres_service.restore_pg_dump_from_archive(db, test_server.id, {})
        assert result.get("ok") is True
        assert result.get("skipped") is True
