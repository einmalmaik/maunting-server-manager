"""AES-256-GCM frame streaming — wire-compatible with DIS backup encrypt-stream.

Frame format (identical to dis-sidecar/server.mjs):
  stream := MAGIC || frame*
  frame  := [4-byte big-endian length][12-byte nonce][ciphertext + 16-byte GCM tag]
  AAD    := [8-byte big-endian frame index][1 byte final flag]
  length = 12 + len(ciphertext_with_tag)
  Plaintext chunk size: 64 KiB

Security audit 2026-09-22 — why the AAD exists: v1 frames each carried their own
GCM tag but nothing tied a frame to its *position*. Anyone who could write where
backups are stored could reorder frames, duplicate them or cut the stream short,
and every frame still verified. A restore then wrote authentic-but-rearranged
plaintext over the live data, silently. v2 binds index and end-of-stream, so all
three manipulations fail the tag check. Streams without the magic are still read
as v1 (old backups stay restorable); v1 is never written.

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

STREAM_MAGIC_V2 = b"MSMBKP2\n"

# A frame never holds more than one plaintext chunk plus nonce and tag. Without
# this ceiling a forged length field makes the reader buffer up to 4 GiB.
MAX_FRAME_LEN = NONCE_LEN + STREAM_CHUNK + TAG_LEN + 64


def _frame_aad(index: int, final: bool) -> bytes:
    """Binds a frame to its position in the stream."""
    return struct.pack(">Q", index) + (b"\x01" if final else b"\x00")


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
    index = 0
    yield STREAM_MAGIC_V2
    with open(path, "rb") as f:
        while True:
            piece = f.read(STREAM_CHUNK)
            if not piece:
                break
            yield _encrypt_frame(aesgcm, piece, index, final=False)
            index += 1
    # The final frame is the receipt that the stream was not cut short.
    yield _encrypt_frame(aesgcm, b"", index, final=True)


def encrypt_bytes_iter(chunks: Iterator[bytes], key: bytes) -> Iterator[bytes]:
    """Buffer input chunks to STREAM_CHUNK and yield encrypted frames."""
    aesgcm = AESGCM(key)
    buf = bytearray()
    index = 0
    yield STREAM_MAGIC_V2
    for chunk in chunks:
        if not chunk:
            continue
        buf.extend(chunk)
        while len(buf) >= STREAM_CHUNK:
            piece = bytes(buf[:STREAM_CHUNK])
            del buf[:STREAM_CHUNK]
            yield _encrypt_frame(aesgcm, piece, index, final=False)
            index += 1
    yield _encrypt_frame(aesgcm, bytes(buf), index, final=True)


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
    version: int | None = None
    index = 0
    saw_final = False

    for chunk in _iter_bytes():
        buffer.extend(chunk)

        # Determine the stream version once, from the leading magic. A v1 stream
        # opens with a frame length field whose high byte cannot be 'M' (0x4D):
        # that would mean a ~1.3 GB frame, far beyond MAX_FRAME_LEN.
        if version is None:
            if len(buffer) < len(STREAM_MAGIC_V2):
                continue
            if bytes(buffer[: len(STREAM_MAGIC_V2)]) == STREAM_MAGIC_V2:
                version = 2
                del buffer[: len(STREAM_MAGIC_V2)]
            else:
                version = 1

        while True:
            if len(buffer) < FRAME_LEN_FIELD:
                break
            frame_len = struct.unpack(">I", buffer[:FRAME_LEN_FIELD])[0]
            if frame_len < NONCE_LEN or frame_len > MAX_FRAME_LEN:
                raise StreamCryptoError("malformed frame length")
            total = FRAME_LEN_FIELD + frame_len
            if len(buffer) < total:
                break
            nonce = bytes(buffer[FRAME_LEN_FIELD : FRAME_LEN_FIELD + NONCE_LEN])
            ct = bytes(buffer[FRAME_LEN_FIELD + NONCE_LEN : total])
            del buffer[:total]
            if len(ct) < TAG_LEN:
                raise StreamCryptoError("ciphertext too short")
            if saw_final:
                raise StreamCryptoError("data after final frame")

            if version == 1:
                try:
                    yield aesgcm.decrypt(nonce, ct, None)
                except Exception as exc:
                    raise StreamCryptoError("decryption failed") from exc
                continue

            # v2: the frame must authenticate at exactly this position. It is
            # either a body frame or the final one — nothing else verifies.
            try:
                plain = aesgcm.decrypt(nonce, ct, _frame_aad(index, final=False))
            except Exception:
                try:
                    plain = aesgcm.decrypt(nonce, ct, _frame_aad(index, final=True))
                except Exception as exc:
                    raise StreamCryptoError("decryption failed") from exc
                saw_final = True
            index += 1
            yield plain

    if buffer:
        raise StreamCryptoError("truncated ciphertext")
    if version == 2 and not saw_final:
        # The one manipulation a per-frame tag cannot notice on its own.
        raise StreamCryptoError("truncated ciphertext")


def _encrypt_frame(aesgcm: AESGCM, plaintext: bytes, index: int, *, final: bool) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    # AAD binds the frame to its position and to whether it ends the stream.
    # Context binding (server ID, backup ID) is handled at a higher layer.
    ct = aesgcm.encrypt(nonce, plaintext, _frame_aad(index, final))  # ciphertext || tag
    frame_len = NONCE_LEN + len(ct)
    return struct.pack(">I", frame_len) + nonce + ct
