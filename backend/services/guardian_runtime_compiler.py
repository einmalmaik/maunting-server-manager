"""Compile Blueprint Guardian intent into the concrete Agent runtime contract."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any

from blueprints.schema import Blueprint
from models import Server


GUARDIAN_SCHEMA_VERSION = 1
SUPPORTED_PROBES = frozenset(
    {
        "process",
        "tcp",
        "udp_port_mapping",
        "http-ping",
        "minecraft-status",
        "minecraft-query",
        "source-query",
    }
)
SUPPORTED_DIAGNOSTICS = frozenset(
    {
        "linux-oom",
        "java-stacktrace",
        "nodejs-stacktrace",
        "port-conflict",
        "missing-runtime",
        "corrupted-config",
        "startup-pattern",
    }
)
SUPPORTED_ACTIONS = frozenset(
    {"restart", "graceful_restart", "clear_declared_lock_files", "quarantine"}
)
_TOKEN_RE = re.compile(r"{{(SERVER_PORT|PORT:([a-zA-Z0-9_.-]{1,64}))}}")
_ANY_TOKEN_RE = re.compile(r"{{[^{}]+}}")

#: Was sich je Server anders einstellen laesst — und in welchen Grenzen.
#:
#: Die Blueprint gilt fuer jeden Server ihres Spiels und kann nicht wissen, dass
#: ausgerechnet auf dieser Node zwoelf Instanzen um acht Gigabyte streiten.
#: Genau diese Luecke fuellt die Uebersteuerung.
#:
#: **Ausschliesslich Skalare, und ausschliesslich diese.** Keine Listen, keine
#: Regexe, keine Probentypen — dieselbe Begruendung, aus der `AENDERBARE_PFADE`
#: in `blueprint_service` listenwertige Pfade ausschliesst: was sich in einer
#: Zahl mit Ober- und Untergrenze ausdruecken laesst, kann keine Struktur
#: zerstoeren. Eine uebersteuerte Probenliste dagegen koennte Guardian fuer
#: diesen Server blind machen, ohne dass es irgendwo als "abgeschaltet" stuende.
#:
#: Die Grenzen sind Deckel, nicht Vorschlaege. Geklemmt wird **hier**, obwohl
#: das Werkzeug dieselben Grenzen schon prueft und einen Verstoss abweist: die
#: Pruefung dort ist eine Rueckmeldung an das Modell, das Klemmen hier ist die
#: Schranke gegen alles, was nicht durch das Werkzeug kam — eine von Hand
#: bearbeitete Zeile etwa. Ein Startfenster von zehn Tagen waere ein Guardian,
#: der nie wieder etwas meldet.
GUARDIAN_STELLSCHRAUBEN: dict[str, tuple[int, int]] = {
    # Wie lange ein Server nach dem Start Ruhe hat, bevor Proben zaehlen.
    "startup_grace_period_seconds": (1, 3_600),
    # Und wann ein Start endgueltig als gescheitert gilt.
    "startup_timeout_seconds": (10, 7_200),
    # Der Abstand zwischen zwei Proben und ihre Geduld.
    "probe_interval_seconds": (1, 600),
    "probe_timeout_seconds": (1, 120),
    # Wieviele Fehlschlaege beziehungsweise Erfolge zaehlen.
    "probe_failure_threshold": (1, 20),
    "probe_success_threshold": (1, 20),
    # Guardians eigene Leiter. ``0`` ist erlaubt und heisst: gar kein
    # Selbstheilungsversuch mehr — der Fall "haende weg, ich habe es von Hand
    # gerichtet, melde nur noch".
    "recovery_max_attempts": (0, 20),
    "recovery_attempt_window_seconds": (60, 86_400),
    "recovery_cooldown_seconds": (0, 86_400),
    # Wie lange ein Server gesund sein muss, damit er als geheilt gilt.
    "verification_min_healthy_seconds": (0, 3_600),
    "verification_required_successes": (1, 20),
    "verification_timeout_seconds": (10, 7_200),
}


class GuardianCompileError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class GuardianRequirements:
    schema_version: int
    probe_types: frozenset[str]
    diagnostic_parsers: frozenset[str]
    recovery_actions: frozenset[str]


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"payload_hash", "sent_at"}
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _duration_seconds(value: str, *, minimum: float = 0.1, maximum: float = 3600) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", str(value).strip())
    if not match:
        raise GuardianCompileError("invalid_duration", f"Invalid Guardian duration: {value}")
    amount = float(match.group(1))
    unit = match.group(2)
    seconds = amount / 1000 if unit == "ms" else amount * 60 if unit == "m" else amount
    if seconds < minimum or seconds > maximum:
        raise GuardianCompileError("invalid_duration", f"Guardian duration outside safe bounds: {value}")
    return seconds


def _port_map(server: Server) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in getattr(server, "ports", []) or []:
        role = str(getattr(item, "role", "") or "").strip()
        port = getattr(item, "port", None)
        if role and isinstance(port, int) and 1 <= port <= 65535:
            result[role] = port
    # Fallback to server properties if missing from ports relation
    if "game" not in result:
        g = getattr(server, "game_port", None)
        if isinstance(g, int) and 1 <= g <= 65535:
            result["game"] = g
    if "query" not in result:
        q = getattr(server, "query_port", None)
        if isinstance(q, int) and 1 <= q <= 65535:
            result["query"] = q
    if "rcon" not in result:
        r = getattr(server, "rcon_port", None)
        if isinstance(r, int) and 1 <= r <= 65535:
            result["rcon"] = r
    return result


def _resolve_tokens(value: str, ports: dict[str, int]) -> str:
    # 1. Find all placeholders matching {{...}}
    placeholders = re.findall(r"{{([^{}]+)}}", value)
    for raw_placeholder in placeholders:
        placeholder = raw_placeholder.strip()
        role = None
        
        # Check standard whitelisted direct placeholders
        if placeholder in ("SERVER_PORT", "GAME_PORT"):
            role = "game"
        elif placeholder == "QUERY_PORT":
            role = "query"
        elif placeholder == "RCON_PORT":
            role = "rcon"
        elif placeholder == "VOICE_PORT":
            role = "voice"
        elif placeholder == "WEB_PORT":
            role = "web"
        elif placeholder.startswith("CUSTOM_PORT_"):
            num = placeholder[12:]
            if num.isdigit():
                role = f"custom_port_{num}"
                if role not in ports and f"custom_{num}" in ports:
                    role = f"custom_{num}"
            else:
                raise GuardianCompileError(
                    "unresolved_placeholder",
                    f"Guardian placeholder CUSTOM_PORT_ suffix must be numeric: {placeholder}",
                )
        # Check legacy PORT:role syntax if role is in whitelist
        elif placeholder.startswith("PORT:"):
            inner = placeholder[5:]
            if inner in ("game", "query", "rcon", "voice", "web"):
                role = inner
            elif inner.startswith("custom_port_") and inner[12:].isdigit():
                role = inner
            elif inner.startswith("custom_") and inner[7:].isdigit():
                role = inner
            else:
                raise GuardianCompileError(
                    "unresolved_placeholder",
                    f"Guardian placeholder role is not in the whitelist: {inner}",
                )
        else:
            raise GuardianCompileError(
                "unresolved_placeholder",
                f"Guardian placeholder is not whitelisted: {placeholder}",
            )
            
        port = ports.get(role)
        if port is None:
            raise GuardianCompileError(
                "unresolved_placeholder",
                f"Guardian port role is not allocated: {role}",
                details={"role": role},
            )
        
        value = value.replace(f"{{{{{raw_placeholder}}}}}", str(port))
        
    if "{{" in value or "}}" in value:
        raise GuardianCompileError(
            "unresolved_placeholder",
            "Guardian configuration contains an unsupported or stray placeholder",
        )
    return value


def _resolved_port(value: str, ports: dict[str, int]) -> int:
    resolved = _resolve_tokens(value, ports)
    if not resolved.isdigit() or not 1 <= int(resolved) <= 65535:
        raise GuardianCompileError("invalid_probe_port", "Guardian probe port is invalid")
    return int(resolved)


def _target_host(server: Server) -> str:
    value = str(getattr(server, "public_bind_ip", "") or "").strip()
    if not value:
        raise GuardianCompileError(
            "probe_target_unavailable",
            "A concrete public_bind_ip is required for host-bound Guardian probes",
        )
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise GuardianCompileError("invalid_probe_target", "Guardian probe target is invalid") from exc
    if address.is_unspecified:
        return "127.0.0.1" if address.version == 4 else "::1"
    if address.is_link_local or address.is_multicast or address.is_reserved:
        raise GuardianCompileError("invalid_probe_target", "Guardian probe target is unsafe")
    return str(address)


def _application_port(application: Any, ports: dict[str, int]) -> int:
    if application.port:
        return _resolved_port(application.port, ports)
        
    if application.type == "minecraft-status":
        port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "minecraft-status requires game port")
        return port
    elif application.type == "minecraft-query":
        port = ports.get("query")
        if port is None:
            port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "minecraft-query requires query or game port")
        return port
    elif application.type == "source-query":
        port = ports.get("query")
        if port is None:
            port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "source-query requires query or game port")
        return port
    elif application.type == "http-ping":
        port = ports.get("web")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "http-ping requires web port or an explicit port")
        return port
    else:
        default_roles = {
            "tcp": "game",
            "udp_port_mapping": "game",
        }
        role = default_roles.get(application.type, "game")
        port = ports.get(role)
        if port is None:
            raise GuardianCompileError(
                "probe_port_unavailable",
                f"Guardian probe {application.id} requires an explicit port or allocated role {role}",
                details={"check_id": application.id, "role": role},
            )
        return port


def gelesene_uebersteuerung(server: Server) -> dict[str, int]:
    """Die Uebersteuerung dieses Servers — gesaeubert und geklemmt.

    Alles, was nicht in `GUARDIAN_STELLSCHRAUBEN` steht, faellt weg; alles, was
    darin steht, wird auf seinen Bereich geklemmt. Unlesbares JSON gilt als
    "keine Uebersteuerung" und wirft **nicht**: diese Funktion laeuft in jedem
    Reconciliation-Takt ueber jeden Server, und eine kaputte Zeile darf nicht
    die Guardian-Synchronisation der ganzen Node anhalten.
    """
    roh = server.guardian_overrides_json
    if not roh:
        return {}
    try:
        geladen = json.loads(roh)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(geladen, dict):
        return {}
    sauber: dict[str, int] = {}
    for name, (unten, oben) in GUARDIAN_STELLSCHRAUBEN.items():
        wert = geladen.get(name)
        if isinstance(wert, bool) or not isinstance(wert, (int, float)):
            # `bool` ist in Python ein `int`, und `True` als Startfenster waere
            # eine Sekunde — ein Wert, den niemand gemeint hat.
            continue
        sauber[name] = max(unten, min(oben, int(wert)))
    return sauber


def _uebersteuern(config: dict[str, Any], werte: dict[str, int]) -> dict[str, Any]:
    """Legt die Uebersteuerung ueber die aus der Blueprint abgeleitete Konfiguration.

    **Nach** der Ableitung und nicht statt ihrer: was hier nicht genannt ist,
    bleibt genau so, wie die Blueprint es sagt. Eine Uebersteuerung ist ein
    Nachtrag, keine zweite Konfiguration — sonst muesste sie vollstaendig sein,
    und dann waere sie eine Blueprint.

    Die Probenwerte gelten fuer **alle** Proben dieses Servers. Einzelne Proben
    anzusprechen hiesse, ihre Kennungen zu kennen, und die stehen in einer Liste
    in der Blueprint; eine Uebersteuerung, die sich auf Listeneintraege bezieht,
    zeigt nach der naechsten Blueprint-Aenderung ins Leere.
    """
    if not werte:
        return config

    startup = config["startup"]
    if "startup_grace_period_seconds" in werte:
        startup["grace_period_seconds"] = werte["startup_grace_period_seconds"]
    if "startup_timeout_seconds" in werte:
        startup["timeout_seconds"] = werte["startup_timeout_seconds"]

    for probe in config["health_checks"]:
        if "probe_interval_seconds" in werte:
            probe["interval_seconds"] = werte["probe_interval_seconds"]
        if "probe_failure_threshold" in werte:
            probe["failure_threshold"] = werte["probe_failure_threshold"]
        if "probe_success_threshold" in werte:
            probe["success_threshold"] = werte["probe_success_threshold"]
        # Die Prozessprobe hat keine Gegenstelle, auf die man warten koennte —
        # sie sieht nach, ob ein Prozess laeuft. Ihr eine Netzgeduld zu geben
        # waere eine Zahl ohne Bedeutung, und der Agent prueft seine Felder.
        if "probe_timeout_seconds" in werte and probe.get("type") != "process":
            probe["timeout_seconds"] = werte["probe_timeout_seconds"]

    verification = config["verification"]
    if "verification_min_healthy_seconds" in werte:
        verification["minimum_healthy_duration_seconds"] = werte[
            "verification_min_healthy_seconds"
        ]
    if "verification_required_successes" in werte:
        verification["required_consecutive_successes"] = werte[
            "verification_required_successes"
        ]
    if "verification_timeout_seconds" in werte:
        verification["verification_timeout_seconds"] = werte["verification_timeout_seconds"]

    recovery = config["recovery"]
    if "recovery_max_attempts" in werte:
        recovery["max_attempts"] = werte["recovery_max_attempts"]
    if "recovery_attempt_window_seconds" in werte:
        recovery["attempt_window_seconds"] = werte["recovery_attempt_window_seconds"]
    if "recovery_cooldown_seconds" in werte:
        recovery["cooldown_seconds"] = werte["recovery_cooldown_seconds"]
    return config


def compile_guardian_config(server: Server, blueprint: Blueprint) -> dict[str, Any]:
    ports = _port_map(server)
    health = blueprint.health
    checks: list[dict[str, Any]] = []
    if health and health.process:
        process = health.process
        if process.required:
            checks.append(
                {
                    "check_id": process.id,
                    "type": "process",
                    "interval_seconds": _duration_seconds(process.interval, minimum=1),
                    "timeout_seconds": 1,
                    "failure_threshold": process.failure_threshold,
                    "success_threshold": process.success_threshold,
                    "required_for_startup": process.required_for_startup,
                    "required_for_verification": process.required_for_verification,
                }
            )
    if health and health.port:
        port_check = health.port
        probe_type = "tcp" if port_check.protocol == "tcp" else "udp_port_mapping"
        target_port = _resolved_port(port_check.port, ports)
        compiled: dict[str, Any] = {
            "check_id": port_check.id,
            "type": probe_type,
            "interval_seconds": _duration_seconds(port_check.interval, minimum=1),
            "timeout_seconds": _duration_seconds(port_check.timeout),
            "failure_threshold": port_check.failure_threshold,
            "success_threshold": port_check.success_threshold,
            "required_for_startup": port_check.required_for_startup,
            "required_for_verification": port_check.required_for_verification,
            "target_port": target_port,
        }
        if probe_type == "tcp":
            compiled["target_host"] = _target_host(server)
        checks.append(compiled)
    if health and health.application:
        application = health.application
        compiled = {
            "check_id": application.id,
            "type": application.type,
            "interval_seconds": _duration_seconds(application.interval, minimum=1),
            "timeout_seconds": _duration_seconds(application.timeout),
            "failure_threshold": application.failure_threshold,
            "success_threshold": application.success_threshold,
            "required_for_startup": application.required_for_startup,
            "required_for_verification": application.required_for_verification,
            "target_host": _target_host(server),
            "target_port": _application_port(application, ports),
        }
        if application.type == "http-ping":
            compiled.update(
                {
                    "path": application.path,
                    "expected_statuses": application.expected_statuses,
                    "follow_redirects": application.follow_redirects,
                    "max_response_bytes": application.max_response_bytes,
                }
            )
        checks.append(compiled)

    # A Guardian configuration with no health signal cannot verify recovery.
    if not checks:
        checks.append(
            {
                "check_id": "process",
                "type": "process",
                "interval_seconds": 15,
                "timeout_seconds": 1,
                "failure_threshold": 1,
                "success_threshold": 1,
                "required_for_startup": True,
                "required_for_verification": True,
            }
        )

    startup = health.startup if health and health.startup else None
    recovery = blueprint.recovery
    logs = blueprint.logs
    diagnostics = blueprint.diagnostics
    backups = blueprint.backups
    verification = recovery.verification if recovery else None
    return _uebersteuern(_aus_blueprint(
        checks, startup, verification, logs, diagnostics, recovery, backups
    ), gelesene_uebersteuerung(server))


def _aus_blueprint(
    checks, startup, verification, logs, diagnostics, recovery, backups
) -> dict[str, Any]:
    """Die Konfiguration, wie die Blueprint sie meint — ohne Uebersteuerung.

    Eigene Funktion, damit die Uebersteuerung eine Zeile weiter oben als
    **Nachtrag** sichtbar ist und nicht als Sonderfall irgendwo mitten in einem
    hundert Zeilen langen Woerterbuchliteral.
    """
    return {
        "health_checks": checks,
        "startup": {
            "grace_period_seconds": startup.grace_period_seconds if startup else 30,
            "timeout_seconds": startup.timeout_seconds if startup else 300,
            "success_patterns": startup.success_patterns if startup else [],
            "failure_patterns": startup.failure_patterns if startup else [],
        },
        "verification": {
            "minimum_healthy_duration_seconds": (
                verification.minimum_healthy_duration_seconds if verification else 30
            ),
            "required_consecutive_successes": (
                verification.required_consecutive_successes if verification else 3
            ),
            "verification_timeout_seconds": (
                verification.verification_timeout_seconds if verification else 180
            ),
        },
        "logs": {
            "sources": logs.sources if logs else [],
            "redact": logs.redact if logs else [],
            "max_tail_bytes": logs.max_tail_bytes if logs else 65_536,
        },
        "diagnostics": {"parsers": diagnostics.parsers if diagnostics else []},
        "recovery": {
            "policies": [policy.model_dump() for policy in recovery.policies] if recovery else [],
            "safe_lock_files": [entry.model_dump() for entry in recovery.safe_lock_files] if recovery else [],
            "max_attempts": recovery.max_attempts if recovery else 3,
            "attempt_window_seconds": recovery.attempt_window_seconds if recovery else 1800,
            "cooldown_seconds": recovery.cooldown_seconds if recovery else 300,
        },
        "backups": {
            "before_risky_action": backups.before_risky_action if backups else True,
            "protected_paths": backups.protected_paths if backups else [],
        },
    }


def is_guardian_enabled(blueprint: Blueprint) -> bool:
    """Guardian is opt-in through at least one explicit Guardian section."""
    return any(
        getattr(blueprint, field) is not None
        for field in ("health", "logs", "diagnostics", "recovery", "backups")
    )


def guardian_config_hash(server: Server, blueprint: Blueprint) -> str:
    encoded = json.dumps(
        {
            "enabled": is_guardian_enabled(blueprint),
            "config": compile_guardian_config(server, blueprint),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _load_optional_json(raw: str | None, field: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GuardianCompileError("invalid_persisted_state", f"Invalid persisted {field}") from exc
    if not isinstance(value, dict):
        raise GuardianCompileError("invalid_persisted_state", f"Invalid persisted {field}")
    return value


def compile_desired_state(server: Server, blueprint: Blueprint) -> dict[str, Any]:
    desired_power_state = str(getattr(server, "desired_power_state", "stopped"))
    if desired_power_state not in {"running", "stopped"}:
        raise GuardianCompileError("invalid_desired_power_state", "Invalid desired power state")
    generation = int(getattr(server, "desired_state_generation", 1) or 0)
    if generation < 1:
        raise GuardianCompileError("invalid_generation", "Invalid desired state generation")
    payload: dict[str, Any] = {
        "schema_version": GUARDIAN_SCHEMA_VERSION,
        "server_id": int(server.id),
        "generation": generation,
        "desired_power_state": desired_power_state,
        "guardian_enabled": is_guardian_enabled(blueprint),
        "recovery_suspension": _load_optional_json(
            getattr(server, "guardian_recovery_suspension", None),
            "Guardian recovery suspension",
        ),
        "quarantine_control": _load_optional_json(
            getattr(server, "guardian_quarantine_control", None),
            "Guardian quarantine control",
        ),
        "guardian": compile_guardian_config(server, blueprint),
    }
    if _ANY_TOKEN_RE.search(json.dumps(payload, ensure_ascii=False)):
        raise GuardianCompileError(
            "unresolved_placeholder",
            "Guardian payload contains an unresolved Blueprint placeholder",
        )
    payload["payload_hash"] = canonical_payload_hash(payload)
    return payload


def required_capabilities(payload: dict[str, Any]) -> GuardianRequirements:
    guardian = payload.get("guardian") or {}
    return GuardianRequirements(
        schema_version=int(payload.get("schema_version") or 0),
        probe_types=frozenset(
            str(check.get("type")) for check in guardian.get("health_checks", [])
        ),
        diagnostic_parsers=frozenset(
            str(value) for value in (guardian.get("diagnostics") or {}).get("parsers", [])
        ),
        recovery_actions=frozenset(
            str(policy.get("action")) for policy in (guardian.get("recovery") or {}).get("policies", [])
        ),
    )


def validate_agent_capabilities(payload: dict[str, Any], capabilities: dict[str, Any]) -> None:
    required = required_capabilities(payload)
    missing: dict[str, list[Any]] = {}
    schemas = {int(value) for value in capabilities.get("guardian_schema_versions", [])}
    if required.schema_version not in schemas:
        missing["guardian_schema_versions"] = [required.schema_version]
    probe_missing = sorted(required.probe_types - set(capabilities.get("probe_types", [])))
    parser_missing = sorted(
        required.diagnostic_parsers - set(capabilities.get("diagnostic_parsers", []))
    )
    action_missing = sorted(
        required.recovery_actions - set(capabilities.get("recovery_actions", []))
    )
    if probe_missing:
        missing["probe_types"] = probe_missing
    if parser_missing:
        missing["diagnostic_parsers"] = parser_missing
    if action_missing:
        missing["recovery_actions"] = action_missing
    if missing:
        raise GuardianCompileError(
            "guardian_capability_mismatch",
            "Agent does not support the required Guardian capabilities",
            details={"unsupported": missing},
        )
