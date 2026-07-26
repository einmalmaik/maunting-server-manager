import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import Server
from services.server_lifecycle_service import queue_lifecycle_operation

logger = logging.getLogger(__name__)

# Minimum seconds between auto-restart attempts for the same server.
_AUTO_RESTART_COOLDOWN_SECONDS = 300


def _trigger_guardian_auto_restart(db: Session, server_id: int) -> None:
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return

        if server.guardian_observed_state == "unknown":
            return

        if server.guardian_observed_state not in ("failed", "stopped"):
            return

        # Ensure generations match (agent has fully processed the latest intent)
        if server.desired_state_generation != server.guardian_accepted_generation:
            return

        if server.desired_power_state != "running":
            return

        # Optional: check if auto_restart is globally enabled for the server
        if not getattr(server, "auto_restart", False):
            return

        # Cooldown: prevent restart loops by checking the last transition timestamp
        if server.guardian_transition_timestamp is not None:
            elapsed = (datetime.now(timezone.utc) - server.guardian_transition_timestamp).total_seconds()
            if elapsed < _AUTO_RESTART_COOLDOWN_SECONDS:
                logger.debug(
                    "Guardian auto-restart skipped for server %s (cooldown: %.0fs remaining)",
                    server_id,
                    _AUTO_RESTART_COOLDOWN_SECONDS - elapsed,
                )
                return

        logger.info("Guardian auto-restart triggered for server %s", server_id)
        queue_lifecycle_operation(db, server, "start")
    except Exception as e:
        logger.error("Error in _trigger_guardian_auto_restart for server %s: %s", server_id, e)
