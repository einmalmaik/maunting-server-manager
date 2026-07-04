"""Tests fuer BackupCryptoService — DIS gemockt via conftest (reversible Streams).

Abgedeckte Assertions:
- VAL-DIS-022: Backend round-trip (encrypt_file_stream → decrypt_to_file == original)
- Key lifecycle: init_key, invalidate_key
- Streaming via httpx (mocked in conftest)
- Empty file round-trip
- Tampered frames → DecryptionFailed
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
import uuid

import pytest

from services.backup_crypto_service import BackupCryptoError, BackupCryptoService

TEST_PASSWORD = "my-backup-password-123"
TEST_SALT = base64.b64encode(b"salt-salt-salt-salt").decode()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ── init_key / invalidate_key ─────────────────────────────────────────────

def test_init_key_returns_uuid():
    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    assert key_id is not None
    # key_id ist eine UUID
    parsed = uuid.UUID(key_id)
    assert str(parsed) == key_id


def test_init_key_multiple_distinct():
    k1 = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    k2 = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    assert k1 != k2


def test_invalidate_key_succeeds():
    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    BackupCryptoService.invalidate_key(key_id)  # should not raise


def test_invalidate_key_idempotent():
    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    BackupCryptoService.invalidate_key(key_id)
    # Second invalidation should not raise (DIS is idempotent)
    BackupCryptoService.invalidate_key(key_id)


# ── VAL-DIS-022: Round-trip ───────────────────────────────────────────────

def test_encrypt_decrypt_roundtrip_small(tmp_path):
    data = b"Hello, backup world! \x00\x01\x02 binary"
    input_path = tmp_path / "original.bin"
    output_path = tmp_path / "decrypted.bin"
    input_path.write_bytes(data)

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        assert len(encrypted) > 0
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    assert output_path.exists()
    assert output_path.read_bytes() == data
    assert _sha256(str(input_path)) == _sha256(str(output_path))


def test_encrypt_decrypt_roundtrip_large(tmp_path):
    data = b"\x42" * (3 * 1024 * 1024)  # 3 MB — multiple 64KB chunks
    input_path = tmp_path / "large.bin"
    output_path = tmp_path / "large_decrypted.bin"
    input_path.write_bytes(data)

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    assert output_path.read_bytes() == data


def test_encrypt_decrypt_roundtrip_empty(tmp_path):
    input_path = tmp_path / "empty.bin"
    output_path = tmp_path / "empty_decrypted.bin"
    input_path.write_bytes(b"")

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    assert output_path.read_bytes() == b""


def test_encrypt_decrypt_roundtrip_single_byte(tmp_path):
    input_path = tmp_path / "one.bin"
    output_path = tmp_path / "one_decrypted.bin"
    input_path.write_bytes(b"\x00")

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    assert output_path.read_bytes() == b"\x00"


# ── Frame-Format ──────────────────────────────────────────────────────────

def test_encrypt_output_frame_format(tmp_path):
    """Verschluesselte Frames folgen [4-byte BE length][12-byte nonce][ct]."""
    input_path = tmp_path / "frame.bin"
    input_path.write_bytes(b"A" * 100)

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = b"".join(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    # Parse first frame
    assert len(encrypted) >= 4
    frame_len = struct.unpack(">I", encrypted[:4])[0]
    assert frame_len >= 12  # nonce + ciphertext
    assert len(encrypted) >= 4 + frame_len
    # nonce is 12 bytes
    nonce = encrypted[4:16]
    assert len(nonce) == 12
    # ciphertext follows
    ct = encrypted[16:4 + frame_len]
    assert len(ct) == frame_len - 12


# ── Key lifecycle (init before, invalidate after) ─────────────────────────

def test_key_lifecycle_init_before_invalidate_after(tmp_path):
    """init_key vor Operation, invalidate_key nach Operation (try/finally)."""
    input_path = tmp_path / "lifecycle.bin"
    output_path = tmp_path / "lifecycle_decrypted.bin"
    input_path.write_bytes(b"lifecycle test")

    key_id = None
    try:
        key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
        encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))
    finally:
        if key_id:
            BackupCryptoService.invalidate_key(key_id)

    assert output_path.read_bytes() == b"lifecycle test"


def test_encrypt_after_invalidate_fails(tmp_path):
    """Nach invalidate_key schlagen encrypt/decrypt fehl."""
    input_path = tmp_path / "fail.bin"
    input_path.write_bytes(b"should fail")

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    BackupCryptoService.invalidate_key(key_id)

    with pytest.raises(BackupCryptoError):
        list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))


def test_decrypt_after_invalidate_fails(tmp_path):
    """Nach invalidate_key schlagen decrypt fehl."""
    input_path = tmp_path / "orig.bin"
    input_path.write_bytes(b"encrypt first")

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    encrypted = list(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
    BackupCryptoService.invalidate_key(key_id)

    output_path = tmp_path / "fail_decrypted.bin"
    with pytest.raises(BackupCryptoError):
        BackupCryptoService.decrypt_to_file(iter(encrypted), key_id, str(output_path))


# ── Tampered frames ───────────────────────────────────────────────────────

def test_decrypt_tampered_frame_fails(tmp_path):
    """Manipulierte Frames → DecryptionFailed."""
    input_path = tmp_path / "tamper.bin"
    input_path.write_bytes(b"tamper me")

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = b"".join(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
        # Flip a bit in the ciphertext
        tampered = bytearray(encrypted)
        tampered[-1] ^= 0xFF
        output_path = tmp_path / "tampered_out.bin"
        with pytest.raises(BackupCryptoError):
            BackupCryptoService.decrypt_to_file(iter([bytes(tampered)]), key_id, str(output_path))
    finally:
        BackupCryptoService.invalidate_key(key_id)


# ── Encrypt produces non-plaintext output ─────────────────────────────────

def test_encrypt_output_not_plaintext(tmp_path):
    """Verschluesselte Bytes sind nicht der Plaintext."""
    data = b"sensitive data here"
    input_path = tmp_path / "secret.bin"
    input_path.write_bytes(data)

    key_id = BackupCryptoService.init_key(TEST_PASSWORD, TEST_SALT)
    try:
        encrypted = b"".join(BackupCryptoService.encrypt_file_stream(str(input_path), key_id))
    finally:
        BackupCryptoService.invalidate_key(key_id)

    # First 4 bytes are frame length (not plaintext)
    assert encrypted[:4] != data[:4]
    # The full encrypted output should not contain the plaintext as-is
    assert data not in encrypted
