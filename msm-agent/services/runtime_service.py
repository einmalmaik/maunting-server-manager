"""Node-local port and firewall operations with a narrow command surface."""

from __future__ import annotations

import logging
import socket
import subprocess

logger = logging.getLogger(__name__)

_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
_FIREWALL_WRAPPER = "/usr/local/sbin/msm-agent-firewall"


def ports_available(ports: list[tuple[int, str]], bind_ip: str = "0.0.0.0") -> dict:
    conflicts: list[dict[str, object]] = []
    for port, protocol in ports:
        sock_type = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
        sock = socket.socket(socket.AF_INET, sock_type)
        try:
            sock.bind((bind_ip or "0.0.0.0", port))
        except OSError:
            conflicts.append({"port": port, "protocol": protocol})
        finally:
            sock.close()
    return {"available": not conflicts, "conflicts": conflicts}


def firewall(action: str, ports: list[tuple[int, str, str]], server_name: str = "server") -> dict:
    results: list[dict[str, object]] = []
    for port, protocol, role in ports:
        result = subprocess.run(
            ["sudo", "-n", _FIREWALL_WRAPPER, action, str(port), protocol, server_name, role],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=_ENV,
        )
        ok = result.returncode == 0
        if not ok:
            logger.warning(
                "Firewall-Wrapper fuer Port %s (%s) fehlgeschlagen (Exit-Code %s): stdout=%s, stderr=%s",
                port,
                protocol,
                result.returncode,
                result.stdout.strip(),
                result.stderr.strip(),
            )
        results.append({"port": port, "protocol": protocol, "ok": ok})
    return {"ok": all(bool(item["ok"]) for item in results), "results": results}
