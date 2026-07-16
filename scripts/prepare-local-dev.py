"""Prepare gitignored MSM development credentials and local TLS material.

The script never prints generated credentials. It only writes them to files
that are ignored by Git and used by the local development processes.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND_ENV = ROOT / "backend" / ".env"
AGENT_DIR = ROOT / "msm-agent"
AGENT_ENV = AGENT_DIR / ".env"
DEV_DIR = AGENT_DIR / ".dev"


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return lines, values


def _set_value(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[index] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def _prepare_agent_env() -> None:
    lines, values = _read_env(AGENT_ENV)
    token = values.get("MSM_AGENT_TOKEN", "")
    placeholders = {"change-me-to-a-long-random-secret", "dev-agent-token-change-me"}
    if len(token) < 24 or token in placeholders:
        _set_value(lines, "MSM_AGENT_TOKEN", secrets.token_urlsafe(36))

    defaults = {
        "MSM_AGENT_HOST": "127.0.0.1",
        "MSM_AGENT_PORT": "9000",
        "MSM_SERVERS_DIR": "./servers",
        "MSM_AGENT_LOG_LEVEL": "INFO",
        "MSM_MANAGED_POSTGRES_DATA_DIR": "./postgres",
    }
    for key, value in defaults.items():
        if not values.get(key):
            _set_value(lines, key, value)

    if os.name == "nt" and not values.get("MSM_DOCKER_HOST"):
        _set_value(lines, "MSM_DOCKER_HOST", "npipe:////./pipe/docker_engine")

    AGENT_ENV.parent.mkdir(parents=True, exist_ok=True)
    AGENT_ENV.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _prepare_backend_env() -> None:
    lines, _values = _read_env(BACKEND_ENV)
    database_url = "postgresql+psycopg2://msm:msm_dev_pass@127.0.0.1:15434/msm"
    database_url_async = "postgresql+asyncpg://msm:msm_dev_pass@127.0.0.1:15434/msm"
    _set_value(lines, "MSM_DATABASE_URL", database_url)
    _set_value(lines, "MSM_DATABASE_URL_ASYNC", database_url_async)
    BACKEND_ENV.parent.mkdir(parents=True, exist_ok=True)
    BACKEND_ENV.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _prepare_tls() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    DEV_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = DEV_DIR / "node-2.crt"
    key_path = DEV_DIR / "node-2.key"

    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        now = datetime.now(timezone.utc)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "MSM Local Node 2")]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        try:
            key_path.chmod(0o600)
        except OSError:
            pass

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    (DEV_DIR / "node-2-fingerprint.txt").write_text(fingerprint + "\n", encoding="ascii")
    return fingerprint


def _prepare_node2_env() -> None:
    node_dir = DEV_DIR / "node-2"
    env_path = node_dir / ".env"
    lines, values = _read_env(env_path)
    token = values.get("MSM_AGENT_TOKEN", "")
    if len(token) < 24:
        _set_value(lines, "MSM_AGENT_TOKEN", secrets.token_urlsafe(36))
    node_values = {
        "MSM_AGENT_HOST": "127.0.0.1",
        "MSM_AGENT_PORT": "9001",
        "MSM_SERVERS_DIR": "./servers",
        "MSM_AGENT_LOG_LEVEL": "INFO",
        "MSM_TLS_CERTFILE": "../node-2.crt",
        "MSM_TLS_KEYFILE": "../node-2.key",
        "MSM_MANAGED_POSTGRES_CONTAINER_NAME": "msm-postgres-node-2",
        "MSM_MANAGED_POSTGRES_PORT": "15433",
        "MSM_MANAGED_POSTGRES_DATA_DIR": "./postgres",
    }
    if os.name == "nt":
        node_values["MSM_DOCKER_HOST"] = "npipe:////./pipe/docker_engine"
    for key, value in node_values.items():
        _set_value(lines, key, value)
    node_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _prepare_minio_env() -> None:
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    path = DEV_DIR / "minio.env"
    if path.exists():
        return
    access_key = f"msmdev{secrets.token_hex(5)}"
    secret_key = secrets.token_urlsafe(36)
    path.write_text(
        f"MINIO_ROOT_USER={access_key}\nMINIO_ROOT_PASSWORD={secret_key}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--multi-node", action="store_true")
    args = parser.parse_args()

    _prepare_backend_env()
    _prepare_agent_env()
    if args.multi_node:
        fingerprint = _prepare_tls()
        _prepare_node2_env()
        _prepare_minio_env()
        print("Lokaler Test-Node vorbereitet (TLS-Fingerprint ist nicht geheim):")
        print(fingerprint)
        print("Node-2-Token: msm-agent/.dev/node-2/.env (wird nicht ausgegeben)")
        print("MinIO-Zugangsdaten: msm-agent/.dev/minio.env (wird nicht ausgegeben)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
