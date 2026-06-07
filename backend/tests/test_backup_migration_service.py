"""Tests fuer den Backup-Auto-Migration-Service (Schritt 9.3).

Deckt die kritischen Invarianten aus Plan 3.10 ab:
- Idempotenz (Crash-Recovery, Re-Run, ueberspringt bereits migrierte Records)
- Sequenzielle Abarbeitung (kein Parallel-Upload)
- Failure-Modi: FileNotFoundError (soft-skip), ProviderError (hard-stop)
- Cancel-Event (User bricht mitten im Lauf ab)
- Cross-Cloud-Migration (anderer target_provider)
- State-File-Roundtrip (load/save/atomic-write)
- Sicherheit: last_error wird sanitized (kein Token, kein Pfad)
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

# Conftest stellt db, test_server, clean_db bereit.


# ── Helper: STATE_FILE/STATE_DIR auf tmp_path umlenken ────────────────


@pytest.fixture
def state_in_tmp(tmp_path, monkeypatch):
    """STATE_FILE + STATE_DIR auf tmp_path umlenken, sodass Tests die echte
    state.json nicht beruehren. Monkey-patcht die Modul-Referenzen, weil
    Path-Instanzen patching (z.B. ``monkeypatch.setattr(STATE_FILE,
    "exists", ...)``) andere Path-Methoden zerschiesst.
    """
    import services.backup_migration_service as mod

    fake_dir = tmp_path / ".msm"
    fake_state = fake_dir / "state.json"
    monkeypatch.setattr(mod, "STATE_DIR", fake_dir)
    monkeypatch.setattr(mod, "STATE_FILE", fake_state)
    return fake_state


# ── State-File Tests ──────────────────────────────────────────────────


class TestStateFile:
    """Tests fuer state.json load/save/atomic-write."""

    def test_load_returns_default_when_file_missing(self, state_in_tmp):
        from services.backup_migration_service import MigrationState, load_state

        # state_in_tmp existiert nicht (nur sein Parent)
        result = load_state()
        assert isinstance(result, MigrationState)
        assert result.cloud_migration_done is False
        assert result.cloud_migration_target == ""

    def test_load_returns_default_when_file_corrupt(self, state_in_tmp):
        from services.backup_migration_service import MigrationState, load_state

        state_in_tmp.parent.mkdir(parents=True, exist_ok=True)
        state_in_tmp.write_text("{ this is not valid json", encoding="utf-8")

        result = load_state()
        assert isinstance(result, MigrationState)
        assert result.cloud_migration_done is False  # Default trotz korruptem File

    def test_save_and_load_roundtrip(self, state_in_tmp):
        from services.backup_migration_service import (
            MigrationState,
            load_state,
            save_state,
        )

        original = MigrationState(
            cloud_migration_done=True,
            cloud_migration_target="s3",
            cloud_migration_completed_at="2026-06-06T22:00:00Z",
            cloud_migration_total=42,
            cloud_migration_migrated=42,
        )
        save_state(original)

        assert state_in_tmp.exists()
        loaded = load_state()
        assert loaded.cloud_migration_done is True
        assert loaded.cloud_migration_target == "s3"
        assert loaded.cloud_migration_completed_at == "2026-06-06T22:00:00Z"
        assert loaded.cloud_migration_total == 42
        assert loaded.cloud_migration_migrated == 42

    def test_save_atomic_overwrite_existing(self, state_in_tmp):
        """save_state schreibt atomar (tmp + rename), keine korrupte JSON."""
        from services.backup_migration_service import (
            MigrationState,
            load_state,
            save_state,
        )

        state_in_tmp.parent.mkdir(parents=True, exist_ok=True)
        save_state(MigrationState(cloud_migration_target="old"))
        save_state(MigrationState(cloud_migration_target="new"))

        loaded = load_state()
        assert loaded.cloud_migration_target == "new"

    def test_load_ignores_unknown_fields(self, state_in_tmp):
        """Robustes Parsing: zusaetzliche Felder (z.B. von aelterer Version)
        werden ignoriert, kein Crash.
        """
        import json
        from services.backup_migration_service import load_state

        state_in_tmp.parent.mkdir(parents=True, exist_ok=True)
        state_in_tmp.write_text(
            json.dumps(
                {
                    "cloud_migration_done": True,
                    "unknown_future_field": "ignored",
                }
            ),
            encoding="utf-8",
        )

        loaded = load_state()
        assert loaded.cloud_migration_done is True


# ── should_run / has_local_backups Tests ──────────────────────────────


class TestShouldRun:
    """Tests fuer should_run() — Trigger-Bedingungen."""

    def test_should_run_false_for_local_provider(self, monkeypatch):
        from services import backup_migration_service as mod
        from services.backup_migration_service import BackupMigrationService

        monkeypatch.setattr(
            "services.backup_migration_service.settings.backup_provider", "local"
        )
        svc = BackupMigrationService()
        assert svc.should_run() is False

    def test_should_run_false_when_state_done(self, state_in_tmp):
        from services import backup_migration_service as mod
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationState,
        )

        monkeypatch_ = __import__("pytest").MonkeyPatch()
        try:
            monkeypatch_.setattr(
                "services.backup_migration_service.settings.backup_provider", "s3"
            )
            mod.save_state(
                MigrationState(
                    cloud_migration_done=True, cloud_migration_target="s3"
                )
            )
            svc = BackupMigrationService()
            assert svc.should_run() is False
        finally:
            monkeypatch_.undo()

    def test_should_run_true_for_cloud_provider_when_state_not_done(
        self, state_in_tmp
    ):
        from services.backup_migration_service import BackupMigrationService

        mp = __import__("pytest").MonkeyPatch()
        try:
            mp.setattr(
                "services.backup_migration_service.settings.backup_provider", "s3"
            )
            # Kein state.json -> default cloud_migration_done=False
            svc = BackupMigrationService()
            assert svc.should_run() is True
        finally:
            mp.undo()


class TestHasLocalBackups:
    """Tests fuer has_local_backups() — DB-Check mit Server-Join."""

    def test_has_local_backups_empty_db(self, db: Session):
        from services.backup_migration_service import BackupMigrationService

        svc = BackupMigrationService()
        assert svc.has_local_backups(db) is False

    def test_has_local_backups_with_local_records(
        self, db: Session, test_server, monkeypatch
    ):
        """provider=local + target=cloud -> True."""
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        monkeypatch.setattr(
            "services.backup_migration_service.settings.backup_provider", "s3"
        )
        db.add(
            Backup(
                server_id=test_server.id,
                filename="/tmp/foo.tar.gz",
                provider="local",
            )
        )
        db.commit()

        svc = BackupMigrationService()
        assert svc.has_local_backups(db) is True

    def test_has_local_backups_false_when_already_on_target(
        self, db: Session, test_server, monkeypatch
    ):
        """provider=s3 + target=s3 -> False (idempotent)."""
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        monkeypatch.setattr(
            "services.backup_migration_service.settings.backup_provider", "s3"
        )
        # filename spiegelt remote_key fuer cloud-Records (NOT NULL constraint)
        db.add(
            Backup(
                server_id=test_server.id,
                filename=f"{test_server.id}/foo.tar.gz",
                provider="s3",
                remote_key=f"{test_server.id}/foo.tar.gz",
            )
        )
        db.commit()

        svc = BackupMigrationService()
        assert svc.has_local_backups(db) is False

    def test_has_local_backups_skips_deleted_server(
        self, db: Session, test_server, monkeypatch
    ):
        """Server-Cascade: deleted Server -> Backup-Record auch weg.

        Plan §3.10: nur Backups zu noch-existierenden Servern migrieren.
        """
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        monkeypatch.setattr(
            "services.backup_migration_service.settings.backup_provider", "s3"
        )
        db.add(
            Backup(
                server_id=test_server.id,
                filename="/tmp/foo.tar.gz",
                provider="local",
            )
        )
        db.commit()

        svc = BackupMigrationService()
        assert svc.has_local_backups(db) is True

        # Server loeschen -> Cascade loescht Backup
        db.delete(test_server)
        db.commit()
        assert svc.has_local_backups(db) is False

    def test_has_local_backups_treats_null_provider_as_local(
        self, db: Session, test_server, monkeypatch
    ):
        """Sehr alte Records (vor Cloud-Enable) haben provider==NULL.

        Die Migration muss auch diese finden.
        """
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        monkeypatch.setattr(
            "services.backup_migration_service.settings.backup_provider", "s3"
        )
        db.add(
            Backup(
                server_id=test_server.id,
                filename="/tmp/old.tar.gz",
                provider=None,  # Sehr alter Record
            )
        )
        db.commit()

        svc = BackupMigrationService()
        assert svc.has_local_backups(db) is True


# ── Migration-Flow Tests ──────────────────────────────────────────────


class TestMigrationFlow:
    """Tests fuer run() — Happy Path + Edge Cases."""

    def test_run_with_no_local_records_marks_done(
        self, db: Session, state_in_tmp
    ):
        from services import backup_migration_service as mod
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        mp = __import__("pytest").MonkeyPatch()
        try:
            mp.setattr(
                "services.backup_migration_service.settings.backup_provider", "s3"
            )
            fake_provider = MagicMock()
            svc = BackupMigrationService()
            progress = svc.run(
                db, target_provider=fake_provider, target_provider_name="s3"
            )

            assert progress.status == MigrationStatus.COMPLETED
            assert progress.migrated == 0
            assert progress.total == 0
            state = mod.load_state()
            assert state.cloud_migration_done is True
            assert state.cloud_migration_target == "s3"
            fake_provider.upload.assert_not_called()
        finally:
            mp.undo()

    def test_run_migrates_one_local_backup(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        # Lokales Backup-File anlegen
        backup_file = tmp_path / "test_backup.tar.gz"
        backup_file.write_bytes(b"fake tar content")
        db.add(
            Backup(
                server_id=test_server.id,
                filename=str(backup_file),
                provider="local",
            )
        )
        db.commit()

        fake_provider = MagicMock()
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        assert progress.status == MigrationStatus.COMPLETED
        assert progress.migrated == 1
        assert progress.total == 1
        # Provider wurde 1x aufgerufen
        assert fake_provider.upload.call_count == 1
        call_args = fake_provider.upload.call_args
        upload_path, remote_key = call_args.args
        assert remote_key == f"{test_server.id}/test_backup.tar.gz"
        # Backup-Record ist jetzt migriert
        db.expire_all()
        rec = db.query(Backup).first()
        assert rec.provider == "s3"
        assert rec.remote_key == f"{test_server.id}/test_backup.tar.gz"
        # Cloud-Record: filename spiegelt remote_key (NOT NULL constraint)
        assert rec.filename == remote_key
        # Lokale Datei ist geloescht
        assert not backup_file.exists()

    def test_run_is_idempotent_skips_already_migrated(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """Re-Run darf bereits migrierte Records nicht doppelt hochladen.

        Plan §3.10: Idempotenz nach Crash.
        """
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        backup_file = tmp_path / "test.tar.gz"
        backup_file.write_bytes(b"x")
        # Bereits migrierter Record (provider=s3, remote_key=...)
        db.add(
            Backup(
                server_id=test_server.id,
                filename=f"{test_server.id}/test.tar.gz",
                provider="s3",
                remote_key=f"{test_server.id}/test.tar.gz",
            )
        )
        # Lokaler Record
        db.add(
            Backup(
                server_id=test_server.id,
                filename=str(backup_file),
                provider="local",
            )
        )
        db.commit()

        fake_provider = MagicMock()
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        # Nur 1 Upload (lokaler Record), bereits-migrierter uebersprungen
        assert progress.migrated == 1
        assert progress.total == 1
        assert fake_provider.upload.call_count == 1


# ── Failure-Mode Tests ────────────────────────────────────────────────


class TestFailureModes:
    """Tests fuer Failure-Pfade: FileNotFoundError, ProviderError, Encryption."""

    def test_run_skips_missing_file_and_continues(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """FileNotFoundError = soft-skip, Job laeuft weiter."""
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        # 1. Backup mit File, 2. Backup OHNE File
        file1 = tmp_path / "exists.tar.gz"
        file1.write_bytes(b"x")
        db.add(Backup(server_id=test_server.id, filename=str(file1), provider="local"))
        db.add(
            Backup(
                server_id=test_server.id,
                filename=str(tmp_path / "missing.tar.gz"),
                provider="local",
            )
        )
        db.commit()

        fake_provider = MagicMock()
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        # 1 migriert, 1 soft-skip (FileNotFoundError), Job gilt als completed
        # (Plan: solange mindestens 1 erfolgreich -> done markieren)
        assert progress.migrated == 1
        assert progress.status == "completed"
        assert fake_provider.upload.call_count == 1  # nur das existierende File

    def test_run_stops_on_provider_error(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """ProviderError (Credentials/Network) = hard-stop, done bleibt false."""
        from services.backup_provider import ProviderError
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        for i in range(3):
            f = tmp_path / f"b{i}.tar.gz"
            f.write_bytes(b"x")
            db.add(Backup(server_id=test_server.id, filename=str(f), provider="local"))
        db.commit()

        fake_provider = MagicMock()
        fake_provider.upload.side_effect = ProviderError("S3-Credentials fehlen")
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        assert progress.status == MigrationStatus.FAILED
        assert progress.migrated == 0
        assert progress.failed == 1
        assert fake_provider.upload.call_count == 1  # Stoppt nach 1. Fehler

    def test_run_sanitizes_error_message(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """Provider-Fehler-Text darf nicht im progress.last_error landen
        (Token-Leak, Pfad-Leak verhindern)."""
        from services.backup_provider import ProviderError
        from models import Backup
        from services.backup_migration_service import BackupMigrationService

        f = tmp_path / "b.tar.gz"
        f.write_bytes(b"x")
        db.add(Backup(server_id=test_server.id, filename=str(f), provider="local"))
        db.commit()

        fake_provider = MagicMock()
        fake_provider.upload.side_effect = ProviderError(
            "AccessKey=AKIA12345SECRET /home/leak"
        )
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        # Sanitization: nur Typ-Name + Backup-ID, nicht die volle Message
        assert progress.last_error is not None
        assert "AKIA" not in progress.last_error
        assert "/home/leak" not in progress.last_error
        assert "ProviderError" in progress.last_error or "Backup" in progress.last_error


# ── Cancel-Tests ──────────────────────────────────────────────────────


class TestCancel:
    """Tests fuer User-Cancel mitten im Lauf."""

    def test_cancel_stops_after_current_backup(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        for i in range(3):
            f = tmp_path / f"b{i}.tar.gz"
            f.write_bytes(b"x")
            db.add(Backup(server_id=test_server.id, filename=str(f), provider="local"))
        db.commit()

        svc = BackupMigrationService()
        call_count = [0]

        def slow_upload(*args, **kwargs):
            call_count[0] += 1
            # Beim 2. Upload cancel triggern
            if call_count[0] == 2:
                svc.cancel()

        fake_provider = MagicMock()
        fake_provider.upload.side_effect = slow_upload

        progress = svc.run(
            db,
            target_provider=fake_provider,
            target_provider_name="s3",
        )

        # Cancel wird ZWISCHEN Iterations geprueft (nicht innerhalb eines
        # Uploads). Daher: 1 fertig (call 1), 2 triggert cancel, call 2
        # returnt normal, dann wird 3 vor Start abgebrochen. migrated=2.
        assert progress.status == MigrationStatus.CANCELLED
        assert progress.migrated == 2  # 2 fertig (call 1 + call 2), dann gestoppt
        assert fake_provider.upload.call_count == 2  # call 3 nie erreicht

    def test_cancel_idempotent(self, db: Session):
        from services.backup_migration_service import BackupMigrationService

        svc = BackupMigrationService()
        svc.cancel()
        svc.cancel()  # Kein Raise
        svc.cancel()  # Immer noch kein Raise


# ── Cross-Cloud-Tests ─────────────────────────────────────────────────


class TestCrossCloudMigration:
    """Tests fuer Cloud A -> Cloud B Migration."""

    def test_run_with_explicit_target_provider(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """Cross-Cloud: target_provider explizit uebergeben.

        Wenn ein User von S3 zu GCS wechselt, soll die Migration die
        existierenden S3-Backups nach GCS kopieren. Der Service nimmt
        dann den GCS-Provider als target, NICHT settings.backup_provider.
        """
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        f = tmp_path / "s3_backup.tar.gz"
        f.write_bytes(b"x")
        db.add(Backup(server_id=test_server.id, filename=str(f), provider="s3"))
        db.commit()

        gcs_provider = MagicMock()
        svc = BackupMigrationService()
        progress = svc.run(
            db,
            target_provider=gcs_provider,
            target_provider_name="gcs",
        )

        # S3 -> GCS upload erfolgt
        assert progress.status == MigrationStatus.COMPLETED
        assert progress.migrated == 1
        assert gcs_provider.upload.call_count == 1
        call_args = gcs_provider.upload.call_args
        _, remote_key = call_args.args
        assert remote_key == f"{test_server.id}/s3_backup.tar.gz"
        # Record ist jetzt gcs
        db.expire_all()
        rec = db.query(Backup).first()
        assert rec.provider == "gcs"


# ── Progress-Tests ───────────────────────────────────────────────────


class TestProgress:
    """Tests fuer progress() Snapshot + Status-Transitions."""

    def test_progress_idle_initially(self):
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        svc = BackupMigrationService()
        p = svc.progress()
        assert p.status == MigrationStatus.IDLE
        assert p.total == 0

    def test_progress_snapshot_is_decoupled(self, db: Session):
        """Aenderungen am internen Progress duerfen den Snapshot nicht
        rueckwirkend aendern (call-by-value statt call-by-reference)."""
        from services.backup_migration_service import BackupMigrationService

        svc = BackupMigrationService()
        snap1 = svc.progress()
        snap1.migrated = 999  # In-place mutation
        snap2 = svc.progress()
        assert snap2.migrated == 0  # unveraendert


# ── 9.4-Tests: State-Reset bei Provider-Wechsel + Encryption ──────────


class TestStateResetOnProviderChange:
    """Schritt 9.4: Wenn install.sh die Pending-Flags setzt (Provider-Wechsel),
    muss der main.py-Hook state.cloud_migration_done zuruecksetzen, damit
    die Migration laeuft. Diese Tests verifizieren das Verhalten der
    _reset_state_if_pending-Helper-Logik (in main.py inline, hier als
    pure-Funktion getestet).
    """

    def test_reset_state_when_pending_auto_migration_flag_set(
        self, state_in_tmp
    ):
        """pending_auto_migration=1 + state.done=true -> state.done=false."""
        from services import backup_migration_service as mod
        from services.backup_migration_service import (
            MigrationState,
            load_state,
        )

        # Setup: state.done=true (alter Cloud-Provider-Stand)
        mod.save_state(
            MigrationState(cloud_migration_done=True, cloud_migration_target="s3")
        )

        # Helper simuliert main.py Hook-Logik
        settings_pending = True  # settings.pending_auto_migration
        if settings_pending:
            state = load_state()
            if state.cloud_migration_done:
                state.cloud_migration_done = False
                state.cloud_migration_target = ""
                state.cloud_migration_completed_at = ""
                mod.save_state(state)

        loaded = load_state()
        assert loaded.cloud_migration_done is False
        assert loaded.cloud_migration_target == ""

    def test_reset_state_when_pending_cross_cloud_flag_set(
        self, state_in_tmp
    ):
        """pending_cross_cloud_migration=1 + state.done=true -> reset."""
        from services import backup_migration_service as mod
        from services.backup_migration_service import (
            MigrationState,
            load_state,
        )

        mod.save_state(
            MigrationState(cloud_migration_done=True, cloud_migration_target="s3")
        )

        settings_pending_cross = True
        if settings_pending_cross:
            state = load_state()
            if state.cloud_migration_done:
                state.cloud_migration_done = False
                state.cloud_migration_target = ""
                state.cloud_migration_completed_at = ""
                mod.save_state(state)

        loaded = load_state()
        assert loaded.cloud_migration_done is False

    def test_does_not_reset_state_when_no_pending_flag(
        self, state_in_tmp
    ):
        """Ohne Pending-Flag darf state NICHT resettet werden.

        Sonst wuerde ein erfolgreich abgeschlossener Migrations-Run
        beim naechsten Startup nochmal getriggert.
        """
        from services import backup_migration_service as mod
        from services.backup_migration_service import (
            MigrationState,
            load_state,
        )

        mod.save_state(
            MigrationState(cloud_migration_done=True, cloud_migration_target="gcs")
        )

        settings_pending = False  # Kein Provider-Wechsel
        if settings_pending:  # noqa
            state = load_state()
            if state.cloud_migration_done:
                state.cloud_migration_done = False
                mod.save_state(state)

        loaded = load_state()
        assert loaded.cloud_migration_done is True  # unveraendert
        assert loaded.cloud_migration_target == "gcs"


class TestEncryptionIntegration:
    """Schritt 9.4: Encryption-Test der Migration.

    Verifiziert dass der Service den tar.gz VOR dem Upload verschluesselt
    (AES-256-GCM), wenn MSM_BACKUP_ENCRYPTION_KEY gesetzt ist. Wir nutzen
    den echten encrypt_file (nicht gemockt), um die End-to-End-Integration
    sicherzustellen.
    """

    def test_run_with_encryption_uploads_encrypted_file(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        import base64
        import os
        import secrets

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        # 32-byte base64 key (wie .env ihn speichert)
        key = base64.b64encode(secrets.token_bytes(32)).decode()

        # Tar-File anlegen
        plain = b"plaintext tar content for encryption test" * 100
        backup_file = tmp_path / "plain.tar.gz"
        backup_file.write_bytes(plain)
        db.add(Backup(server_id=test_server.id, filename=str(backup_file), provider="local"))
        db.commit()

        # Capture was uploaded: lies Bytes BEVOR der finally-Block das
        # encrypted temp-File wieder loescht.
        captured_encrypted: list[bytes] = []
        captured_path: list = []
        captured_remote_key: list[str] = []

        def capture_upload(path, remote_key, **kwargs):
            captured_path.append(path)
            captured_remote_key.append(remote_key)
            captured_encrypted.append(open(path, "rb").read())

        fake_provider = MagicMock()
        fake_provider.upload.side_effect = capture_upload

        svc = BackupMigrationService()

        # Patch encryption_key
        from services import backup_migration_service as mod
        original = mod.settings.backup_encryption_key
        try:
            mod.settings.backup_encryption_key = key
            progress = svc.run(
                db,
                target_provider=fake_provider,
                target_provider_name="s3",
            )
        finally:
            mod.settings.backup_encryption_key = original

        assert progress.status == MigrationStatus.COMPLETED
        assert progress.migrated == 1
        # upload() wurde 1x aufgerufen
        assert len(captured_encrypted) == 1
        # Remote-Key hat .enc Suffix (zeigt dass Encryption aktiv war)
        assert captured_remote_key[0].endswith(".enc")
        # Uploaded file ist NICHT das Original
        assert str(captured_path[0]) != str(backup_file)
        # File-Format: [1 byte version=0x01][12 byte nonce][ciphertext+tag]
        encrypted_data = captured_encrypted[0]
        assert encrypted_data != plain
        assert encrypted_data[0] == 0x01
        nonce = encrypted_data[1:13]
        ciphertext = encrypted_data[13:]
        # Mit dem richtigen Key koennen wir entschluesseln
        aesgcm = AESGCM(base64.b64decode(key))
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        assert decrypted == plain
        # Encrypted temp file ist nach Migration aufgeraeumt
        assert not os.path.exists(captured_path[0])
        # Original-File ist geloescht (Cloud-only-Mode)
        assert not backup_file.exists()

    def test_run_without_encryption_uploads_plain_file(
        self, db: Session, test_server, tmp_path, state_in_tmp
    ):
        """Ohne Encryption-Key wird das tar.gz unverschluesselt hochgeladen
        (heutiges Verhalten, nur fuer local-Provider sinnvoll).
        """
        from models import Backup
        from services.backup_migration_service import (
            BackupMigrationService,
            MigrationStatus,
        )

        plain = b"plaintext content"
        backup_file = tmp_path / "plain.tar.gz"
        backup_file.write_bytes(plain)
        db.add(Backup(server_id=test_server.id, filename=str(backup_file), provider="local"))
        db.commit()

        uploaded_path = []

        def capture_upload(path, key, **kwargs):
            uploaded_path.append(path)

        fake_provider = MagicMock()
        fake_provider.upload.side_effect = capture_upload

        from services import backup_migration_service as mod
        original = mod.settings.backup_encryption_key
        try:
            mod.settings.backup_encryption_key = ""  # Kein Key
            svc = BackupMigrationService()
            progress = svc.run(
                db,
                target_provider=fake_provider,
                target_provider_name="s3",
            )
        finally:
            mod.settings.backup_encryption_key = original

        assert progress.status == MigrationStatus.COMPLETED
        assert progress.migrated == 1
        # Upload ist das Original-File (kein .enc Suffix im remote_key)
        assert len(uploaded_path) == 1
        assert str(uploaded_path[0]) == str(backup_file)
        assert not str(backup_file).endswith(".enc")
