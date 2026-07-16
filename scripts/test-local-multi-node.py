"""Safe local smoke test for the two-agent development topology."""

from __future__ import annotations

import argparse
import base64
import io
import json
import secrets
import ssl
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "msm-agent"


def _env_value(path: Path, key: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        current_key, value = line.split("=", 1)
        if current_key.strip() == key:
            return value.strip().strip('"').strip("'")
    raise RuntimeError(f"{key} fehlt in {path}")


def _request(
    base: str,
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict | None = None,
    context: ssl.SSLContext | None = None,
) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload: object = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _wait_ready(
    base: str,
    path: str,
    *,
    context: ssl.SSLContext | None = None,
    timeout_seconds: int = 30,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            status, _ = _request(base, path, context=context)
            if status == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError(f"Dienst nicht bereit: {base}{path}")


def _check_agent(
    name: str,
    base: str,
    token: str,
    context: ssl.SSLContext | None = None,
) -> None:
    status, health = _request(base, "/health", context=context)
    _expect(status == 200, f"{name}: Health HTTP {status}")
    _expect(isinstance(health, dict) and health.get("docker_connected") is True, f"{name}: Docker nicht verbunden")

    status, _ = _request(base, "/containers", context=context)
    _expect(status == 401, f"{name}: unauthentifizierter Zugriff wurde nicht blockiert")

    status, containers = _request(base, "/containers", token=token, context=context)
    _expect(status == 200 and isinstance(containers, list), f"{name}: authentifizierte Kommunikation fehlgeschlagen")

    status, port_result = _request(
        base,
        "/runtime/ports/check",
        token=token,
        method="POST",
        body={"ports": [{"port": 29991, "protocol": "tcp", "role": "smoke"}]},
        context=context,
    )
    _expect(status == 200 and isinstance(port_result, dict), f"{name}: Port-Pruefung fehlgeschlagen")

    server_id = "local-smoke"
    status, _ = _request(
        base, f"/files/server-root?server_id={server_id}", token=token, method="PUT", context=context
    )
    _expect(status in {200, 409}, f"{name}: Server-Verzeichnis konnte nicht angelegt werden")
    try:
        status, result = _request(
            base,
            "/containers/ephemeral/run",
            token=token,
            method="POST",
            body={"image": "alpine:3.20", "command": ["sh", "-c", "printf msm-node-ok"], "timeout": 120},
            context=context,
        )
        _expect(status == 200 and isinstance(result, dict) and result.get("ok") is True, f"{name}: Testcontainer fehlgeschlagen")
        _expect(result.get("stdout") == "msm-node-ok", f"{name}: unerwartete Testcontainer-Ausgabe")
    finally:
        _request(
            base, f"/files/server-root?server_id={server_id}", token=token, method="DELETE", context=context
        )


def _minio_client(port: int):
    import boto3
    from botocore.config import Config

    minio_env = AGENT_DIR / ".dev" / "minio.env"
    access_key = _env_value(minio_env, "MINIO_ROOT_USER")
    secret_key = _env_value(minio_env, "MINIO_ROOT_PASSWORD")
    return boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{port}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def _ensure_minio_bucket(port: int) -> None:
    client = _minio_client(port)
    buckets = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
    if "msm-backups" not in buckets:
        client.create_bucket(Bucket="msm-backups")


def _check_backup_roundtrip(
    base: str,
    token: str,
    minio_port: int,
    context: ssl.SSLContext,
) -> None:
    minio_env = AGENT_DIR / ".dev" / "minio.env"
    access_key = _env_value(minio_env, "MINIO_ROOT_USER")
    secret_key = _env_value(minio_env, "MINIO_ROOT_PASSWORD")
    server_id = "local-backup-smoke"
    object_key = "smoke/local-backup-smoke.enc"
    original = f"msm-backup-roundtrip-{secrets.token_hex(8)}"
    encryption_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    s3_config = {
        "endpoint": f"http://127.0.0.1:{minio_port}",
        "access_key": access_key,
        "secret_key": secret_key,
        "bucket": "msm-backups",
        "region": "us-east-1",
    }
    s3 = _minio_client(minio_port)

    _request(base, f"/files/server-root?server_id={server_id}", token=token, method="DELETE", context=context)
    try:
        status, _ = _request(base, f"/files/server-root?server_id={server_id}", token=token, method="PUT", context=context)
        _expect(status == 200, "Backup-Testverzeichnis konnte nicht angelegt werden")
        status, _ = _request(
            base,
            f"/files/write?server_id={server_id}&path=probe.txt",
            token=token,
            method="POST",
            body={"content": original},
            context=context,
        )
        _expect(status == 200, "Backup-Testdatei konnte nicht geschrieben werden")

        status, result = _request(
            base,
            "/backup/create",
            token=token,
            method="POST",
            body={
                "server_id": server_id,
                "s3_config": s3_config,
                "encryption_key": encryption_key,
                "s3_key": object_key,
            },
            context=context,
        )
        _expect(status == 200 and isinstance(result, dict) and result.get("ok") is True, "Verschluesseltes Agent-S3-Backup fehlgeschlagen")

        encrypted = s3.get_object(Bucket="msm-backups", Key=object_key)["Body"].read()
        _expect(original.encode("utf-8") not in encrypted, "Backup enthaelt Klartext")
        try:
            with tarfile.open(fileobj=io.BytesIO(encrypted), mode="r:*"):
                pass
        except tarfile.TarError:
            pass
        else:
            raise RuntimeError("Verschluesseltes Backup ist als TAR lesbar")

        status, _ = _request(
            base,
            f"/files/write?server_id={server_id}&path=probe.txt",
            token=token,
            method="POST",
            body={"content": "mutated"},
            context=context,
        )
        _expect(status == 200, "Backup-Testdatei konnte nicht veraendert werden")

        status, result = _request(
            base,
            "/backup/restore",
            token=token,
            method="POST",
            body={
                "server_id": server_id,
                "s3_config": s3_config,
                "encryption_key": encryption_key,
                "s3_key": object_key,
            },
            context=context,
        )
        _expect(status == 200 and isinstance(result, dict) and result.get("ok") is True, "Agent-S3-Restore fehlgeschlagen")
        status, restored = _request(
            base,
            f"/files/read?server_id={server_id}&path=probe.txt",
            token=token,
            context=context,
        )
        _expect(status == 200 and isinstance(restored, dict) and restored.get("content") == original, "Restore-Datenintegritaet stimmt nicht")
    finally:
        try:
            s3.delete_object(Bucket="msm-backups", Key=object_key)
        except Exception:
            pass
        _request(base, f"/files/server-root?server_id={server_id}", token=token, method="DELETE", context=context)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--local-port", type=int, default=9000)
    parser.add_argument("--remote-port", type=int, default=9001)
    parser.add_argument("--minio-port", type=int, default=9002)
    args = parser.parse_args()

    local_token = _env_value(AGENT_DIR / ".env", "MSM_AGENT_TOKEN")
    remote_token = _env_value(AGENT_DIR / ".dev" / "node-2" / ".env", "MSM_AGENT_TOKEN")
    cert_path = AGENT_DIR / ".dev" / "node-2.crt"
    if not cert_path.exists():
        raise RuntimeError("TLS-Zertifikat fehlt; zuerst start-dev-multi-node.bat starten")
    tls_context = ssl.create_default_context(cafile=str(cert_path))

    backend_base = f"http://127.0.0.1:{args.backend_port}"
    local_base = f"http://127.0.0.1:{args.local_port}"
    remote_base = f"https://127.0.0.1:{args.remote_port}"
    minio_base = f"http://127.0.0.1:{args.minio_port}"

    _wait_ready(backend_base, "/api/health")
    _wait_ready(local_base, "/health")
    _wait_ready(remote_base, "/health", context=tls_context)
    _wait_ready(minio_base, "/minio/health/live")

    status, backend = _request(backend_base, "/api/health")
    _expect(status == 200 and isinstance(backend, dict) and backend.get("status") == "ok", "Backend ist nicht bereit")

    _check_agent("Local Node", local_base, local_token)
    _check_agent("Simulated Node", remote_base, remote_token, tls_context)

    status, _ = _request(minio_base, "/minio/health/live")
    _expect(status == 200, "MinIO ist nicht bereit")
    _ensure_minio_bucket(args.minio_port)
    _check_backup_roundtrip(remote_base, remote_token, args.minio_port, tls_context)

    print("OK: Backend, beide Agents, Auth, Docker, Dateien, Ports, TLS und MinIO funktionieren.")
    print("Der lokale MinIO-Bucket 'msm-backups' ist bereit.")
    print("OK: Verschluesseltes Agent-Backup und Restore sind dateninteger; Klartext war nicht lesbar.")
    print("Die Testcontainer und Testverzeichnisse wurden entfernt.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        raise SystemExit(1)
