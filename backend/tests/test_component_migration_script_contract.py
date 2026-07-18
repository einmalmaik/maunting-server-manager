from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "helper-scripts" / "migrate-panel-components.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_has_strict_secret_safe_failure_contract() -> None:
    script = _script()

    assert "set -Eeuo pipefail" in script
    assert "umask 077" in script
    assert "trap restart_source_on_error ERR" in script
    assert "systemctl start msm-panel.service" in script
    assert "SOURCE_STOPPED=true" in script
    assert "TARGET_COMMITTED=true" in script
    assert script.index("ssh_run \"$remote_install\"") < script.index("systemctl stop msm-panel.service")
    assert "prepare_component_migration.py" in script
    assert "--source-env" in script and "--target-env" in script
    assert "MSM_SECRET_KEY=" not in script
    assert "MSM_DIS_SIDECAR_TOKEN=" not in script
    assert "PGPASSWORD=" not in script
    assert "--exclude='.env'" in script


def test_script_keeps_node_security_boundary_and_source_data() -> None:
    script = _script()
    node_installer = (ROOT / "helper-scripts" / "install-msm-node.sh").read_text(encoding="utf-8")

    assert "helper-scripts/install-msm-node.sh" in script
    assert "Owner-Freigabe" in script
    assert "MSM_ENROLLED_NODE_ID=" in script
    assert "Im Panel angezeigte/neu angelegte Ersatz-Node-ID" not in script
    assert "NODE_ID=$(jq -r '.node_id // empty'" in node_installer
    assert 'echo "MSM_ENROLLED_NODE_ID=${NODE_ID}"' in node_installer
    assert "scripts/handoff_local_node.py" in script
    assert "scripts/migrate_server_to_node.py" in script
    assert "Quelldaten werden nicht gelöscht" in script
    assert "--server-id \"$server_id\"" in script
    assert "--target-node-id \"$TARGET_NODE_ID\"" in script


def test_operator_docs_and_environment_contract_stay_synchronized() -> None:
    command = "sudo /opt/msm/helper-scripts/migrate-panel-components.sh"
    self_hosting = (ROOT / "docs" / "self-hosting.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    visible_docs = (ROOT / "frontend" / "src" / "pages" / "docs" / "SelfHostingDocs.tsx").read_text(
        encoding="utf-8"
    )
    backend_env = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    frontend_env = (ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")

    assert command in self_hosting
    assert command in readme
    assert command in visible_docs
    assert "helper-scripts/migrate-panel-components.sh" in agents
    assert "MSM_API_URL=" in backend_env
    assert "MSM_LOCAL_AGENT_ENABLED=" in backend_env
    assert "helper-scripts/migrate-panel-components.sh" in frontend_env


@pytest.mark.skipif(os.name == "nt", reason="Native Bash execution is verified in Linux CI")
def test_script_syntax_and_dry_run_do_not_require_a_production_install() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")

    syntax = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    dry_run = subprocess.run(
        [
            bash,
            str(SCRIPT),
            "--migrate-frontend",
            "--frontend-origin",
            "https://panel.example.com",
            "--api-domain",
            "api.example.com",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Dry-run abgeschlossen" in dry_run.stdout
    assert "es wurde nichts verändert" in dry_run.stdout
