"""SHA-256 certificate fingerprint pinning for MSM Agent TLS.

Self-signed agent certs: skip public CA trust, pin exact DER SHA-256.
Never logs PEMs, keys, or tokens.
"""

from __future__ import annotations

import hashlib
import logging
import ssl
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def normalize_fingerprint(value: str | None) -> str:
    """Lowercase hex SHA-256 without colons/spaces."""
    if not value:
        return ""
    cleaned = (
        value.strip()
        .lower()
        .replace(":", "")
        .replace(" ", "")
        .replace("-", "")
    )
    # Accept optional "sha256/" prefix from some tools
    if cleaned.startswith("sha256/"):
        cleaned = cleaned[7:]
    return cleaned


def fingerprint_from_der(der_bytes: bytes) -> str:
    return hashlib.sha256(der_bytes).hexdigest()


def fingerprint_from_pem(pem: str | bytes) -> str:
    if isinstance(pem, bytes):
        pem_str = pem.decode("ascii", errors="strict")
    else:
        pem_str = pem
    der = ssl.PEM_cert_to_DER_cert(pem_str)
    return fingerprint_from_der(der)


def parse_host_port(base_url: str) -> tuple[str, int]:
    """Extract host and port from agent base URL (http/https)."""
    raw = (base_url or "").strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host:
        raise ValueError("invalid agent host")
    if parsed.port:
        return host, int(parsed.port)
    return host, 443 if parsed.scheme == "https" else 80


def build_pinned_ssl_context(base_url: str, expected_fingerprint: str) -> ssl.SSLContext:
    """Fetch peer cert, verify SHA-256 pin, return context that trusts only that cert.

    Raises ValueError on fingerprint mismatch or connection failure.
    """
    expected = normalize_fingerprint(expected_fingerprint)
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("invalid TLS fingerprint (expect 64 hex chars SHA-256)")

    host, port = parse_host_port(base_url)
    try:
        pem = ssl.get_server_certificate((host, port), timeout=10)
    except OSError as exc:
        logger.warning("TLS cert fetch failed host=%s port=%s", host, port)
        raise ValueError("could not fetch agent TLS certificate") from exc

    actual = fingerprint_from_pem(pem)
    if actual != expected:
        logger.warning(
            "TLS fingerprint mismatch host=%s (expected pin set, actual differs)",
            host,
        )
        raise ValueError("TLS certificate fingerprint mismatch")

    # Trust only this certificate (self-signed OK); hostname not required to match CN.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=pem)
    return ctx
