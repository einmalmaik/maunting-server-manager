import asyncio
import json
import logging
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from config import settings
from services import docker_service, file_service

logger = logging.getLogger("msm-agent.guardian")

# Keep track of local recovery attempts and failure counters in-memory (KISS)
_failure_counters: Dict[int, int] = {}
_recovery_stage: Dict[int, int] = {}  # Keep track of the current recovery ladder stage per server
_is_recovering: Dict[int, bool] = {}

_running = False
_loop_task: asyncio.Task | None = None


async def start_guardian_loop():
    global _running
    if _running:
        return
    _running = True
    logger.info("Guardian Autonomous Engine loop starting...")
    while _running:
        try:
            await reconcile_all_servers()
        except Exception as e:
            logger.exception("Error in Guardian reconciliation loop: %s", e)
        await asyncio.sleep(15)  # Reconcile loop runs every 15 seconds


async def stop_guardian_loop():
    global _running
    _running = False
    logger.info("Guardian Autonomous Engine loop stopped.")


def _read_desired_state(server_id: int) -> Dict[str, Any] | None:
    try:
        root = file_service.server_root(server_id)
        state_file = root / ".msm_desired_state.json"
        if state_file.is_file():
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Could not read desired state for server %s: %s", server_id, e)
    return None


def _write_desired_state(server_id: int, state: Dict[str, Any]) -> None:
    try:
        root = file_service.server_root(server_id)
        state_file = root / ".msm_desired_state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Could not write desired state for server %s: %s", server_id, e)


def _log_incident(server_id: int, incident: Dict[str, Any]) -> None:
    try:
        root = file_service.server_root(server_id)
        incidents_file = root / ".msm_incidents.json"
        incidents = []
        if incidents_file.is_file():
            try:
                with open(incidents_file, "r", encoding="utf-8") as f:
                    incidents = json.load(f)
            except Exception:
                incidents = []

        incidents.append(incident)
        # Limit to last 50 incidents to prevent disk bloat (KISS)
        incidents = incidents[-50:]

        with open(incidents_file, "w", encoding="utf-8") as f:
            json.dump(incidents, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Could not log incident for server %s: %s", server_id, e)


async def check_port_liveness(port: int, protocol: str, timeout: float = 3.0) -> bool:
    """Check if TCP/UDP socket bind responds."""
    if protocol.lower() == "udp":
        # UDP connect doesn't verify port listening on standard sockets.
        # Fallback to a best-effort DNS-like or query bind verify, or assume true if process runs.
        return True
    
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(
            loop.sock_connect(sock, ("127.0.0.1", port)),
            timeout=timeout
        )
        return True
    except Exception:
        return False
    finally:
        sock.close()


async def check_http_ping(port: int, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"http://127.0.0.1:{port}")
            return r.status_code < 500
    except Exception:
        return False


async def parse_diagnostics(container_name: str, log_sources: List[str], server_id: int) -> str:
    """Parse container logs or file log sources to identify matched failure type."""
    logs = ""
    try:
        logs = docker_service.container_logs(container_name, tail=100)
    except Exception:
        pass

    # Scan logs for known pattern signatures
    logs_lower = logs.lower()
    if "outofmemory" in logs_lower or "oom-killer" in logs_lower or "killed" in logs_lower:
        return "out_of_memory"
    if "address already in use" in logs_lower or "bindexception" in logs_lower or "could not bind" in logs_lower:
        return "port_conflict"
    if "unable to access jarfile" in logs_lower or "noclassdeffounderror" in logs_lower:
        return "missing_runtime"
    if "corrupt" in logs_lower or "unexpected token" in logs_lower:
        return "corrupted_config"
    return "unknown"


async def reconcile_all_servers():
    base = settings.servers_path()
    if not base.is_dir():
        return

    for path in base.iterdir():
        if not path.is_dir():
            continue
        folder_name = path.name
        if not folder_name.isdigit():
            continue

        server_id = int(folder_name)
        state = _read_desired_state(server_id)
        if not state:
            continue

        desired_status = state.get("status", "stopped")
        if desired_status != "running":
            # If not desired to run, clear counters and do nothing
            _failure_counters.pop(server_id, None)
            _recovery_stage.pop(server_id, None)
            _is_recovering.pop(server_id, None)
            continue

        if _is_recovering.get(server_id, False):
            # Already executing recovery, skip this tick
            continue

        container_name = f"{settings.container_name_prefix}{server_id}"
        
        # 1. Measure Current State
        try:
            inspect = docker_service.inspect_managed_state(container_name)
        except Exception as e:
            logger.warning("Could not inspect container state for %s: %s", container_name, e)
            inspect = None

        is_running = inspect and inspect.get("status") == "running"
        oom_killed = inspect and inspect.get("oom_killed", False)

        # 2. Check Health
        is_healthy = True
        failure_reason = "process_down"

        if not is_running:
            is_healthy = False
            if oom_killed:
                failure_reason = "out_of_memory"
        else:
            # Container runs. Check health definitions
            health_def = state.get("health", {})
            
            # Port checks
            port_def = health_def.get("port")
            if port_def and port_def.get("port"):
                port_str = str(port_def["port"])
                # Resolve placeholder {{SERVER_PORT}}
                # In real setup, placeholders are rendered on startup. We fallback to game port checks if not resolved.
                if "{{SERVER_PORT}}" not in port_str and port_str.isdigit():
                    port_val = int(port_str)
                    proto = port_def.get("protocol", "tcp")
                    timeout_val = 3.0
                    timeout_str = str(port_def.get("timeout", "3s")).rstrip("s")
                    if timeout_str.replace(".", "", 1).isdigit():
                        timeout_val = float(timeout_str)
                    
                    port_ok = await check_port_liveness(port_val, proto, timeout_val)
                    if not port_ok:
                        is_healthy = False
                        failure_reason = "port_conflict"

            # Application HTTP Ping
            app_def = health_def.get("application")
            if is_healthy and app_def and app_def.get("type") == "http-ping":
                # Find HTTP Port or default
                port_val = 80
                for p_key, p_val in state.get("ports", {}).items():
                    if p_key == "game" or p_key == "http":
                        port_val = int(p_val)
                app_ok = await check_http_ping(port_val)
                if not app_ok:
                    is_healthy = False
                    failure_reason = "application_query_failed"

        # 3. Reconciliation Action
        if is_healthy:
            # Reset counters if healthy
            _failure_counters[server_id] = 0
            _recovery_stage[server_id] = 0
        else:
            failures = _failure_counters.get(server_id, 0) + 1
            _failure_counters[server_id] = failures
            logger.warning("Server %s is unhealthy (Failures: %s, Reason: %s)", server_id, failures, failure_reason)

            # Retrieve failure threshold from config
            threshold = 3
            app_def = state.get("health", {}).get("application", {})
            if app_def and app_def.get("failure_threshold"):
                threshold = int(app_def["failure_threshold"])

            if failures >= threshold:
                # Trigger Escalation Recovery
                _is_recovering[server_id] = True
                try:
                    await execute_recovery(server_id, container_name, state, failure_reason)
                except Exception as rec_err:
                    logger.exception("Error executing recovery for server %s: %s", server_id, rec_err)
                finally:
                    _is_recovering[server_id] = False
                    _failure_counters[server_id] = 0  # Reset counter to wait for next checks


async def execute_recovery(server_id: int, container_name: str, state: Dict[str, Any], initial_reason: str):
    stage = _recovery_stage.get(server_id, 0) + 1
    _recovery_stage[server_id] = stage

    log_sources = state.get("logs", {}).get("sources", [])
    matched_reason = await parse_diagnostics(container_name, log_sources, server_id)
    if matched_reason == "unknown":
        matched_reason = initial_reason

    # Map matched_reason to recovery policy config if exists
    action = "restart"
    policies = state.get("recovery", {}).get("policies", [])
    for policy in policies:
        if policy.get("match") == matched_reason:
            action = policy.get("action", "restart")
            break

    logger.warning("Guardian initiating Recovery Stage %s (Reason: %s, Action: %s)", stage, matched_reason, action)

    incident = {
        "id": f"inc_{int(time.time())}_{server_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": matched_reason,
        "stage": stage,
        "recovery_action": action,
        "result": "pending"
    }

    if stage >= 5:
        # Stage 5+ requires Control Plane intervention / rollback snapshot / quarantine
        logger.error("Guardian escalating server %s to Quarantine (Stage limit reached)", server_id)
        state["status"] = "quarantined"
        _write_desired_state(server_id, state)
        incident["result"] = "quarantined"
        incident["message"] = "Max recovery stages exceeded. Server quarantined."
        _log_incident(server_id, incident)
        return

    # Execute recovery steps locally
    if action == "resolve_managed_port_conflict" or stage == 3:
        # Stage 3/Port conflict: Free bind port / stop conflicting container
        logger.info("Guardian attempting to free port/locks for server %s", server_id)
        try:
            # Stop the container, remove locks, and restart
            docker_service.stop_container(container_name, timeout=5)
            # Remove lock files
            root = file_service.server_root(server_id)
            for lk in root.glob("**/*.lock"):
                lk.unlink(missing_ok=True)
            for lk in root.glob("**/postmaster.pid"):
                lk.unlink(missing_ok=True)
            docker_service.start_container(container_name)
            incident["result"] = "success"
        except Exception as e:
            incident["result"] = "failed"
            incident["error"] = str(e)

    elif action == "controlled_memory_recovery":
        # Stage 2/Memory recovery: restart with clean heap / reset limits if allowed
        logger.info("Guardian performing memory recovery restart for server %s", server_id)
        try:
            docker_service.restart_container(container_name, timeout=10)
            incident["result"] = "success"
        except Exception as e:
            incident["result"] = "failed"
            incident["error"] = str(e)

    else:
        # Default action: simple restart
        logger.info("Guardian performing standard restart for server %s", server_id)
        try:
            docker_service.restart_container(container_name, timeout=10)
            incident["result"] = "success"
        except Exception as e:
            incident["result"] = "failed"
            incident["error"] = str(e)

    _log_incident(server_id, incident)
