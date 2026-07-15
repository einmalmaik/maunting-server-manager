"""AES-GCM frame format must match DIS backup encrypt-stream."""

from __future__ import annotations

import base64
import io
import struct

import pytest

from services.stream_crypto import (
    NONCE_LEN,
    STREAM_CHUNK,
    StreamCryptoError,
    decode_key_b64,
    decrypt_stream_to_file,
    encrypt_file_frames,
)


def test_roundtrip_small(tmp_path):
    key = b"k" * 32
    key_b64 = base64.b64encode(key).decode()
    assert decode_key_b64(key_b64) == key

    src = tmp_path / "plain.bin"
    plain = b"hello multi-node backup stream " * 100
    src.write_bytes(plain)

    frames = b"".join(encrypt_file_frames(str(src), key))
    assert len(frames) > 0
    flen = struct.unpack(">I", frames[:4])[0]
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
    count = 0
    off = 0
    while off + 4 <= len(frames):
        flen = struct.unpack(">I", frames[off : off + 4])[0]
        off += 4 + flen
        count += 1
    assert count >= 3

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
