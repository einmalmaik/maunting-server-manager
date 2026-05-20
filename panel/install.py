#!/usr/bin/env python3
"""
Maunting Server Panel — One-Click Installer
Anfängerfreundliche Installation für Linux (Debian/Ubuntu).

Usage:
    sudo python3 install.py
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parent
ROOT_DIR = PANEL_DIR.parent

# ── Colors ────────────────────────────────────────────────────────────────────
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"


def _print(msg: str, color: str = C_RESET) -> None:
    print(f"{color}{msg}{C_RESET}")


def _step(n: int, total: int, title: str) -> None:
    _print(f"\n[{n}/{total}] {title}", f"{C_BOLD}{C_CYAN}")


def _ok(msg: str) -> None:
    _print(f"  {C_GREEN}✓{C_RESET} {msg}")


def _warn(msg: str) -> None:
    _print(f"  {C_YELLOW}⚠{C_RESET} {msg}")


def _err(msg: str) -> None:
    _print(f"  {C_RED}✗{C_RESET} {msg}")


def _ask(msg: str, default: str = "") -> str:
    prompt = f"{C_CYAN}?{C_RESET} {msg}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    val = input(prompt).strip()
    return val if val else default


def _ask_yes_no(msg: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"{C_CYAN}?{C_RESET} {msg}{suffix}: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "ja")


def _run(cmd: list[str] | str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = cmd.split()
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _python_version_ok() -> bool:
    major, minor = sys.version_info[:2]
    return (major, minor) >= (3, 11)


# ── 1. Welcome & System Check ───────────────────────────────────────────────

def step_1_welcome() -> None:
    _print("=" * 60, C_BOLD)
    _print("  Maunting Server Panel — Installation", f"{C_BOLD}{C_CYAN}")
    _print("  Einfach. Sicher. Schritt für Schritt.", C_DIM)
    _print("=" * 60, C_BOLD)
    _print("")
    _print("Dieser Installer richtet das Panel auf deinem Linux-Server ein.")
    _print("Du wirst nach ein paar Einstellungen gefragt — alles andere")
    _print("erledigt das Skript automatisch.")
    _print("")
    if not _ask_yes_no("Installation starten?", True):
        _print("Abgebrochen.", C_YELLOW)
        sys.exit(0)


# ── 2. Check Requirements ───────────────────────────────────────────────────

def step_2_requirements() -> bool:
    _step(2, 10, "System-Check")

    ok = True

    if not _python_version_ok():
        _err(f"Python {sys.version_info.major}.{sys.version_info.minor} gefunden. Benötigt wird Python >= 3.11.")
        ok = False
    else:
        _ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    if not _has_cmd("node"):
        _err("Node.js nicht gefunden. Bitte installiere Node.js 18+ (z.B. via nvm).")
        ok = False
    else:
        result = _run(["node", "--version"], check=False)
        _ok(f"Node.js {result.stdout.strip()}")

    if not _has_cmd("npm"):
        _err("npm nicht gefunden.")
        ok = False
    else:
        _ok("npm gefunden")

    if not _has_cmd("git"):
        _warn("git nicht gefunden. Wird empfohlen, aber nicht zwingend.")
    else:
        _ok("git gefunden")

    if not ok:
        _print("")
        _print("Bitte behebe die oben genannten Probleme und starte den Installer erneut.", C_RED)
        sys.exit(1)
    return True


# ── 3. MariaDB ──────────────────────────────────────────────────────────────

def step_3_mariadb() -> bool:
    _step(3, 10, "Datenbank (MariaDB)")
    _print("Das Panel unterstützt SQLite (einfach, Datei-basiert) und MariaDB (performant, für Produktion).")
    use_mariadb = _ask_yes_no("MariaDB installieren? (Empfohlen für Produktion)", False)

    if not use_mariadb:
        _ok("SQLite wird als Datenbank verwendet.")
        return False

    _print("MariaDB wird installiert...")
    try:
        _run(["apt-get", "update"])
        _run(["apt-get", "install", "-y", "mariadb-server", "mariadb-client"])
        _run(["systemctl", "enable", "mariadb"])
        _run(["systemctl", "start", "mariadb"])
        _ok("MariaDB installiert und gestartet.")
    except subprocess.CalledProcessError as exc:
        _err(f"MariaDB-Installation fehlgeschlagen: {exc}")
        _warn("Falle zurück auf SQLite.")
        return False

    _print("\nMariaDB Root-Passwort setzen (oder leer lassen, falls bereits gesetzt):")
    root_pw = getpass.getpass("  MariaDB root password: ")

    db_name = _ask("Datenbank-Name", "maunting_panel")
    db_user = _ask("Datenbank-Benutzer", "maunting")
    db_pass = getpass.getpass("  Datenbank-Passwort: ")

    sql = f"""
CREATE DATABASE IF NOT EXISTS {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';
GRANT ALL PRIVILEGES ON {db_name}.* TO '{db_user}'@'localhost';
FLUSH PRIVILEGES;
"""
    try:
        if root_pw:
            _run(["mysql", "-u", "root", "-p" + root_pw, "-e", sql])
        else:
            _run(["mysql", "-u", "root", "-e", sql])
        _ok(f"Datenbank '{db_name}' und Benutzer '{db_user}' erstellt.")
    except subprocess.CalledProcessError as exc:
        _err(f"Datenbank-Einrichtung fehlgeschlagen: {exc}")
        return False

    env_path = PANEL_DIR / ".env"
    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\nDATABASE_URL=mysql+pymysql://{db_user}:{db_pass}@localhost/{db_name}\n")
    _ok("Datenbank-URL in .env geschrieben.")
    return True


# ── 4. phpMyAdmin ───────────────────────────────────────────────────────────

def step_4_phpmyadmin() -> None:
    _step(4, 10, "phpMyAdmin (optional)")
    if not _ask_yes_no("phpMyAdmin installieren?", False):
        return
    try:
        _run(["apt-get", "install", "-y", "phpmyadmin"])
        _ok("phpMyAdmin installiert. Zugriff üblicherweise unter /phpmyadmin.")
    except subprocess.CalledProcessError as exc:
        _err(f"phpMyAdmin-Installation fehlgeschlagen: {exc}")
        _warn("Du kannst phpMyAdmin später manuell installieren.")


# ── 5. Python venv & Dependencies ────────────────────────────────────────────

def step_5_python_deps() -> None:
    _step(5, 10, "Python-Umgebung")
    venv_dir = PANEL_DIR / ".venv"

    if venv_dir.exists():
        _warn("Virtuelle Umgebung existiert bereits.")
        if not _ask_yes_no("Neu erstellen?", False):
            _ok("Bestehende venv wird verwendet.")
        else:
            shutil.rmtree(venv_dir)

    if not venv_dir.exists():
        _print("Virtuelle Umgebung wird erstellt...")
        _run([sys.executable, "-m", "venv", str(venv_dir)])
        _ok("Virtuelle Umgebung erstellt.")

    pip = venv_dir / "bin" / "pip"
    if not pip.exists():
        pip = venv_dir / "Scripts" / "pip.exe"  # Windows-Fallback

    _print("Python-Abhängigkeiten werden installiert (das kann ein paar Minuten dauern)...")
    _run([str(pip), "install", "--upgrade", "pip"])
    req_file = PANEL_DIR / "requirements.txt"
    _run([str(pip), "install", "-r", str(req_file)])
    _ok("Python-Abhängigkeiten installiert.")


# ── 6. Frontend Dependencies ────────────────────────────────────────────────

def step_6_frontend_deps() -> None:
    _step(6, 10, "Frontend-Abhängigkeiten")
    fe_dir = PANEL_DIR / "frontend"
    if not fe_dir.exists():
        _warn("Frontend-Verzeichnis nicht gefunden. Überspringe.")
        return
    _print("npm install wird ausgeführt...")
    _run(["npm", "install"], cwd=fe_dir)
    _ok("Frontend-Abhängigkeiten installiert.")


# ── 7. .env & Konfiguration ─────────────────────────────────────────────────

def step_7_env_config() -> None:
    _step(7, 10, "Konfiguration")
    env_path = PANEL_DIR / ".env"

    if env_path.exists():
        _warn(".env existiert bereits.")
        if not _ask_yes_no("Überschreiben?", False):
            _ok("Bestehende .env beibehalten.")
            return

    _print("\nBitte gib die folgenden Werte an (Enter = Vorschlag übernehmen):")

    secret = os.urandom(32).hex()
    bind_host = _ask("Bind-Host", "0.0.0.0")
    bind_port = _ask("Bind-Port", "8710")
    manager_path = _ask("Conan Manager-Pfad (leer = überspringen)", "")
    dayz_path = _ask("DayZ Manager-Pfad (leer = überspringen)", "")
    https_only = "true" if _ask_yes_no("HTTPS-Only (Secure Cookies)?", False) else "false"

    lines = [
        f"APP_ENV=production",
        f"APP_SECRET_KEY={secret}",
        f"PANEL_BIND_HOST={bind_host}",
        f"PANEL_BIND_PORT={bind_port}",
        f"PANEL_HTTPS_ONLY={https_only}",
    ]
    if manager_path:
        lines.append(f"CONAN_MANAGER_PATH={manager_path}")
    if dayz_path:
        lines.append(f"DAYZ_MANAGER_PATH={dayz_path}")

    # Email config (optional)
    if _ask_yes_no("E-Mail-Konfiguration jetzt einrichten?", False):
        email_provider = _ask("Provider (smtp/resend/none)", "none")
        lines.append(f"EMAIL_PROVIDER={email_provider}")
        if email_provider == "smtp":
            lines.append(f"SMTP_HOST={_ask('SMTP-Host', '')}")
            lines.append(f"SMTP_PORT={_ask('SMTP-Port', '587')}")
            lines.append(f"SMTP_USER={_ask('SMTP-User', '')}")
            smtp_pw = getpass.getpass("  SMTP-Passwort: ")
            lines.append(f"SMTP_PASSWORD={smtp_pw}")
            lines.append(f"EMAIL_FROM={_ask('Absender-E-Mail', '')}")
        elif email_provider == "resend":
            resend_key = getpass.getpass("  Resend API Key: ")
            lines.append(f"RESEND_API_KEY={resend_key}")
            lines.append(f"EMAIL_FROM={_ask('Absender-E-Mail', '')}")

    # DB URL already written in step 3 if MariaDB chosen, otherwise use SQLite
    if not any("DATABASE_URL" in line for line in lines):
        db_path = PANEL_DIR / "data" / "panel.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        lines.append(f"DATABASE_URL=sqlite:///{db_path}")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _ok(".env geschrieben.")


# ── 8. Datenbank-Initialisierung ──────────────────────────────────────────────

def step_8_database() -> None:
    _step(8, 10, "Datenbank-Initialisierung")
    _print("Datenbank-Tabellen werden erstellt...")

    python = PANEL_DIR / ".venv" / "bin" / "python"
    if not python.exists():
        python = PANEL_DIR / ".venv" / "Scripts" / "python.exe"

    script = """
import os, sys
os.chdir(sys.argv[1])
from app.database import engine, Base
from app.models import User, AuditLog, PanelSetting, BackupCode, ServerMembership, AuthThrottle, Server
Base.metadata.create_all(bind=engine)
print('Tables created.')
"""
    try:
        _run([str(python), "-c", script, str(PANEL_DIR)])
        _ok("Datenbank-Tabellen erstellt.")
    except subprocess.CalledProcessError as exc:
        _err(f"Datenbank-Initialisierung fehlgeschlagen: {exc.stderr}")
        sys.exit(1)


# ── 9. Admin-Konto ──────────────────────────────────────────────────────────

def step_9_admin() -> None:
    _step(9, 10, "Admin-Konto erstellen")
    _print("Das erste Konto wird automatisch zum Owner (volle Rechte).\n")

    username = _ask("Admin-Username", "admin")
    while not re.match(r"^[a-zA-Z0-9_]{1,64}$", username):
        _err("Ungültiger Username. Nur Buchstaben, Zahlen und Unterstrich, max. 64 Zeichen.")
        username = _ask("Admin-Username", "admin")

    password = getpass.getpass("  Admin-Passwort: ")
    while len(password) < 8:
        _err("Passwort muss mindestens 8 Zeichen lang sein.")
        password = getpass.getpass("  Admin-Passwort: ")

    email = _ask("Admin-E-Mail (optional)", "")

    python = PANEL_DIR / ".venv" / "bin" / "python"
    if not python.exists():
        python = PANEL_DIR / ".venv" / "Scripts" / "python.exe"

    script = f"""
import os, sys
os.chdir(sys.argv[1])
from app.database import SessionLocal
from app.auth import hash_password
from app.models import User
import datetime

db = SessionLocal()
if db.query(User).filter_by(username="{username}").first():
    print("User already exists.")
else:
    user = User(
        username="{username}",
        email="{email or ""}",
        password_hash=hash_password("{password}"),
        role="owner",
        is_active=True,
    )
    db.add(user)
    db.commit()
    print("Admin created.")
db.close()
"""
    try:
        result = _run([str(python), "-c", script, str(PANEL_DIR)])
        if "already exists" in result.stdout:
            _warn("Admin-Konto existiert bereits.")
        else:
            _ok("Admin-Konto erstellt.")
    except subprocess.CalledProcessError as exc:
        _err(f"Admin-Erstellung fehlgeschlagen: {exc.stderr}")
        sys.exit(1)


# ── 10. Systemd Service ──────────────────────────────────────────────────────

def step_10_systemd() -> None:
    _step(10, 10, "Systemd-Service (optional)")
    if not _ask_yes_no("Systemd-Service erstellen? (Panel startet dann automatisch beim Boot)", False):
        _ok("Systemd-Service übersprungen.")
        return

    service_name = _ask("Service-Name", "maunting-panel")
    user = _ask("System-Benutzer", "root")

    service_content = f"""[Unit]
Description=Maunting Server Panel
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={PANEL_DIR}
EnvironmentFile={PANEL_DIR}/.env
ExecStart={PANEL_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8710
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    service_path = Path(f"/etc/systemd/system/{service_name}.service")
    try:
        with open(service_path, "w", encoding="utf-8") as f:
            f.write(service_content)
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", service_name])
        _ok(f"Systemd-Service '{service_name}' erstellt und aktiviert.")
        _print(f"  Starten:  sudo systemctl start {service_name}")
        _print(f"  Status:   sudo systemctl status {service_name}")
    except PermissionError:
        _err("Keine Berechtigung für /etc/systemd/system. Bitte mit sudo ausführen.")
    except subprocess.CalledProcessError as exc:
        _err(f"Systemd-Konfiguration fehlgeschlagen: {exc}")


# ── 11. Done ────────────────────────────────────────────────────────────────

def step_11_done() -> None:
    _print("\n" + "=" * 60, C_BOLD)
    _print("  Installation abgeschlossen!", f"{C_BOLD}{C_GREEN}")
    _print("=" * 60, C_BOLD)
    _print(f"\n  Panel-Verzeichnis:  {PANEL_DIR}")
    _print(f"  Config:             {PANEL_DIR}/.env")
    _print(f"  Datenbank:          Siehe .env (DATABASE_URL)")
    _print(f"\n  Starten (Dev):      cd {PANEL_DIR} && ./.venv/bin/uvicorn app.main:app --reload")
    _print(f"  Frontend-Dev:       cd {PANEL_DIR}/frontend && npm run dev")
    _print(f"\n  Öffne im Browser:   http://<deine-ip>:8710")
    _print(f"  Erster Login:       Admin-Konto aus Schritt 9")
    _print("")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if os.geteuid() != 0:
        _warn("Der Installer sollte idealerweise als root (oder mit sudo) ausgeführt werden,")
        _warn("damit MariaDB, Systemd und Pakete korrekt installiert werden können.")
        _print("")
        if not _ask_yes_no("Trotzdem fortfahren?", False):
            sys.exit(0)

    step_1_welcome()
    step_2_requirements()
    step_3_mariadb()
    step_4_phpmyadmin()
    step_5_python_deps()
    step_6_frontend_deps()
    step_7_env_config()
    step_8_database()
    step_9_admin()
    step_10_systemd()
    step_11_done()


if __name__ == "__main__":
    main()
