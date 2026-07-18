from __future__ import annotations

import os
import shutil
import tempfile
import tarfile
import subprocess
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from models import Node
from services.node_client import NodeClient
from services.panel_backup_service import create_panel_backup

logger = logging.getLogger(__name__)


def _get_repo_root() -> str:
    # backend/services/update_service.py -> msm root
    return str(Path(__file__).resolve().parent.parent.parent)


def get_update_status() -> dict:
    """Ermittelt den aktuellen Git-Update-Status des Panels."""
    repo_root = _get_repo_root()
    try:
        # 1. Fetch remote changes
        subprocess.run(["git", "fetch"], check=True, capture_output=True, text=True, cwd=repo_root, timeout=15)

        # 2. Get active branch name
        branch_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True, cwd=repo_root, timeout=10
        )
        branch = branch_res.stdout.strip()

        # 3. Get local SHA
        local_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, cwd=repo_root, timeout=10
        )
        local_sha = local_res.stdout.strip()

        # 4. Get remote SHA
        upstream_res = subprocess.run(
            ["git", "rev-parse", "@{u}"],
            capture_output=True, text=True, cwd=repo_root, timeout=10
        )
        if upstream_res.returncode == 0:
            remote_sha = upstream_res.stdout.strip()
        else:
            # Fallback to origin/<branch>
            remote_res = subprocess.run(
                ["git", "rev-parse", f"origin/{branch}"],
                capture_output=True, text=True, cwd=repo_root, timeout=10
            )
            remote_sha = remote_res.stdout.strip() if remote_res.returncode == 0 else ""

        if not remote_sha:
            return {"update_available": False, "error": "Could not determine remote commit hash", "ok": False}

        update_available = (local_sha != remote_sha)
        return {
            "update_available": update_available,
            "local_sha": local_sha[:8],
            "remote_sha": remote_sha[:8],
            "branch": branch,
            "ok": True
        }
    except Exception as e:
        logger.warning("Git-Update-Check fehlgeschlagen: %s", e)
        return {"update_available": False, "error": str(e), "ok": False}


def trigger_panel_update(db: Session) -> dict:
    """Erstellt ein automatisches Backup des Panels und startet den Update-Prozess."""
    # 1. Backup erstellen
    try:
        backup_record = create_panel_backup(db, name="Pre-Update Auto-Backup")
        logger.info("Pre-Update Backup erfolgreich erstellt: %s", os.path.basename(backup_record.local_path))
    except Exception as e:
        logger.error("Pre-Update Backup fehlgeschlagen, Update wird abgebrochen: %s", e)
        raise RuntimeError(f"Backup vor Update fehlgeschlagen: {str(e)}")

    # 2. Update-Script im Hintergrund starten
    # Der msm-User benötigt NOPASSWD-Rechte für /opt/msm/update.sh
    # Da das Update-Script den Panel-Service stoppt und neu startet, wird dieser Prozess beendet.
    update_script = os.path.join(_get_repo_root(), "update.sh")
    if not os.path.isfile(update_script):
        update_script = "/opt/msm/update.sh"

    logger.info("Starte Panel-Update mit %s...", update_script)
    try:
        subprocess.Popen(["sudo", "bash", update_script, "--force"])
    except Exception as e:
        logger.error("Fehler beim Starten des Update-Scripts: %s", e)
        raise RuntimeError(f"Update-Script konnte nicht gestartet werden: {str(e)}")

    return {"ok": True, "message": "Panel-Update und Backup initiiert"}


def generate_agent_package() -> bytes:
    """Generiert das aktuelle Agenten-Paket als tar.gz im Arbeitsspeicher."""
    repo_root = Path(_get_repo_root())
    agent_dir = repo_root / "msm-agent"
    installer = repo_root / "helper-scripts" / "install-msm-agent.sh"

    if not agent_dir.is_dir() or not installer.is_file():
        raise FileNotFoundError("Agent-Quellen nicht gefunden")

    fd, archive_path = tempfile.mkstemp(prefix="msm-agent-update-", suffix=".tar.gz")
    os.close(fd)

    excluded = {"venv", ".env", ".dev", "__pycache__", ".pytest_cache", "servers", "postgres", "certs", "tests"}

    def package_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if any(part in excluded for part in Path(info.name).parts):
            return None
        if info.name.endswith((".pyc", ".db", ".sqlite", ".sqlite3")):
            return None
        return info

    try:
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(agent_dir, arcname="msm-agent", filter=package_filter)
            archive.add(installer, arcname="helper-scripts/install-msm-agent.sh", filter=package_filter)

        with open(archive_path, "rb") as f:
            data = f.read()
        return data
    finally:
        Path(archive_path).unlink(missing_ok=True)


def trigger_node_updates(db: Session) -> dict:
    """Triggert das Update des Agenten auf allen remote Nodes."""
    nodes = db.query(Node).filter(Node.is_local == False).all()
    if not nodes:
        return {"ok": True, "message": "Keine remote Nodes zum Aktualisieren vorhanden", "results": []}

    try:
        archive_bytes = generate_agent_package()
    except Exception as e:
        logger.error("Konnte Agent-Paket fuer Node-Updates nicht generieren: %s", e)
        return {"ok": False, "error": f"Generierung fehlgeschlagen: {str(e)}"}

    results = []
    for node in nodes:
        try:
            logger.info("Sende Agent-Update an Node '%s' (%s)...", node.name, node.host)
            client = NodeClient.from_node(node)
            res = client.update_agent(archive_bytes)
            results.append({"node_id": node.id, "name": node.name, "ok": True, "message": res.get("message", "")})
        except Exception as e:
            logger.warning("Agent-Update an Node '%s' fehlgeschlagen: %s", node.name, e)
            results.append({"node_id": node.id, "name": node.name, "ok": False, "error": str(e)})

    success_count = sum(1 for r in results if r["ok"])
    return {
        "ok": success_count == len(results),
        "results": results,
        "message": f"{success_count}/{len(results)} Nodes erfolgreich aktualisiert"
    }
