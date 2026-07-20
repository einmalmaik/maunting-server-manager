import logging
from sqlalchemy.orm import Session
from models import Server
from services.server_lifecycle_service import queue_lifecycle_operation

logger = logging.getLogger(__name__)

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

        logger.info("Guardian auto-restart triggered for server %s", server_id)
        # Using "start" as the lifecycle operation to initiate the auto-restart, 
        # as defined by P3.1
        queue_lifecycle_operation(db, server, "start")
    except Exception as e:
        logger.error("Error in _trigger_guardian_auto_restart for server %s: %s", server_id, e)
