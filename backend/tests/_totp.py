"""TOTP helper for tests — replaces pyotp with standard-library implementation.

Tests need to generate valid TOTP codes to verify 2FA flows. This module
provides that without pulling in pyotp (which is removed from production).
"""
import base64
import hashlib
import hmac
import struct
import time


def totp_now(secret_b32: str, versatz: int = 0) -> str:
    """Generate the current TOTP code for a base32 secret (replaces pyotp.TOTP(secret).now()).

    Each code is accepted once. A test that needs a second code within the same
    30 s takes the next one (``versatz=1``); the sidecar accepts ±1 step.
    """
    return _code(secret_b32, int(time.time()) // 30 + versatz)


def totp_schritt(secret_b32: str, code: str) -> int | None:
    """The step a code hits (current ±1), like `/totp/verify` in the sidecar."""
    jetzt = int(time.time()) // 30
    eingabe = code.replace(" ", "").strip()
    for schritt in (jetzt, jetzt - 1, jetzt + 1):
        try:
            if _code(secret_b32, schritt) == eingabe:
                return schritt
        except Exception:
            return None
    return None


def _code(secret_b32: str, counter: int) -> str:
    # Pad secret to valid base32 length
    padded = secret_b32 + "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(padded)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**6).zfill(6)


def random_totp_secret() -> str:
    """Generate a random base32 TOTP secret (replaces pyotp.random_base32())."""
    import secrets
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
