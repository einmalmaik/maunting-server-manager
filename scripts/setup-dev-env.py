"""Dev-Envs erzeugen: backend/.env, dis-sidecar/.env, searxng-sidecar/.env,
livekit-sidecar/.env — mit konsistenten Secrets und localhost-/Docker-Namen.

- Läuft auf Windows UND Linux:  python scripts/setup-dev-env.py
- Überschreibt keine bestehenden .env-Dateien (außer --force).
- Secrets: SECRET_KEY, DIS-Salt, DIS-Token, LiveKit-Key/Secret werden
  zufällig erzeugt und backend <-> sidecar-konsistent geschrieben.
- backend/.env nutzt DOCKER-interne Hostnamen (panel-db, dis, redis,
  searxng), weil das Backend im Linux-Container läuft. Vom Windows-Host
  bleibt alles über localhost erreichbar (Ports siehe docker-compose.dev.yml).
- MSM_PANEL_URL=http://localhost:3000 → Frontend-Dev (Vite) + CORS.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ENV = ROOT / "backend" / ".env"
DIS_ENV = ROOT / "dis-sidecar" / ".env"
SEARXNG_ENV = ROOT / "searxng-sidecar" / ".env"
LIVEKIT_ENV = ROOT / "livekit-sidecar" / ".env"
AGENT_ENV = ROOT / "msm-agent" / ".env"
FRONTEND_ENV = ROOT / "frontend" / ".env.local"

DB_PASS = "msm_dev_pass"


def _read(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    vals: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return lines, vals


def _set(lines: list[str], key: str, value: str) -> None:
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Bestehende .env-Dateien neu schreiben")
    args = ap.parse_args()

    for p in (BACKEND_ENV, DIS_ENV, SEARXNG_ENV, LIVEKIT_ENV, AGENT_ENV, FRONTEND_ENV):
        if p.exists() and not args.force:
            print(f"behalten: {p.relative_to(ROOT)} (use --force zum Neuerzeugen)")
        if p.exists() and not args.force:
            continue

    # Bestehende Werte wiederverwenden (kein Re-Key bei erneutem Lauf)
    _, b = _read(BACKEND_ENV)
    _, d = _read(DIS_ENV)

    secret = b.get("MSM_SECRET_KEY") or d.get("MSM_SECRET_KEY") or secrets.token_urlsafe(48)
    salt = b.get("MSM_DIS_SALT") or d.get("MSM_DIS_SALT") or secrets.token_urlsafe(32)
    dis_token = (
        b.get("MSM_DIS_SIDECAR_TOKEN") or d.get("MSM_DIS_SIDECAR_TOKEN") or secrets.token_urlsafe(32)
    )
    livekit_key = b.get("MSM_LIVEKIT_API_KEY") or f"devkey{secrets.token_hex(6)}"
    livekit_secret = b.get("MSM_LIVEKIT_API_SECRET") or secrets.token_urlsafe(32)

    def fresh(path: Path) -> bool:
        return args.force or not path.exists()

    if fresh(BACKEND_ENV):
        lines, vals = _read(BACKEND_ENV)
        db = f"postgresql+psycopg2://msm:{DB_PASS}@panel-db:5432/msm"
        dba = f"postgresql+asyncpg://msm:{DB_PASS}@panel-db:5432/msm"
        defaults = {
            "MSM_APP_NAME": '"Maunting Service Manager"',
            "MSM_DEBUG": "true",
            "MSM_DATABASE_URL": db,
            "MSM_DATABASE_URL_ASYNC": dba,
            "MSM_SECRET_KEY": secret,
            "MSM_DIS_SIDECAR_URL": "http://dis:9100",
            "MSM_DIS_SIDECAR_TOKEN": dis_token,
            "MSM_DIS_SALT": salt,
            "MSM_PANEL_URL": "http://localhost:3000",
            "MSM_API_URL": "http://localhost:8000",
            "MSM_SERVE_FRONTEND": "false",
            "MSM_SERVERS_DIR": "/data/servers",
            "MSM_VAULT_BLOB_DIR": "/data/vault-blobs",
            "MSM_BLUEPRINTS_DIR": "/opt/msm/blueprints/community",
            "MSM_PANEL_CONFIG_DIR": "/opt/msm",
            "MSM_PANEL_BACKUP_DIR": "/opt/msm/backups/panel",
            "MSM_LOCAL_AGENT_ENV_FILE": "/opt/msm/msm-agent/.env",
            "MSM_REDIS_URL": "redis://redis:6379",
            "MSM_SEARXNG_URL": "http://searxng:8080",
            "MSM_LIVEKIT_API_KEY": livekit_key,
            "MSM_LIVEKIT_API_SECRET": livekit_secret,
            "MSM_MANAGED_POSTGRES_HOST": "127.0.0.1",
            "MSM_MANAGED_POSTGRES_PORT": "15432",
        }
        for k, v in defaults.items():
            if args.force or not vals.get(k):
                _set(lines, k, v)
        _write(BACKEND_ENV, lines)
        print(f"geschrieben: {BACKEND_ENV.relative_to(ROOT)}")

    if fresh(DIS_ENV):
        lines, vals = _read(DIS_ENV)
        for k, v in {
            "MSM_SECRET_KEY": secret,
            "MSM_DIS_SALT": salt,
            "MSM_DIS_SIDECAR_TOKEN": dis_token,
            "MSM_DIS_SIDECAR_PORT": "9100",
            "NODE_ENV": "development",
        }.items():
            if args.force or not vals.get(k):
                _set(lines, k, v)
        _write(DIS_ENV, lines)
        print(f"geschrieben: {DIS_ENV.relative_to(ROOT)}")

    if fresh(SEARXNG_ENV):
        lines, vals = _read(SEARXNG_ENV)
        if args.force or not vals.get("SEARXNG_SECRET_KEY"):
            _set(lines, "SEARXNG_SECRET_KEY", secrets.token_urlsafe(32))
        _write(SEARXNG_ENV, lines)
        print(f"geschrieben: {SEARXNG_ENV.relative_to(ROOT)}")

    if fresh(LIVEKIT_ENV):
        LIVEKIT_ENV.write_text(
            f'LIVEKIT_KEYS="{livekit_key}: {livekit_secret}"\nNODE_IP="127.0.0.1"\n',
            encoding="utf-8",
        )
        try:
            LIVEKIT_ENV.chmod(0o600)
        except OSError:
            pass
        print(f"geschrieben: {LIVEKIT_ENV.relative_to(ROOT)}")

    if fresh(AGENT_ENV):
        lines, vals = _read(AGENT_ENV)
        if not vals.get("MSM_AGENT_TOKEN") or len(vals.get("MSM_AGENT_TOKEN", "")) < 24:
            _set(lines, "MSM_AGENT_TOKEN", secrets.token_urlsafe(36))
        for k, v in {
            "MSM_AGENT_HOST": "127.0.0.1",
            "MSM_AGENT_PORT": "9000",
            "MSM_SERVERS_DIR": "./servers",
            "MSM_AGENT_LOG_LEVEL": "INFO",
        }.items():
            if not vals.get(k):
                _set(lines, k, v)
        _write(AGENT_ENV, lines)
        print(f"geschrieben: {AGENT_ENV.relative_to(ROOT)}")

    if fresh(FRONTEND_ENV):
        # Leer = gleiche Origin + Vite-Proxy /api → localhost:8000
        FRONTEND_ENV.write_text("VITE_API_URL=\nVITE_WS_URL=\n", encoding="utf-8")
        print(f"geschrieben: {FRONTEND_ENV.relative_to(ROOT)}")

    print("OK: Dev-Envs konsistent (backend <-> DIS <-> LiveKit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
