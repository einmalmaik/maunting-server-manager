from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT


def test_panel_status_json_does_not_require_default_server_directory(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        ["bash", "conanserver.sh", "panel", "status", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "server directory does not exist" not in result.stderr
    assert '"installed":false' in result.stdout
    assert '"proxy_name":"caddy"' in result.stdout


def test_panel_caddy_render_writes_managed_reverse_proxy_block(tmp_path: Path):
    script = f"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
red=""
green=""
yellow=""
lightblue=""
default=""
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-caddy-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

PANEL_CADDYFILE="$tmpdir/Caddyfile"
PANEL_ENV_FILE="$tmpdir/.env"
PANEL_DIR="$PWD/panel"
PANEL_BIND_HOST="127.0.0.1"
PANEL_BIND_PORT="8710"
printf 'PANEL_BASE_PATH="/"\nPANEL_PUBLIC_DOMAIN="panel.example.invalid"\n' > "$PANEL_ENV_FILE"

fn_panel_render_caddy_file
grep -Fx '# BEGIN CONAN EXILES PANEL' "$PANEL_CADDYFILE"
grep -Fx 'panel.example.invalid {{' "$PANEL_CADDYFILE"
grep -Fx '        X-Content-Type-Options nosniff' "$PANEL_CADDYFILE"
grep -Fx '    reverse_proxy 127.0.0.1:8710 {{' "$PANEL_CADDYFILE"
grep -Fx '# END CONAN EXILES PANEL' "$PANEL_CADDYFILE"
"""

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"),
        check=False,
    )

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_panel_repair_env_completion_adds_production_app_env(tmp_path: Path):
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
red=""
green=""
yellow=""
lightblue=""
default=""
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-env-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

PANEL_ENV_FILE="$tmpdir/.env"
printf 'PANEL_RUNTIME_USER="%s"\nAPP_SECRET_KEY="test"\n' "$(id -un)" > "$PANEL_ENV_FILE"

fn_panel_ensure_env_complete
grep -Fx 'APP_ENV="production"' "$PANEL_ENV_FILE"
"""

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"),
        check=False,
    )

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout
