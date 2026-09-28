"""AES-GCM frame format must match DIS backup encrypt-stream."""

from __future__ import annotations

import base64
import io
import struct

import pytest

from services.stream_crypto import (
    NONCE_LEN,
    STREAM_CHUNK,
    STREAM_MAGIC_V2,
    StreamCryptoError,
    decode_key_b64,
    decrypt_stream_to_file,
    encrypt_file_frames,
)


def _zerlege(stream: bytes) -> tuple[bytes, list[bytes]]:
    """Trennt Magic und Frames eines v2-Stroms."""
    assert stream.startswith(STREAM_MAGIC_V2)
    rest = stream[len(STREAM_MAGIC_V2) :]
    frames = []
    off = 0
    while off + 4 <= len(rest):
        flen = struct.unpack(">I", rest[off : off + 4])[0]
        frames.append(rest[off : off + 4 + flen])
        off += 4 + flen
    assert off == len(rest)
    return STREAM_MAGIC_V2, frames


def test_roundtrip_small(tmp_path):
    key = b"k" * 32
    key_b64 = base64.b64encode(key).decode()
    assert decode_key_b64(key_b64) == key

    src = tmp_path / "plain.bin"
    plain = b"hello multi-node backup stream " * 100
    src.write_bytes(plain)

    frames = b"".join(encrypt_file_frames(str(src), key))
    assert len(frames) > 0
    assert frames.startswith(STREAM_MAGIC_V2)
    flen = struct.unpack(">I", frames[len(STREAM_MAGIC_V2) : len(STREAM_MAGIC_V2) + 4])[0]
    assert flen >= NONCE_LEN + 16

    out = tmp_path / "out.bin"
    decrypt_stream_to_file(io.BytesIO(frames), key, str(out))
    assert out.read_bytes() == plain


def test_roundtrip_multi_chunk(tmp_path):
    key = b"z" * 32
    src = tmp_path / "big.bin"
    plain = bytes((i % 256) for i in range(STREAM_CHUNK * 2 + 123))
    src.write_bytes(plain)
    frames = b"".join(encrypt_file_frames(str(src), key))
    _, zerlegt = _zerlege(frames)
    # drei Nutzlast-Frames plus das abschliessende Quittungsframe
    assert len(zerlegt) >= 4

    out = tmp_path / "out.bin"
    decrypt_stream_to_file(io.BytesIO(frames), key, str(out))
    assert out.read_bytes() == plain


def test_wrong_key_fails(tmp_path):
    key = b"a" * 32
    bad = b"b" * 32
    src = tmp_path / "p.bin"
    src.write_bytes(b"secret payload data here!!")
    frames = b"".join(encrypt_file_frames(str(src), key))
    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(frames), bad, str(out))


# ── Positionsbindung der Frames (Audit 22.09.2026) ──────────────────────────
#
# Bis v1 trug jedes Frame seinen eigenen GCM-Tag, aber nichts band es an seinen
# Platz. Wer an den abgelegten Backup-Strom herankam, konnte Frames tauschen,
# doppeln oder den Strom abschneiden — jedes einzelne Frame prüfte weiterhin
# sauber, und die Wiederherstellung schrieb echten, aber falsch
# zusammengesetzten Klartext über die Live-Daten.


def _mehrfach_frames(tmp_path, key: bytes) -> tuple[bytes, list[bytes], bytes]:
    src = tmp_path / "quelle.bin"
    plain = bytes((i % 251) for i in range(STREAM_CHUNK * 2 + 77))
    src.write_bytes(plain)
    strom = b"".join(encrypt_file_frames(str(src), key))
    magic, frames = _zerlege(strom)
    return magic, frames, plain


def test_vertauschte_frames_werden_abgewiesen(tmp_path):
    key = b"r" * 32
    magic, frames, _ = _mehrfach_frames(tmp_path, key)
    getauscht = magic + frames[1] + frames[0] + b"".join(frames[2:])

    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(getauscht), key, str(out))


def test_gedoppeltes_frame_wird_abgewiesen(tmp_path):
    key = b"d" * 32
    magic, frames, _ = _mehrfach_frames(tmp_path, key)
    gedoppelt = magic + frames[0] + frames[0] + b"".join(frames[1:])

    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(gedoppelt), key, str(out))


def test_abgeschnittener_strom_wird_abgewiesen(tmp_path):
    """Das Quittungsframe fehlt — die eine Manipulation, die ein Tag je Frame
    grundsätzlich nicht bemerken kann."""
    key = b"t" * 32
    magic, frames, _ = _mehrfach_frames(tmp_path, key)
    gekuerzt = magic + b"".join(frames[:-2])

    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(gekuerzt), key, str(out))


def test_angehaengtes_frame_wird_abgewiesen(tmp_path):
    key = b"a" * 32
    magic, frames, _ = _mehrfach_frames(tmp_path, key)
    verlaengert = magic + b"".join(frames) + frames[0]

    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(verlaengert), key, str(out))


def test_v1_altbestand_bleibt_lesbar(tmp_path):
    """Backups aus der Zeit vor der Positionsbindung müssen weiter aufgehen —
    sonst ist die Härtung selbst der Datenverlust."""
    import os
    import struct as _struct

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = b"v" * 32
    aesgcm = AESGCM(key)
    plain = b"altes backup aus der v1-zeit" * 50

    alt = b""
    for off in range(0, len(plain), STREAM_CHUNK):
        stueck = plain[off : off + STREAM_CHUNK]
        nonce = os.urandom(NONCE_LEN)
        ct = aesgcm.encrypt(nonce, stueck, None)
        alt += _struct.pack(">I", NONCE_LEN + len(ct)) + nonce + ct

    out = tmp_path / "o.bin"
    decrypt_stream_to_file(io.BytesIO(alt), key, str(out))
    assert out.read_bytes() == plain


def test_ueberlange_framelaenge_wird_sofort_abgewiesen(tmp_path):
    """Ein erfundenes Längenfeld darf keinen 4-GiB-Puffer aufziehen."""
    key = b"l" * 32
    bomb = STREAM_MAGIC_V2 + struct.pack(">I", 0xFFFFFFFF) + b"\x00" * 64

    out = tmp_path / "o.bin"
    with pytest.raises(StreamCryptoError):
        decrypt_stream_to_file(io.BytesIO(bomb), key, str(out))
