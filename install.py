#!/usr/bin/env python3
"""
Maunting Server Manager — Developer Setup Helper
NUR für lokale Entwicklung. Für Server-Installation: sudo bash install.sh
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

    pip = venv / "bin" / "pip" if os.name != "nt" else venv / "Scripts" / "pip.exe"
    run([str(pip), "install", "--upgrade", "pip"], cwd=backend)
    run([str(pip), "install", "-r", "requirements.txt"], cwd=backend)

    # Init DB
    alembic = venv / "bin" / "alembic" if os.name != "nt" else venv / "Scripts" / "alembic.exe"
    python = venv / "bin" / "python" if os.name != "nt" else venv / "Scripts" / "python.exe"
    if alembic.exists():
        try:
            run([str(alembic), "upgrade", "head"], cwd=backend)
        except subprocess.CalledProcessError:
            print("[WARN] Alembic fehlgeschlagen, erstelle Tabellen direkt...")
            run([str(python), "-c",
                 "from database import engine, Base; from models import *; Base.metadata.create_all(engine)"],
                cwd=backend)


def setup_frontend(msm_dir: Path) -> None:
    frontend = msm_dir / "frontend"
    run(["npm", "install"], cwd=frontend)


def main() -> int:
    parser = argparse.ArgumentParser(description="MSM Dev Setup Helper")
    parser.add_argument("--dir", default=".", help="MSM Projektverzeichnis")
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

    print("[OK] Dev-Setup abgeschlossen.")
    print("     Backend:  cd backend && source venv/bin/activate && uvicorn main:app --reload")
    print("     Frontend: cd frontend && npm run dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
