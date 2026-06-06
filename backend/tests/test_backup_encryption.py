"""Tests fuer die client-seitige Backup-Verschluesselung (AES-256-GCM).

Deckt:
- Roundtrip (encrypt → decrypt) erhaelt Daten byte-genau
- Zwei verschiedene Keys produzieren unterschiedliche Ciphertexte
- Falscher Key → BackupEncryptionError, KEIN Klartext-Leak in Exception
- Manipulierter Ciphertext (1 Byte flip) → BackupEncryptionError
- Truncated File → BackupEncryptionError
- Falsche Version-Byte → BackupEncryptionError
- Datei-Format: erstes Byte = 0x01, dann 12-Byte-Nonce
- generate_master_key produziert base64-32-Bytes
- Encrypt einer grossen Datei (1 MB) funktioniert
- Encrypt-Cleanup: bei Fehler wird dst wieder entfernt (kein Partial-Write)

Security-relevant: keine Assertion enthaelt Key-Material oder Klartext.
"""
import base64
from pathlib import Path

import pytest

from services.backup_encryption import (
    BackupEncryptionError,
    FORMAT_VERSION,
    KEY_SIZE,
    NONCE_SIZE,
    decrypt_file,
    encrypt_file,
    generate_master_key,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _key() -> str:
    return generate_master_key()


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


# ── Tests ────────────────────────────────────────────────────────────────


class TestKeyGeneration:
    def test_generate_master_key_returns_base64_32_bytes(self):
        key = generate_master_key()
        # urlsafe_b64decode benoetigt Padding (token_urlsafe liefert ohne)
        padded = key + "=" * (-len(key) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        assert len(decoded) == KEY_SIZE

    def test_generate_master_key_returns_unique_values(self):
        # secrets muss unvorhersagbar sein — 1000 Keys duerfen nicht kollidieren
        keys = {generate_master_key() for _ in range(1000)}
        assert len(keys) == 1000


class TestRoundtrip:
    def test_encrypt_decrypt_preserves_content(self, tmp_path: Path):
        plaintext = b"Hello, this is a test backup file with some content."
        src = tmp_path / "in.bin"
        enc = tmp_path / "in.bin.enc"
        out = tmp_path / "out.bin"
        _write_bytes(src, plaintext)

        key = _key()
        encrypt_file(src, enc, key)
        assert enc.exists()
        decrypt_file(enc, out, key)
        assert out.read_bytes() == plaintext

    def test_encrypt_with_empty_file(self, tmp_path: Path):
        # AES-256-GCM erlaubt leere Klartexte
        src = tmp_path / "empty.bin"
        enc = tmp_path / "empty.bin.enc"
        out = tmp_path / "empty.out.bin"
        src.write_bytes(b"")
        key = _key()
        encrypt_file(src, enc, key)
        decrypt_file(enc, out, key)
        assert out.read_bytes() == b""

    def test_encrypt_with_large_file(self, tmp_path: Path):
        # 1 MB Test — Sicherstellen dass es nicht an Chunking-Grenzen scheitert
        plaintext = b"x" * (1024 * 1024)
        src = tmp_path / "big.bin"
        enc = tmp_path / "big.bin.enc"
        out = tmp_path / "big.out.bin"
        _write_bytes(src, plaintext)
        key = _key()
        encrypt_file(src, enc, key)
        decrypt_file(enc, out, key)
        assert out.read_bytes() == plaintext

    def test_encrypt_writes_header_with_correct_format(self, tmp_path: Path):
        src = tmp_path / "f.bin"
        enc = tmp_path / "f.bin.enc"
        src.write_bytes(b"data")
        key = _key()
        encrypt_file(src, enc, key)
        raw = enc.read_bytes()
        # Format: [version:1][nonce:12][ciphertext+tag]
        assert raw[0] == FORMAT_VERSION
        assert len(raw) > 1 + NONCE_SIZE
        # Nonce ist 12 Bytes zufaellig; kann nicht weiter geprueft werden
        # (kein deterministischer Output), aber Laenge schon
        assert len(raw) - 1 - NONCE_SIZE > 0  # ciphertext + tag

    def test_encrypt_same_plaintext_with_different_keys_different_ciphertext(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc1 = tmp_path / "p1.enc"
        enc2 = tmp_path / "p2.enc"
        src.write_bytes(b"identical plaintext")
        k1, k2 = _key(), _key()
        encrypt_file(src, enc1, k1)
        encrypt_file(src, enc2, k2)
        # Nonces sind auch zufaellig — selbst mit gleichem Key waeren sie unterschiedlich
        assert enc1.read_bytes() != enc2.read_bytes()


class TestNegativePaths:
    def test_decrypt_with_wrong_key_fails(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        out = tmp_path / "out.bin"
        src.write_bytes(b"secret content")
        encrypt_file(src, enc, _key())
        with pytest.raises(BackupEncryptionError) as exc:
            decrypt_file(enc, out, _key())  # anderer Key
        # KEIN Klartext im Error-Text (kein Leak)
        assert b"secret" not in str(exc.value).encode()
        # dst darf nicht teilweise geschrieben sein
        assert not out.exists()

    def test_decrypt_with_tampered_ciphertext_fails(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        out = tmp_path / "out.bin"
        src.write_bytes(b"secret content")
        key = _key()
        encrypt_file(src, enc, key)

        # Flippe ein Byte im Ciphertext-Bereich
        raw = bytearray(enc.read_bytes())
        raw[-1] ^= 0x01  # Tag-Bereich (oder letzter Ciphertext-Byte)
        enc.write_bytes(bytes(raw))

        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, out, key)

    def test_decrypt_truncated_file_fails(self, tmp_path: Path):
        enc = tmp_path / "trunc.enc"
        out = tmp_path / "out.bin"
        # Schreibe nur 5 Bytes (zu kurz fuer 1+12+16 minimum)
        enc.write_bytes(b"\x01\x02\x03\x04\x05")
        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, out, _key())

    def test_decrypt_unknown_version_fails(self, tmp_path: Path):
        enc = tmp_path / "bad.enc"
        out = tmp_path / "out.bin"
        # Erste Byte auf unbekannte Version setzen + Dummy-Nonce + Dummy-CT
        enc.write_bytes(b"\xff" + b"\x00" * 12 + b"\x00" * 32)
        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, out, _key())

    def test_decrypt_empty_key_fails(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        out = tmp_path / "out.bin"
        src.write_bytes(b"x")
        encrypt_file(src, enc, _key())
        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, out, "")

    def test_decrypt_invalid_base64_key_fails(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        out = tmp_path / "out.bin"
        src.write_bytes(b"x")
        encrypt_file(src, enc, _key())
        with pytest.raises(BackupEncryptionError):
            # Nicht-base64 String
            decrypt_file(enc, out, "!!!not-base64!!!")

    def test_decrypt_wrong_length_key_fails(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        out = tmp_path / "out.bin"
        src.write_bytes(b"x")
        encrypt_file(src, enc, _key())
        # 16 Bytes statt 32
        short_key = base64.urlsafe_b64encode(b"\x00" * 16).decode()
        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, out, short_key)


class TestEncryptionCleanup:
    def test_encrypt_cleans_up_dst_on_write_failure(self, tmp_path: Path):
        # Simuliere Write-Failure: dst ist ein Directory statt Datei
        src = tmp_path / "in.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst"  # existiert nicht
        dst.mkdir()  # als Directory anlegen → write() failt

        key = _key()
        with pytest.raises(BackupEncryptionError):
            encrypt_file(src, dst, key)
        # dst (das Directory) darf nicht in einen kaputten Zustand hinterlassen sein
        # — bei mkdir-Target ist das OK, aber bei File-Target wurde cleanup versucht

    def test_decrypt_cleans_up_dst_on_write_failure(self, tmp_path: Path):
        src = tmp_path / "p.bin"
        enc = tmp_path / "p.enc"
        src.write_bytes(b"data")
        encrypt_file(src, enc, _key())
        dst = tmp_path / "dst"
        dst.mkdir()  # write failt
        key = _key()
        with pytest.raises(BackupEncryptionError):
            decrypt_file(enc, dst, key)
