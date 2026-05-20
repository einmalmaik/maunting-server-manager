#!/usr/bin/env python3
"""
Maunting Server Manager — Python Setup Helper
Wird von install.sh aufgerufen, kann aber auch standalone laufen.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def setup_backend(msm_dir: Path) -> None:
    backend = msm_dir / "backend"
    venv = backend / "venv"

    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)], cwd=backend)

    pip = venv / "bin" / "pip"
    run([str(pip), "install", "--upgrade", "pip"], cwd=backend)
    run([str(pip), "install", "-r", "requirements.txt"], cwd=backend)

    # Init DB
    alembic = venv / "bin" / "alembic"
    if alembic.exists():
        try:
            run([str(alembic), "upgrade", "head"], cwd=backend)
        except subprocess.CalledProcessError:
            print("[WARN] Alembic fehlgeschlagen, erstelle Tabellen direkt...")
            python = venv / "bin" / "python"
            run([str(python), "-c",
                 "from database import engine, Base; from models import *; Base.metadata.create_all(engine)"],
                cwd=backend)


def setup_frontend(msm_dir: Path) -> None:
    frontend = msm_dir / "frontend"
    run(["npm", "install"], cwd=frontend)
    run(["npm", "run", "build"], cwd=frontend)


def setup_caddy(msm_dir: Path, domain: str | None) -> None:
    template = msm_dir / "Caddyfile.template"
    caddyfile = Path("/etc/caddy/Caddyfile")

    content = template.read_text(encoding="utf-8")
    content = content.replace("{{DOMAIN}}", domain or "localhost")

    if not domain:
        content = content.replace(":443", ":80")
        content = content.replace("tls internal", "# tls internal")

    caddyfile.write_text(content, encoding="utf-8")
    run(["systemctl", "reload", "caddy"])


def setup_systemd(msm_dir: Path) -> None:
    template = msm_dir / "msm.service.template"
    content = template.read_text(encoding="utf-8")
    content = content.replace("{{MSM_DIR}}", str(msm_dir))

    service_path = Path("/etc/systemd/system/msm-panel.service")
    service_path.write_text(content, encoding="utf-8")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "msm-panel.service"])


def main() -> int:
    parser = argparse.ArgumentParser(description="MSM Setup Helper")
    parser.add_argument("--dir", default="/opt/msm", help="MSM Installationsverzeichnis")
    parser.add_argument("--domain", default=None, help="Domain für Caddy (leer = IP)")
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    msm_dir = Path(args.dir).resolve()

    if not (msm_dir / "backend").exists():
        print(f"[ERR] Backend nicht gefunden in {msm_dir}")
        return 1

    if not args.skip_backend:
        setup_backend(msm_dir)

    if not args.skip_frontend:
        setup_frontend(msm_dir)

    setup_caddy(msm_dir, args.domain)
    setup_systemd(msm_dir)

    print("[OK] Setup abgeschlossen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
