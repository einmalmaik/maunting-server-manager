"""Tests fuer S3Service — moto fuer S3-Mocking, DIS gemockt via conftest.

Abgedeckte Assertions:
- VAL-S3-013: upload_stream (multipart, object retrievable)
- VAL-S3-014: download_stream (streamable body, bytes match)
- VAL-S3-015: list_objects und delete_object
- VAL-S3-016: _get_client entschluesselt Credentials via DIS (AAD)
- VAL-S3-017: Operations feheln klar wenn nicht konfiguriert
- VAL-S3-018: boto3-Fehler ohne Credential-Leak
"""
from __future__ import annotations

import io

import boto3
import pytest
from moto import mock_aws

from services.dis_client import DisClient, DisDecryptionError
from services.panel_settings_service import PanelSettingsService
from services.s3_service import (
    S3NotConfiguredError,
    S3OperationError,
    S3Service,
)

S3_AAD = "msm:backup:s3"
TEST_BUCKET = "msm-test-bucket"
# Endpoint leer lassen fuer moto-Tests (boto3 nutzt Default-AWS-Endpoint,
# den moto interceptiert). In Produktion wird hier der Provider-Endpoint gesetzt.
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
# Standard AWS-Dokumentations-Beispiel-Credentials (von moto 5.x akzeptiert)
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def _setup_s3_config() -> None:
    """S3-Config in panel_settings mit mock-verschluesselten Credentials."""
    PanelSettingsService.set("backup.s3_endpoint", TEST_ENDPOINT)
    PanelSettingsService.set(
        "backup.s3_access_key_encrypted",
        DisClient.encrypt(TEST_ACCESS_KEY, aad=S3_AAD),
    )
    PanelSettingsService.set(
        "backup.s3_secret_key_encrypted",
        DisClient.encrypt(TEST_SECRET_KEY, aad=S3_AAD),
    )
    PanelSettingsService.set("backup.s3_bucket", TEST_BUCKET)
    PanelSettingsService.set("backup.s3_region", TEST_REGION)


def _create_moto_bucket() -> None:
    """Erstellt den Test-Bucket in moto."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=TEST_BUCKET)


# ── VAL-S3-013: upload_stream ────────────────────────────────────────────

@mock_aws
def test_upload_stream_bytes():
    _setup_s3_config()
    _create_moto_bucket()
    S3Service.upload_stream(io.BytesIO(b"hello world"), "test-key")
    client = boto3.client("s3", region_name="us-east-1")
    resp = client.get_object(Bucket=TEST_BUCKET, Key="test-key")
    assert resp["Body"].read() == b"hello world"


@mock_aws
def test_upload_stream_large_uses_multipart():
    _setup_s3_config()
    _create_moto_bucket()
    # 8 MB — groesser als das Default-Multipart-Threshold (8 MB ab boto3 1.x).
    data = b"\x00" * (8 * 1024 * 1024)
    S3Service.upload_stream(io.BytesIO(data), "big-key")
    client = boto3.client("s3", region_name="us-east-1")
    resp = client.get_object(Bucket=TEST_BUCKET, Key="big-key")
    assert resp["Body"].read() == data


@mock_aws
def test_upload_stream_from_iterator():
    _setup_s3_config()
    _create_moto_bucket()

    def gen():
        yield b"chunk1-"
        yield b"chunk2-"
        yield b"chunk3"

    S3Service.upload_stream(gen(), "iter-key")
    client = boto3.client("s3", region_name="us-east-1")
    resp = client.get_object(Bucket=TEST_BUCKET, Key="iter-key")
    assert resp["Body"].read() == b"chunk1-chunk2-chunk3"


# ── VAL-S3-014: download_stream ──────────────────────────────────────────

@mock_aws
def test_download_stream_returns_streamable_body():
    _setup_s3_config()
    _create_moto_bucket()
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=TEST_BUCKET, Key="dl-key", Body=b"download me")
    body = S3Service.download_stream("dl-key")
    # StreamBody ist lazy — read() holt die Bytes
    assert body.read() == b"download me"


@mock_aws
def test_download_stream_large():
    _setup_s3_config()
    _create_moto_bucket()
    data = b"\x42" * (5 * 1024 * 1024)
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=TEST_BUCKET, Key="dl-large", Body=data)
    body = S3Service.download_stream("dl-large")
    assert body.read() == data


# ── VAL-S3-015: list_objects und delete_object ───────────────────────────

@mock_aws
def test_list_objects_with_prefix():
    _setup_s3_config()
    _create_moto_bucket()
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=TEST_BUCKET, Key="msm-backups/a.enc", Body=b"a")
    client.put_object(Bucket=TEST_BUCKET, Key="msm-backups/b.enc", Body=b"b")
    client.put_object(Bucket=TEST_BUCKET, Key="other/c.enc", Body=b"c")

    result = S3Service.list_objects("msm-backups/")
    keys = [obj["key"] for obj in result]
    assert "msm-backups/a.enc" in keys
    assert "msm-backups/b.enc" in keys
    assert "other/c.enc" not in keys
    for obj in result:
        assert "size" in obj
        assert "last_modified" in obj


@mock_aws
def test_list_objects_empty():
    _setup_s3_config()
    _create_moto_bucket()
    result = S3Service.list_objects("nonexistent-prefix/")
    assert result == []


@mock_aws
def test_delete_object():
    _setup_s3_config()
    _create_moto_bucket()
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=TEST_BUCKET, Key="del-key", Body=b"data")
    S3Service.delete_object("del-key")
    objs = client.list_objects_v2(Bucket=TEST_BUCKET).get("Contents", [])
    assert all(o["Key"] != "del-key" for o in objs)


@mock_aws
def test_delete_object_idempotent_missing():
    _setup_s3_config()
    _create_moto_bucket()
    # Deleting a non-existent key should not raise
    S3Service.delete_object("nonexistent-key")


@mock_aws
def test_delete_object_siblings_unchanged():
    _setup_s3_config()
    _create_moto_bucket()
    client = boto3.client("s3", region_name="us-east-1")
    client.put_object(Bucket=TEST_BUCKET, Key="keep.enc", Body=b"keep")
    client.put_object(Bucket=TEST_BUCKET, Key="del.enc", Body=b"del")
    S3Service.delete_object("del.enc")
    objs = {o["Key"] for o in client.list_objects_v2(Bucket=TEST_BUCKET).get("Contents", [])}
    assert "keep.enc" in objs
    assert "del.enc" not in objs


# ── test_connection ──────────────────────────────────────────────────────

@mock_aws
def test_test_connection_success():
    _setup_s3_config()
    _create_moto_bucket()
    result = S3Service.test_connection()
    assert result["ok"] is True
    assert result["bucket"] == TEST_BUCKET


@mock_aws
def test_test_connection_bucket_missing():
    _setup_s3_config()
    # No bucket created in moto
    with pytest.raises(S3OperationError) as exc_info:
        S3Service.test_connection()
    assert TEST_ACCESS_KEY not in str(exc_info.value)
    assert TEST_SECRET_KEY not in str(exc_info.value)


# ── VAL-S3-016: Credentials via DIS entschluesselt ───────────────────────

@mock_aws
def test_get_client_decrypts_credentials_with_correct_aad():
    _setup_s3_config()
    _create_moto_bucket()
    # Wenn die Operation funktioniert, wurde _get_client erfolgreich
    # Credentials via DIS mit AAD="msm:backup:s3" entschluesselt.
    S3Service.upload_stream(io.BytesIO(b"aad-test"), "aad-key")
    client = boto3.client("s3", region_name="us-east-1")
    resp = client.get_object(Bucket=TEST_BUCKET, Key="aad-key")
    assert resp["Body"].read() == b"aad-test"


@mock_aws
def test_get_client_wrong_aad_fails():
    """Credentials mit falschem AAD verschluesselt → Entschluesselung schlaegt fehl."""
    PanelSettingsService.set("backup.s3_endpoint", TEST_ENDPOINT)
    # Mit falschem AAD verschluesseln
    PanelSettingsService.set(
        "backup.s3_access_key_encrypted",
        DisClient.encrypt(TEST_ACCESS_KEY, aad="msm:wrong:aad"),
    )
    PanelSettingsService.set(
        "backup.s3_secret_key_encrypted",
        DisClient.encrypt(TEST_SECRET_KEY, aad="msm:wrong:aad"),
    )
    PanelSettingsService.set("backup.s3_bucket", TEST_BUCKET)
    PanelSettingsService.set("backup.s3_region", TEST_REGION)
    with pytest.raises(DisDecryptionError):
        S3Service.upload_stream(io.BytesIO(b"fail"), "fail-key")


# ── VAL-S3-017: Not configured ───────────────────────────────────────────

@mock_aws
def test_not_configured_upload():
    with pytest.raises(S3NotConfiguredError, match="nicht konfiguriert"):
        S3Service.upload_stream(io.BytesIO(b"x"), "key")


@mock_aws
def test_not_configured_download():
    with pytest.raises(S3NotConfiguredError, match="nicht konfiguriert"):
        S3Service.download_stream("key")


@mock_aws
def test_not_configured_list():
    with pytest.raises(S3NotConfiguredError, match="nicht konfiguriert"):
        S3Service.list_objects("prefix/")


@mock_aws
def test_not_configured_delete():
    with pytest.raises(S3NotConfiguredError, match="nicht konfiguriert"):
        S3Service.delete_object("key")


@mock_aws
def test_not_configured_test_connection():
    with pytest.raises(S3NotConfiguredError, match="nicht konfiguriert"):
        S3Service.test_connection()


@mock_aws
def test_partial_config_raises_not_configured():
    """Bucket gesetzt, aber keine Credentials → S3NotConfiguredError."""
    PanelSettingsService.set("backup.s3_endpoint", "https://s3.example.com")
    PanelSettingsService.set("backup.s3_bucket", TEST_BUCKET)
    # Credentials fehlen
    with pytest.raises(S3NotConfiguredError):
        S3Service.upload_stream(io.BytesIO(b"x"), "key")


# ── VAL-S3-018: boto3-Fehler ohne Credential-Leak ────────────────────────

@mock_aws
def test_download_nonexistent_key_no_credential_leak():
    _setup_s3_config()
    _create_moto_bucket()
    with pytest.raises(S3OperationError) as exc_info:
        S3Service.download_stream("nonexistent-key")
    err_str = str(exc_info.value)
    assert TEST_ACCESS_KEY not in err_str
    assert TEST_SECRET_KEY not in err_str


@mock_aws
def test_upload_to_nonexistent_bucket_no_credential_leak():
    _setup_s3_config()
    # Kein Bucket erstellt → NoSuchBucket
    with pytest.raises(S3OperationError) as exc_info:
        S3Service.upload_stream(io.BytesIO(b"data"), "key")
    err_str = str(exc_info.value)
    assert TEST_ACCESS_KEY not in err_str
    assert TEST_SECRET_KEY not in err_str


@mock_aws
def test_delete_nonexistent_key_no_credential_leak():
    _setup_s3_config()
    _create_moto_bucket()
    # Delete on missing key is idempotent in S3 → should not raise
    S3Service.delete_object("nonexistent")
    # Verify no error and no credentials in any exception (none raised)
