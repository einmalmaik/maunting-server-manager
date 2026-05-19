from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import HTTPException

from app.api import autorestart as autorestart_api
from app.api import mods
from app.models import User
from app import shell
from app.shell import CommandResult, PanelCommandError


REPO_ROOT = Path(__file__).resolve().parents[2]


def _owner_user() -> User:
    return User(id=1, username="owner", password_hash="x", role="owner", is_active=True)


def _run_bash(script: str) -> subprocess.CompletedProcess[bytes]:
    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )


def test_workshop_autoupdate_minutes_writes_minute_cron_entry():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
source "./lib/workshop.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-workshop-autoupdate-minutes-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
export LOCK_HELD="0"
export LOCK_DIR="${tmpdir}/dayz.lock"
export CURRENT_COMMAND="workshop autoupdate"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

crontab_store="${tmpdir}/crontab.txt"
crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store" 2>/dev/null || true
        return 0
    fi
    cp "$1" "$crontab_store"
}
systemctl() {
    [ "${1:-}" = "is-active" ] && [ "${2:-}" = "--quiet" ] && [ "${3:-}" = "cron" ]
}

fn_workshop_autoupdate_set_minutes 10 >/dev/null

grep -F '*/10 * * * * bash ' "$crontab_store"
grep -F -- '--server alpha workshop' "$crontab_store"
grep -F "$WORKSHOP_AUTOUPDATE_LOG" "$crontab_store"
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_workshop_autoupdate_hours_keep_hourly_cron_entry():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
source "./lib/workshop.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-workshop-autoupdate-hours-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
export LOCK_HELD="0"
export LOCK_DIR="${tmpdir}/dayz.lock"
export CURRENT_COMMAND="workshop autoupdate"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

crontab_store="${tmpdir}/crontab.txt"
crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store" 2>/dev/null || true
        return 0
    fi
    cp "$1" "$crontab_store"
}
systemctl() {
    [ "${1:-}" = "is-active" ] && [ "${2:-}" = "--quiet" ] && [ "${3:-}" = "cron" ]
}

fn_workshop_autoupdate_set_interval 6 >/dev/null

grep -F '0 */6 * * * bash ' "$crontab_store"
grep -F -- '--server alpha workshop' "$crontab_store"
grep -F "$WORKSHOP_AUTOUPDATE_LOG" "$crontab_store"
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_autorestart_cron_entries_write_to_server_log():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-autorestart-cron-log-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

crontab_store="${tmpdir}/crontab.txt"
crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store" 2>/dev/null || true
        return 0
    fi
    cp "$1" "$crontab_store"
}

fn_apply_autorestart_crontab "00:00" "12:00" >/dev/null

grep -F 'bash ' "$crontab_store"
grep -F -- '--server alpha restart' "$crontab_store"
grep -F "$AUTORESTART_CRON_LOG" "$crontab_store"
grep -F '# BEGIN CONANSERVER AUTORESTART alpha' "$crontab_store"
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_autorestart_rewrite_removes_legacy_managed_block():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-autorestart-legacy-cleanup-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

crontab_store="${tmpdir}/crontab.txt"
cat > "$crontab_store" <<CRON
# BEGIN CONANSERVER AUTORESTART
00 00 * * * /legacy/conanserver.sh restart >/dev/null 2>&1 # conanserver-autorestart
# END CONANSERVER AUTORESTART
CRON

crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store" 2>/dev/null || true
        return 0
    fi
    cp "$1" "$crontab_store"
}

fn_apply_autorestart_crontab "06:00" >/dev/null

if grep -F '# BEGIN CONANSERVER AUTORESTART' "$crontab_store" | grep -Fv '# BEGIN CONANSERVER AUTORESTART alpha'; then
    exit 1
fi
grep -F '# BEGIN CONANSERVER AUTORESTART alpha' "$crontab_store"
grep -F 'bash ' "$crontab_store"
grep -F -- '--server alpha restart' "$crontab_store"
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_workshop_autoupdate_rewrite_removes_legacy_managed_block():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
source "./lib/workshop.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-workshop-legacy-cleanup-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
export LOCK_HELD="0"
export LOCK_DIR="${tmpdir}/dayz.lock"
export CURRENT_COMMAND="workshop autoupdate"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

crontab_store="${tmpdir}/crontab.txt"
cat > "$crontab_store" <<CRON
# BEGIN CONANSERVER WORKSHOP AUTOUPDATE
*/10 * * * * /legacy/conanserver.sh workshop >/dev/null 2>&1 # conanserver-workshop-autoupdate
# END CONANSERVER WORKSHOP AUTOUPDATE
CRON

crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store" 2>/dev/null || true
        return 0
    fi
    cp "$1" "$crontab_store"
}
systemctl() {
    [ "${1:-}" = "is-active" ] && [ "${2:-}" = "--quiet" ] && [ "${3:-}" = "cron" ]
}

fn_workshop_autoupdate_set_minutes 10 >/dev/null

if grep -F '# BEGIN CONANSERVER WORKSHOP AUTOUPDATE' "$crontab_store" | grep -Fv '# BEGIN CONANSERVER WORKSHOP AUTOUPDATE alpha'; then
    exit 1
fi
grep -F '# BEGIN CONANSERVER WORKSHOP AUTOUPDATE alpha' "$crontab_store"
grep -F 'bash ' "$crontab_store"
grep -F -- '--server alpha workshop' "$crontab_store"
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_panel_bridge_workshop_reports_structured_scheduler_state():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
source "./lib/panel.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-panel-bridge-workshop-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
mkdir -p "$SERVER_DIR"
fn_init_server_paths
printf '123 foo\n' > "$WORKSHOP_CFG"

crontab_store="${tmpdir}/crontab.txt"
cat > "$crontab_store" <<CRON
# BEGIN CONANSERVER WORKSHOP AUTOUPDATE alpha
*/10 * * * * /tmp/conanserver.sh --server alpha workshop >> ${WORKSHOP_AUTOUPDATE_LOG} 2>&1 # conanserver-workshop-autoupdate
# END CONANSERVER WORKSHOP AUTOUPDATE alpha
CRON

crontab() {
    if [ "${1:-}" = "-l" ]; then
        cat "$crontab_store"
        return 0
    fi
    cp "$1" "$crontab_store"
}
systemctl() {
    [ "${1:-}" = "is-active" ] && [ "${2:-}" = "--quiet" ] && [ "${3:-}" = "cron" ]
}

result="$(fn_panel_bridge_workshop)"
printf '%s' "$result" | grep '"autoupdate_enabled":true'
printf '%s' "$result" | grep '"autoupdate_interval_minutes":10'
printf '%s' "$result" | grep '"scheduler_ready":true'
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_panel_bridge_autorestart_keeps_configured_times_when_crontab_is_missing():
    script = r"""
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
export ansi="off"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/autorestart.sh"
source "./lib/panel.sh"
fn_init_colors

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pytest-panel-bridge-autorestart-XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

export SERVER_NAME="alpha"
export SERVER_DIR="${tmpdir}/servers/alpha"
mkdir -p "$SERVER_DIR"
fn_init_server_paths

autorestart_mode="times"
autorestart_times="00:00 12:00"
autorestart_interval_hours=""

crontab() {
    if [ "${1:-}" = "-l" ]; then
        return 0
    fi
    return 1
}
systemctl() {
    [ "${1:-}" = "is-active" ] && [ "${2:-}" = "--quiet" ] && [ "${3:-}" = "cron" ]
}

result="$(fn_panel_bridge_autorestart)"
printf '%s' "$result" | grep '"times":\["00:00","12:00"\]'
printf '%s' "$result" | grep '"effective_times":\[\]'
"""

    result = _run_bash(script)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_fetch_action_log_strips_ansi_sequences(monkeypatch, tmp_path):
    server_dir = tmp_path / "servers" / "alpha"
    server_dir.mkdir(parents=True)
    (server_dir / "panel_action.log").write_text("\x1b[32m[ Success ]\x1b[0m cron ready\r\nplain line\r\n", encoding="utf-8")

    monkeypatch.setattr(shell, "get_server_dir", lambda server_name=None: server_dir)

    assert shell.fetch_action_log("alpha") == ["[ Success ] cron ready", "plain line"]


def test_get_mod_autoupdate_returns_structured_workshop_state(monkeypatch):
    monkeypatch.setattr(
        mods,
        "fetch_workshop_status",
        lambda server_name=None: {
            "autoupdate_enabled": True,
            "autoupdate_interval_minutes": 10,
            "autoupdate_display": "Interval: every 10 minutes",
            "scheduler_ready": False,
            "scheduler_error": "cron is not active",
            "cron_active": False,
            "cron_installed": True,
            "cron_service_name": "cron",
            "autoupdate_log_path": "/tmp/workshop_autoupdate.log",
        },
    )

    result = mods.get_mod_autoupdate(user=_owner_user(), server="alpha")

    assert result == {
        "enabled": True,
        "interval_minutes": 10,
        "display": "Interval: every 10 minutes",
        "scheduler_ready": False,
        "scheduler_error": "cron is not active",
        "cron_active": False,
        "cron_installed": True,
        "cron_service_name": "cron",
        "log_path": "/tmp/workshop_autoupdate.log",
    }


def test_autorestart_update_keeps_success_audit_when_status_refresh_fails(monkeypatch):
    recorded_entries: list[object] = []

    class DummySession:
        def add(self, entry):
            recorded_entries.append(entry)

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(autorestart_api, "invoke_core_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autorestart_api,
        "fetch_autorestart_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PanelCommandError(
                CommandResult(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="cron status refresh failed",
                )
            )
        ),
    )

    body = autorestart_api.AutorestartUpdate(mode="off")

    try:
        autorestart_api.update_autorestart(
            body=body,
            db=DummySession(),
            user=_owner_user(),
            server="alpha",
        )
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "cron status refresh failed"
    else:
        raise AssertionError("Expected HTTPException")

    assert len(recorded_entries) == 1
    assert recorded_entries[0].status == "success"
    assert recorded_entries[0].detail == "Applied, but status refresh failed."
