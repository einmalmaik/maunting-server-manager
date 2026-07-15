"""Agent-side S3 backup/restore: tar → AES-GCM frames → S3 (and reverse).

Credentials and encryption keys exist only in process memory for the call.
Never log secrets, keys, or passwords.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from services.file_service import PathEscapeError, PathValidationError, server_root
from services.stream_crypto import (
    StreamCryptoError,
    decode_key_b64,
    decrypt_stream_to_file,
    encrypt_file_frames,
)

logger = logging.getLogger(__name__)


class AgentBackupError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _s3_client(s3: dict[str, Any]):
    try:
        import boto3
    except ImportError as exc:
        raise AgentBackupError("boto3 not installed on agent", 503) from exc

    kwargs: dict[str, Any] = {
        "aws_access_key_id": s3["access_key"],
        "aws_secret_access_key": s3["secret_key"],
    }
    if s3.get("endpoint"):
        kwargs["endpoint_url"] = s3["endpoint"]
    if s3.get("region"):
        kwargs["region_name"] = s3["region"]
    return boto3.client("s3", **kwargs)


def create_encrypted_s3_backup(
    server_id: str | int,
    *,
    s3: dict[str, Any],
    encryption_key_b64: str,
    s3_object_key: str,
) -> dict[str, Any]:
    """Tar server dir → encrypt (DIS-compatible) → multipart upload to S3.

    Returns size_bytes, s3_key, sha256 of encrypted object (hex).
    """
    try:
        root = server_root(server_id)
    except (PathValidationError, PathEscapeError) as exc:
        raise AgentBackupError(str(exc), 400) from exc
    if not root.is_dir():
        raise AgentBackupError("Server directory not found", 404)

    for field in ("access_key", "secret_key", "bucket"):
        if not s3.get(field):
            raise AgentBackupError(f"s3_config.{field} required", 400)
    if not s3_object_key or ".." in s3_object_key:
        raise AgentBackupError("invalid s3_key", 400)

    key = decode_key_b64(encryption_key_b64)
    tmp_dir = tempfile.mkdtemp(prefix="msm-agent-bak-")
    tar_path = os.path.join(tmp_dir, "backup.tar.gz")
    try:
        try:
            os.chmod(tmp_dir, 0o700)
        except OSError:
            pass

        # Full tree tar.gz (KISS; panel/pg dumps remain panel-side)
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(str(root), arcname=".")
        try:
            os.chmod(tar_path, 0o600)
        except OSError:
            pass

        client = _s3_client(s3)
        bucket = s3["bucket"]
        hasher = hashlib.sha256()
        size = 0

        # Stream encrypted frames via multipart upload
        from boto3.s3.transfer import TransferConfig

        class _FrameReader:
            """file-like read() over encrypt_file_frames generator."""

            def __init__(self) -> None:
                self._gen = encrypt_file_frames(tar_path, key)
                self._buf = b""
                self._done = False

            def read(self, n: int = -1) -> bytes:
                if n == 0:
                    return b""
                if n < 0:
                    parts = [self._buf]
                    self._buf = b""
                    for fr in self._gen:
                        parts.append(fr)
                    self._done = True
                    data = b"".join(parts)
                    hasher.update(data)
                    nonlocal size
                    size += len(data)
                    return data
                while len(self._buf) < n and not self._done:
                    try:
                        self._buf += next(self._gen)
                    except StopIteration:
                        self._done = True
                data = self._buf[:n]
                self._buf = self._buf[n:]
                if data:
                    hasher.update(data)
                    size += len(data)
                return data

        reader = _FrameReader()
        client.upload_fileobj(
            reader,
            bucket,
            s3_object_key,
            Config=TransferConfig(multipart_threshold=8 * 1024 * 1024),
        )

        return {
            "ok": True,
            "s3_key": s3_object_key,
            "size_bytes": size,
            "sha256": hasher.hexdigest(),
        }
    except StreamCryptoError as exc:
        raise AgentBackupError("encryption failed", 500) from exc
    except AgentBackupError:
        raise
    except Exception as exc:
        logger.warning("agent s3 backup failed: %s", type(exc).__name__)
        raise AgentBackupError("backup to S3 failed", 502) from exc
    finally:
        # Zero-ish: drop key reference; wipe temp files immediately
        key = b"\x00" * 32
        shutil.rmtree(tmp_dir, ignore_errors=True)


def restore_encrypted_s3_backup(
    server_id: str | int,
    *,
    s3: dict[str, Any],
    encryption_key_b64: str,
    s3_object_key: str,
) -> dict[str, Any]:
    """Download from S3 → decrypt → extract into server directory."""
    try:
        root = server_root(server_id)
    except (PathValidationError, PathEscapeError) as exc:
        raise AgentBackupError(str(exc), 400) from exc

    for field in ("access_key", "secret_key", "bucket"):
        if not s3.get(field):
            raise AgentBackupError(f"s3_config.{field} required", 400)
    if not s3_object_key:
        raise AgentBackupError("s3_key required", 400)

    key = decode_key_b64(encryption_key_b64)
    tmp_dir = tempfile.mkdtemp(prefix="msm-agent-rst-")
    enc_path = os.path.join(tmp_dir, "backup.enc")
    tar_path = os.path.join(tmp_dir, "backup.tar.gz")
    try:
        try:
            os.chmod(tmp_dir, 0o700)
        except OSError:
            pass

        client = _s3_client(s3)
        client.download_file(s3["bucket"], s3_object_key, enc_path)

        with open(enc_path, "rb") as enc_f:
            decrypt_stream_to_file(enc_f, key, tar_path)
        try:
            os.chmod(tar_path, 0o600)
        except OSError:
            pass

        # Replace server root contents
        if root.exists():
            # Move aside then extract (caller should stop containers first)
            backup_old = root.parent / f"{root.name}_pre_restore"
            if backup_old.exists():
                shutil.rmtree(backup_old, ignore_errors=True)
            shutil.move(str(root), str(backup_old))
        root.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tar_path, "r:gz") as tar:
            # Safe extract: refuse absolute paths / path escape
            for member in tar.getmembers():
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
                    raise AgentBackupError("unsafe path in archive", 400)
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=str(root), filter=tarfile.data_filter)
            else:
                tar.extractall(path=str(root))

        # cleanup pre_restore on success
        backup_old = root.parent / f"{root.name}_pre_restore"
        if backup_old.exists():
            shutil.rmtree(backup_old, ignore_errors=True)

        return {"ok": True}
    except StreamCryptoError as exc:
        raise AgentBackupError("decryption failed", 400) from exc
    except AgentBackupError:
        raise
    except Exception as exc:
        logger.warning("agent s3 restore failed: %s", type(exc).__name__)
        # best-effort rollback
        backup_old = root.parent / f"{root.name}_pre_restore"
        if backup_old.exists() and not root.exists():
            try:
                shutil.move(str(backup_old), str(root))
            except OSError:
                pass
        raise AgentBackupError("restore from S3 failed", 502) from exc
    finally:
        key = b"\x00" * 32
        shutil.rmtree(tmp_dir, ignore_errors=True)
