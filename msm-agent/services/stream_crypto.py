"""AES-256-GCM frame streaming — wire-compatible with DIS backup encrypt-stream.

Frame format (identical to dis-sidecar/server.mjs):
  [4-byte big-endian length][12-byte nonce][ciphertext + 16-byte GCM tag]
  length = 12 + len(ciphertext_with_tag)
  Plaintext chunk size: 64 KiB

Keys are held only in memory for the duration of encrypt/decrypt; never written to disk.
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STREAM_CHUNK = 64 * 1024
NONCE_LEN = 12
TAG_LEN = 16
FRAME_LEN_FIELD = 4


class StreamCryptoError(Exception):
    """Encryption/decryption failed (tamper, wrong key, truncated)."""


def decode_key_b64(key_b64: str) -> bytes:
    import base64

    raw = base64.b64decode(key_b64.strip())
    if len(raw) != 32:
        raise StreamCryptoError("encryption_key must be 32 bytes (AES-256)")
    return raw


def encrypt_file_frames(path: str, key: bytes) -> Iterator[bytes]:
    """Read file, yield DIS-compatible encrypted frames."""
    aesgcm = AESGCM(key)
    with open(path, "rb") as f:
        while True:
            piece = f.read(STREAM_CHUNK)
            if not piece:
                break
            yield _encrypt_frame(aesgcm, piece)


def encrypt_bytes_iter(chunks: Iterator[bytes], key: bytes) -> Iterator[bytes]:
    """Buffer input chunks to STREAM_CHUNK and yield encrypted frames."""
    aesgcm = AESGCM(key)
    buf = bytearray()
    for chunk in chunks:
        if not chunk:
            continue
        buf.extend(chunk)
        while len(buf) >= STREAM_CHUNK:
            piece = bytes(buf[:STREAM_CHUNK])
            del buf[:STREAM_CHUNK]
            yield _encrypt_frame(aesgcm, piece)
    if buf:
        yield _encrypt_frame(aesgcm, bytes(buf))


def decrypt_stream_to_file(encrypted: BinaryIO | Iterator[bytes], key: bytes, out_path: str) -> None:
    """Decrypt DIS frames from stream into out_path (atomic via .tmp)."""
    aesgcm = AESGCM(key)
    tmp = out_path + ".tmp"
    try:
        with open(tmp, "wb") as out:
            for plain in decrypt_frames(encrypted, aesgcm):
                out.write(plain)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def decrypt_frames(source: BinaryIO | Iterator[bytes], aesgcm: AESGCM | None = None, key: bytes | None = None) -> Iterator[bytes]:
    """Yield plaintext chunks from a DIS encrypted frame stream."""
    if aesgcm is None:
        if key is None:
            raise StreamCryptoError("key required")
        aesgcm = AESGCM(key)

    def _iter_bytes() -> Iterator[bytes]:
        if hasattr(source, "read"):
            while True:
                b = source.read(64 * 1024)  # type: ignore[union-attr]
                if not b:
                    break
                yield b
        else:
            yield from source  # type: ignore[misc]

    buffer = bytearray()
    for chunk in _iter_bytes():
        buffer.extend(chunk)
        while True:
            if len(buffer) < FRAME_LEN_FIELD:
                break
            frame_len = struct.unpack(">I", buffer[:FRAME_LEN_FIELD])[0]
            if frame_len < NONCE_LEN:
                raise StreamCryptoError("malformed frame length")
            total = FRAME_LEN_FIELD + frame_len
            if len(buffer) < total:
                break
            nonce = bytes(buffer[FRAME_LEN_FIELD : FRAME_LEN_FIELD + NONCE_LEN])
            ct = bytes(buffer[FRAME_LEN_FIELD + NONCE_LEN : total])
            del buffer[:total]
            if len(ct) < TAG_LEN:
                raise StreamCryptoError("ciphertext too short")
            try:
                yield aesgcm.decrypt(nonce, ct, None)
            except Exception as exc:
                raise StreamCryptoError("decryption failed") from exc
    if buffer:
        raise StreamCryptoError("truncated ciphertext")


def _encrypt_frame(aesgcm: AESGCM, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    ct = aesgcm.encrypt(nonce, plaintext, None)  # ciphertext || tag
    frame_len = NONCE_LEN + len(ct)
    return struct.pack(">I", frame_len) + nonce + ct
