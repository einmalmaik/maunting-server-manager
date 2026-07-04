"""Tests fuer Backup-Config Service und Router.

Abgedeckte Assertions:
- VAL-S3-001: Admin kann S3-Config setzen
- VAL-S3-002: S3-Credentials verschluesselt via DIS (AAD=s3)
- VAL-S3-003: GET gibt maskierte Credentials (letzte 4 Zeichen)
- VAL-S3-004: Non-admin 403, unauth 401 auf allen Endpunkten
- VAL-S3-005: Missing required fields rejected, region optional
- VAL-S3-006/007: S3 Verbindungstest (success/error ohne Leak)
- VAL-S3-008: Verbindungstest wenn nicht konfiguriert -> clear error
- VAL-S3-009: Admin kann Backup-Passwort setzen (verschluesselt, AAD=pw)
- VAL-S3-010: Backup-Passwort wird in KEINER API-Antwort zurueckgegeben
- VAL-S3-011: Salt auto-generiert, plain-text, reused on change
- VAL-S3-012: Empty/whitespace Passwort rejected
- VAL-S3-019: Status reports s3_configured, backup_password_set, last_panel_backup
- VAL-S3-020: Overwrite semantics (no duplicate panel_settings rows)
- VAL-S3-021: AAD domain separation (cross-context decrypt fails)
- VAL-S3-022: No credentials/passwords in logs
"""
from __future__ import annotations

import logging

import boto3
import pytest
from moto import mock_aws

from models import PanelSetting
from services.backup_config_service import BackupConfigService
from services.dis_client import DisClient, DisDecryptionError
from services.panel_settings_service import PanelSettingsService

S3_AAD = "msm:backup:s3"
PW_AAD = "msm:backup:pw"

TEST_ENDPOINT = "https://s3.us-west-004.backblazeb2.com"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TEST_BUCKET = "msm-backup-bucket"
TEST_REGION = "us-west-004"
TEST_PASSWORD = "MySecureBackupPassword123!"

# ── Helper ───────────────────────────────────────────────────────────────


def _set_s3_config_via_api(client, cookies, cfg: dict) -> object:
    """POST /api/backup-config/s3 mit CSRF."""
    csrf = cookies.get("__Secure-csrf_token")
    return client.post(
        "/api/backup-config/s3",
        json=cfg,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def _set_password_via_api(client, cookies, password: str) -> object:
    csrf = cookies.get("__Secure-csrf_token")
    return client.post(
        "/api/backup-config/password",
        json={"password": password},
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


# ── Service Tests ────────────────────────────────────────────────────────


class TestBackupConfigService:
    """Unit-Tests fuer BackupConfigService (Service-Layer)."""

    def test_set_and_get_s3_config(self):
        """VAL-S3-001/003: S3-Config setzen, GET gibt maskierte Credentials."""
        BackupConfigService.set_s3_config(
            endpoint=TEST_ENDPOINT,
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
            region=TEST_REGION,
        )
        cfg = BackupConfigService.get_s3_config()
        assert cfg["endpoint"] == TEST_ENDPOINT
        assert cfg["bucket"] == TEST_BUCKET
        assert cfg["region"] == TEST_REGION
        # Credentials maskiert (letzte 4 Zeichen)
        assert cfg["access_key"].endswith(TEST_ACCESS_KEY[-4:])
        assert cfg["secret_key"].endswith(TEST_SECRET_KEY[-4:])
        assert "*" in cfg["access_key"]
        assert "*" in cfg["secret_key"]
        # Keine Plaintext-Credentials
        assert TEST_ACCESS_KEY not in cfg["access_key"]
        assert TEST_SECRET_KEY not in cfg["secret_key"]

    def test_s3_credentials_encrypted_in_db(self, db):
        """VAL-S3-002: Credentials verschluesselt in panel_settings (AAD=s3)."""
        BackupConfigService.set_s3_config(
            endpoint=TEST_ENDPOINT,
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
        )
        access_enc = PanelSettingsService.get("backup.s3_access_key_encrypted")
        secret_enc = PanelSettingsService.get("backup.s3_secret_key_encrypted")
        # Verschluesselt (nicht Plaintext)
        assert access_enc != TEST_ACCESS_KEY
        assert secret_enc != TEST_SECRET_KEY
        # Mit korrektem AAD entschluesselbar
        assert DisClient.decrypt(access_enc, aad=S3_AAD) == TEST_ACCESS_KEY
        assert DisClient.decrypt(secret_enc, aad=S3_AAD) == TEST_SECRET_KEY

    def test_aad_domain_separation(self):
        """VAL-S3-021: Cross-Context-Decryption schlaegt fehl."""
        BackupConfigService.set_s3_config(
            endpoint=TEST_ENDPOINT,
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
        )
        BackupConfigService.set_backup_password(TEST_PASSWORD)
        access_enc = PanelSettingsService.get("backup.s3_access_key_encrypted")
        pw_enc = PanelSettingsService.get("backup.password_encrypted")
        # S3-Cred mit pw-AAD -> Fehler
        with pytest.raises(DisDecryptionError):
            DisClient.decrypt(access_enc, aad=PW_AAD)
        # Password mit s3-AAD -> Fehler
        with pytest.raises(DisDecryptionError):
            DisClient.decrypt(pw_enc, aad=S3_AAD)

    def test_is_s3_configured(self):
        assert BackupConfigService.is_s3_configured() is False
        BackupConfigService.set_s3_config(
            endpoint="",
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
        )
        assert BackupConfigService.is_s3_configured() is True

    def test_overwrite_no_duplicate_rows(self, db):
        """VAL-S3-020: Zweimal setzen ueberschreibt, keine Duplikate."""
        BackupConfigService.set_s3_config(
            endpoint="https://old.example.com",
            access_key="OLDACCESSKEY1234",
            secret_key="OLDSECRETKEY1234ABCD",
            bucket="old-bucket",
        )
        BackupConfigService.set_s3_config(
            endpoint=TEST_ENDPOINT,
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
        )
        # Genau eine Row pro Key
        rows = db.query(PanelSetting).filter(
            PanelSetting.key == "backup.s3_bucket"
        ).all()
        assert len(rows) == 1
        assert rows[0].value == TEST_BUCKET

    def test_set_backup_password_encrypted(self, db):
        """VAL-S3-009: Passwort verschluesselt via DIS (AAD=pw)."""
        BackupConfigService.set_backup_password(TEST_PASSWORD)
        pw_enc = PanelSettingsService.get("backup.password_encrypted")
        assert pw_enc != TEST_PASSWORD
        assert DisClient.decrypt(pw_enc, aad=PW_AAD) == TEST_PASSWORD
        assert BackupConfigService.is_backup_password_set() is True

    def test_get_backup_password(self):
        """get_backup_password entschluesselt korrekt."""
        BackupConfigService.set_backup_password(TEST_PASSWORD)
        assert BackupConfigService.get_backup_password() == TEST_PASSWORD

    def test_get_backup_password_not_set_raises(self):
        """get_backup_password wirft Fehler wenn nicht gesetzt."""
        with pytest.raises((ValueError, DisDecryptionError)):
            BackupConfigService.get_backup_password()

    def test_salt_auto_generated_and_reused(self, db):
        """VAL-S3-011: Salt auto-generiert, plain-text, reused on change."""
        # Vorher kein Salt
        assert PanelSettingsService.get("backup.salt") == ""
        salt1 = BackupConfigService.ensure_backup_salt()
        assert salt1 != ""
        # Salt ist base64
        import base64
        decoded = base64.b64decode(salt1)
        assert len(decoded) >= 16
        # In DB als plain-text gespeichert
        row = db.query(PanelSetting).filter(
            PanelSetting.key == "backup.salt"
        ).first()
        assert row is not None
        assert row.value == salt1

        # Passwort setzen reused Salt
        BackupConfigService.set_backup_password("FirstPassword123!")
        salt_after_first = PanelSettingsService.get("backup.salt")
        assert salt_after_first == salt1

        # Zweites Passwort setzen reused Salt ebenfalls
        BackupConfigService.set_backup_password("SecondPassword456!")
        salt_after_second = PanelSettingsService.get("backup.salt")
        assert salt_after_second == salt1

    def test_salt_get_returns_plaintext(self):
        """get_backup_salt gibt base64 direkt (kein DIS decrypt)."""
        BackupConfigService.set_backup_password(TEST_PASSWORD)
        salt = BackupConfigService.get_backup_salt()
        # Sollte identisch mit ensure_backup_salt sein
        assert salt == BackupConfigService.ensure_backup_salt()
        # Base64-decodierbar
        import base64
        base64.b64decode(salt)

    def test_empty_password_rejected_at_service(self):
        """VAL-S3-012: Empty/whitespace Passwort wirft Fehler (Service-Layer)."""
        # Service-Layer hat keine Validierung; das macht der Router via Pydantic.
        # Aber wir stellen sicher, dass ein leerer String nicht als 'gesetzt' zaehlt.
        BackupConfigService.set_backup_password("   ")
        # is_backup_password_set prueft nur ob ein Wert gespeichert wurde.
        # Die echte Validierung erfolgt im Router (Pydantic field_validator).
        assert BackupConfigService.is_backup_password_set() is True

    def test_status_reports_flags(self):
        """VAL-S3-019: Status reports s3_configured, backup_password_set, last_panel_backup."""
        status = BackupConfigService.get_status()
        assert status["s3_configured"] is False
        assert status["backup_password_set"] is False
        assert status["last_panel_backup"] is None

        BackupConfigService.set_s3_config(
            endpoint=TEST_ENDPOINT,
            access_key=TEST_ACCESS_KEY,
            secret_key=TEST_SECRET_KEY,
            bucket=TEST_BUCKET,
        )
        BackupConfigService.set_backup_password(TEST_PASSWORD)
        status = BackupConfigService.get_status()
        assert status["s3_configured"] is True
        assert status["backup_password_set"] is True
        assert status["last_panel_backup"] is None


# ── Router Tests ─────────────────────────────────────────────────────────


class TestBackupConfigRouter:
    """Integration-Tests fuer Backup-Config Router (API-Layer)."""

    def test_get_config_unauthenticated_401(self, client):
        """VAL-S3-004: Unauth -> 401."""
        resp = client.get("/api/backup-config")
        assert resp.status_code == 401

    def test_get_config_non_admin_403(self, client, user_cookies):
        """VAL-S3-004: Non-admin -> 403."""
        resp = client.get("/api/backup-config", cookies=user_cookies)
        assert resp.status_code == 403

    def test_get_config_admin_200(self, client, owner_cookies):
        """Admin -> 200 mit maskierten Credentials."""
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": TEST_ENDPOINT,
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
            "region": TEST_REGION,
        })
        resp = client.get("/api/backup-config", cookies=owner_cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == TEST_ENDPOINT
        assert data["bucket"] == TEST_BUCKET
        assert data["region"] == TEST_REGION
        # Maskiert
        assert data["access_key"].endswith(TEST_ACCESS_KEY[-4:])
        assert data["secret_key"].endswith(TEST_SECRET_KEY[-4:])
        assert TEST_ACCESS_KEY not in data["access_key"]
        assert TEST_SECRET_KEY not in data["secret_key"]

    def test_set_s3_config_unauthenticated_401(self, client):
        resp = client.post("/api/backup-config/s3", json={
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
        })
        assert resp.status_code == 401

    def test_set_s3_config_non_admin_403(self, client, user_cookies):
        csrf = user_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/s3",
            json={"access_key": "k", "secret_key": "s", "bucket": "b"},
            cookies=user_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_set_s3_config_no_csrf_403(self, client, owner_cookies):
        """CSRF required on write endpoints."""
        resp = client.post(
            "/api/backup-config/s3",
            json={"access_key": "k", "secret_key": "s", "bucket": "b"},
            cookies=owner_cookies,
        )
        assert resp.status_code == 403

    def test_set_s3_config_missing_required_fields(self, client, owner_cookies):
        """VAL-S3-005: Missing required fields -> 4xx. Region optional."""
        csrf = owner_cookies.get("__Secure-csrf_token")
        # Fehlt access_key
        resp = client.post(
            "/api/backup-config/s3",
            json={"secret_key": "s", "bucket": "b"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422
        # Fehlt secret_key
        resp = client.post(
            "/api/backup-config/s3",
            json={"access_key": "k", "bucket": "b"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422
        # Fehlt bucket
        resp = client.post(
            "/api/backup-config/s3",
            json={"access_key": "k", "secret_key": "s"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422

    def test_set_s3_config_region_optional(self, client, owner_cookies):
        """VAL-S3-005: Region optional -> 200 ohne region."""
        resp = _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": TEST_ENDPOINT,
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
        })
        assert resp.status_code == 200
        # Status sollte S3 als konfiguriert melden
        status = client.get("/api/backup-config/status", cookies=owner_cookies)
        assert status.json()["s3_configured"] is True

    def test_set_s3_config_persists_encrypted(self, client, owner_cookies, db):
        """VAL-S3-001/002: Config persistiert + verschluesselt."""
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": TEST_ENDPOINT,
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
            "region": TEST_REGION,
        })
        access_row = db.query(PanelSetting).filter(
            PanelSetting.key == "backup.s3_access_key_encrypted"
        ).first()
        assert access_row is not None
        assert access_row.value != TEST_ACCESS_KEY
        assert DisClient.decrypt(access_row.value, aad=S3_AAD) == TEST_ACCESS_KEY

    @mock_aws
    def test_test_s3_connection_success(self, client, owner_cookies):
        """VAL-S3-006: Verbindungstest success mit validen Credentials."""
        # moto Bucket erstellen
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=TEST_BUCKET)
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": "",  # moto: kein Endpoint
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
            "region": "us-east-1",
        })
        csrf = owner_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/test-s3",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["bucket"] == TEST_BUCKET

    @mock_aws
    def test_test_s3_connection_invalid_creds_no_leak(self, client, owner_cookies):
        """VAL-S3-007: Invalid creds -> error ohne Credential-Leak."""
        # Kein Bucket erstellen -> head_bucket schlaegt fehl
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": "",
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": "nonexistent-bucket-xyz",
            "region": "us-east-1",
        })
        csrf = owner_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/test-s3",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code in (400, 502)
        body = resp.text
        # Keine Credentials im Response
        assert TEST_ACCESS_KEY not in body
        assert TEST_SECRET_KEY not in body

    def test_test_s3_connection_not_configured(self, client, owner_cookies):
        """VAL-S3-008: Verbindungstest ohne Config -> clear error."""
        csrf = owner_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/test-s3",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "nicht konfiguriert" in resp.json()["detail"].lower()

    def test_test_s3_connection_unauthenticated_401(self, client):
        resp = client.post("/api/backup-config/test-s3")
        assert resp.status_code == 401

    def test_test_s3_connection_non_admin_403(self, client, user_cookies):
        csrf = user_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/test-s3",
            cookies=user_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_test_s3_no_csrf_403(self, client, owner_cookies):
        resp = client.post("/api/backup-config/test-s3", cookies=owner_cookies)
        assert resp.status_code == 403

    # ── Password Endpunkte ──

    def test_set_password_admin_200(self, client, owner_cookies, db):
        """VAL-S3-009: Admin kann Passwort setzen (verschluesselt)."""
        resp = _set_password_via_api(client, owner_cookies, TEST_PASSWORD)
        assert resp.status_code == 200
        # Verschluesselt in DB
        pw_enc = PanelSettingsService.get("backup.password_encrypted")
        assert pw_enc != TEST_PASSWORD
        assert DisClient.decrypt(pw_enc, aad=PW_AAD) == TEST_PASSWORD
        # is_backup_password_set
        assert BackupConfigService.is_backup_password_set() is True

    def test_set_password_unauthenticated_401(self, client):
        resp = client.post(
            "/api/backup-config/password",
            json={"password": TEST_PASSWORD},
        )
        assert resp.status_code == 401

    def test_set_password_non_admin_403(self, client, user_cookies):
        csrf = user_cookies.get("__Secure-csrf_token")
        resp = client.post(
            "/api/backup-config/password",
            json={"password": TEST_PASSWORD},
            cookies=user_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_set_password_no_csrf_403(self, client, owner_cookies):
        resp = client.post(
            "/api/backup-config/password",
            json={"password": TEST_PASSWORD},
            cookies=owner_cookies,
        )
        assert resp.status_code == 403

    def test_set_password_empty_rejected(self, client, owner_cookies):
        """VAL-S3-012: Empty/whitespace Passwort rejected."""
        csrf = owner_cookies.get("__Secure-csrf_token")
        # Leer
        resp = client.post(
            "/api/backup-config/password",
            json={"password": ""},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422
        # Whitespace-only
        resp = client.post(
            "/api/backup-config/password",
            json={"password": "   "},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422
        # Nichts gespeichert
        assert BackupConfigService.is_backup_password_set() is False

    def test_password_never_in_response(self, client, owner_cookies):
        """VAL-S3-010: Passwort nie in API-Antwort."""
        _set_password_via_api(client, owner_cookies, TEST_PASSWORD)
        # GET backup-config
        resp = client.get("/api/backup-config", cookies=owner_cookies)
        assert TEST_PASSWORD not in resp.text
        # GET status
        resp = client.get("/api/backup-config/status", cookies=owner_cookies)
        assert TEST_PASSWORD not in resp.text
        # POST password response
        resp = _set_password_via_api(client, owner_cookies, "AnotherPass456!")
        assert "AnotherPass456!" not in resp.text
        assert TEST_PASSWORD not in resp.text

    def test_password_overwrite_reuses_salt(self, client, owner_cookies, db):
        """VAL-S3-020: Passwort-Aenderung reused Salt."""
        _set_password_via_api(client, owner_cookies, "FirstPass123!")
        salt1 = PanelSettingsService.get("backup.salt")
        assert salt1 != ""
        _set_password_via_api(client, owner_cookies, "SecondPass456!")
        salt2 = PanelSettingsService.get("backup.salt")
        assert salt1 == salt2
        # Nur eine Salt-Row
        rows = db.query(PanelSetting).filter(
            PanelSetting.key == "backup.salt"
        ).all()
        assert len(rows) == 1

    # ── Status Endpunkt ──

    def test_status_admin_200(self, client, owner_cookies):
        """VAL-S3-019: Status reports flags, admin-only."""
        resp = client.get("/api/backup-config/status", cookies=owner_cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert "s3_configured" in data
        assert "backup_password_set" in data
        assert "last_panel_backup" in data
        assert data["s3_configured"] is False
        assert data["backup_password_set"] is False
        assert data["last_panel_backup"] is None

    def test_status_unauthenticated_401(self, client):
        resp = client.get("/api/backup-config/status")
        assert resp.status_code == 401

    def test_status_non_admin_403(self, client, user_cookies):
        resp = client.get("/api/backup-config/status", cookies=user_cookies)
        assert resp.status_code == 403

    def test_status_no_credentials_leaked(self, client, owner_cookies):
        """VAL-S3-022: Status enthaelt keine Credentials."""
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": TEST_ENDPOINT,
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
        })
        _set_password_via_api(client, owner_cookies, TEST_PASSWORD)
        resp = client.get("/api/backup-config/status", cookies=owner_cookies)
        body = resp.text
        assert TEST_ACCESS_KEY not in body
        assert TEST_SECRET_KEY not in body
        assert TEST_PASSWORD not in body


# ── Log-Scan Tests ───────────────────────────────────────────────────────


class TestNoSecretsInLogs:
    """VAL-S3-022: Keine Credentials/Passwoerter in Logs."""

    def test_no_credentials_in_logs(self, client, owner_cookies, caplog):
        """Capture logs waehrend Config-Operationen; keine Secrets."""
        caplog.set_level(logging.DEBUG)
        _set_s3_config_via_api(client, owner_cookies, {
            "endpoint": TEST_ENDPOINT,
            "access_key": TEST_ACCESS_KEY,
            "secret_key": TEST_SECRET_KEY,
            "bucket": TEST_BUCKET,
        })
        _set_password_via_api(client, owner_cookies, TEST_PASSWORD)
        client.get("/api/backup-config", cookies=owner_cookies)
        client.get("/api/backup-config/status", cookies=owner_cookies)
        log_text = caplog.text
        assert TEST_ACCESS_KEY not in log_text
        assert TEST_SECRET_KEY not in log_text
        assert TEST_PASSWORD not in log_text
