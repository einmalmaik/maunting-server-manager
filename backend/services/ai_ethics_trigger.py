"""Strukturierter Ethics-Trigger für das MSM-Agentic-Framework.

Arbeitet zu 100% ohne Regex-Listen oder Wortfilter. Die Risikobewertung erfolgt
rein strukturiert über semantische Werkzeug-Metadaten (`ai_tool_registry.WERKZEUGE`),
Reversibilität, Drittwirkungen und den gewählten Zoning-Modus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services import ai_tool_registry


RiskLevel = Literal["low", "review", "critical"]
EthicsMode = Literal["off", "auto", "always", "critical"]


@dataclass(frozen=True)
class DecisionContext:
    """Der strukturierte Kontext, der bei Bedarf an die Ethics Engine geht."""

    goal: str
    planned_action: str
    tool_name: str
    tool_arguments: dict[str, Any]
    server_id: int | None
    is_destructive: bool
    requires_confirmation: bool
    autonomous: bool
    target_description: str | None = None


@dataclass(frozen=True)
class EthicsTriggerResult:
    """Das Ergebnis der Trigger-Vorfilterung."""

    should_evaluate: bool
    risk_level: RiskLevel
    reason: str
    decision_context: DecisionContext


def evaluate_action_risk(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    goal: str = "",
    planned_action: str = "",
    server_id: int | None = None,
    autonomous: bool = False,
) -> tuple[RiskLevel, bool, str]:
    """Ermittelt das Risiko einer geplanten Aktion rein strukturiert."""
    args = arguments or {}
    spec = ai_tool_registry.WERKZEUGE.get(tool_name)

    # 1. Unumkehrbare / Zerstörerische Handlungen -> CRITICAL
    if spec is not None and spec.immer_bestaetigen:
        return (
            "critical",
            True,
            f"Unumkehrbare oder destruktive Systemaktion ({tool_name})",
        )

    # Spezifische heikle Werkzeuge oder Parameter
    if tool_name in (
        "propose_server_delete",
        "propose_file_delete",
        "propose_backup_restore",
        "propose_blueprint_delete",
        "propose_ai_tarif_role",
    ):
        return (
            "critical",
            True,
            f"Kritische Lösch- oder Tarifänderung ({tool_name})",
        )

    # Desktop-System-Aktionen
    if tool_name.startswith("desktop_"):
        if tool_name in ("desktop_aufraeumen", "desktop_befehl"):
            return (
                "critical",
                True,
                f"Potenziell eingreifende Desktop-Aktion ({tool_name})",
            )
        return (
            "review",
            False,
            f"Interaktive Desktop-Steuerung ({tool_name})",
        )

    # 2. Schreibaktionen & Außenkommunikation -> REVIEW
    if spec is not None and spec.art in ("server_write", "global_write"):
        if tool_name == "propose_email_send":
            return (
                "review",
                False,
                "Ausgehende E-Mail-Kommunikation an Dritte",
            )
        return (
            "review",
            False,
            f"Konfigurations- oder Schreibänderung ({tool_name})",
        )

    # Worker-Delegation mit potenziellen Folgeaktionen
    if tool_name == "worker_start":
        return (
            "review",
            False,
            "Start eines Hintergrund-Workers für komplexe Aufgaben",
        )

    # Autonomer Modus erhöht das Prüfbedürfnis
    if autonomous:
        return (
            "review",
            False,
            f"Autonome Ausführung ohne manuelle Benutzerbestätigung ({tool_name})",
        )

    # 3. Reine Lese- oder Informationswerkzeuge -> LOW
    return (
        "low",
        False,
        f"Reine Informations- oder Leseoperation ({tool_name})",
    )


def should_trigger_ethics(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    ethics_mode: str = "auto",
    goal: str = "",
    planned_action: str = "",
    server_id: int | None = None,
    autonomous: bool = False,
    target_description: str | None = None,
) -> EthicsTriggerResult:
    """Entscheidet anhand von strukturiertem Kontext und Modus über den Aufruf der Ethics Engine."""
    mode = (ethics_mode or "auto").strip().lower()
    if mode not in ("off", "auto", "always", "critical"):
        mode = "auto"

    args = arguments or {}
    risk_level, is_destructive, reason = evaluate_action_risk(
        tool_name=tool_name,
        arguments=args,
        goal=goal,
        planned_action=planned_action,
        server_id=server_id,
        autonomous=autonomous,
    )

    context = DecisionContext(
        goal=goal or f"Ausführung von {tool_name}",
        planned_action=planned_action or f"Werkzeugaufruf {tool_name} mit Parametern {list(args.keys())}",
        tool_name=tool_name,
        tool_arguments=args,
        server_id=server_id,
        is_destructive=is_destructive,
        requires_confirmation=is_destructive or (spec := ai_tool_registry.WERKZEUGE.get(tool_name)) is not None and spec.art in ("server_write", "global_write"),
        autonomous=autonomous,
        target_description=target_description,
    )

    if mode == "off":
        return EthicsTriggerResult(
            should_evaluate=False,
            risk_level=risk_level,
            reason="Ethics Engine ist deaktiviert (Modus: off)",
            decision_context=context,
        )

    if mode == "always":
        return EthicsTriggerResult(
            should_evaluate=True,
            risk_level=risk_level,
            reason="Modus 'always': Jede geplante Handlung wird ethisch reflektiert",
            decision_context=context,
        )

    if mode == "critical":
        should_eval = (risk_level == "critical")
        return EthicsTriggerResult(
            should_evaluate=should_eval,
            risk_level=risk_level,
            reason=reason,
            decision_context=context,
        )

    # Standard 'auto': Bei REVIEW und CRITICAL
    should_eval = (risk_level in ("review", "critical"))
    return EthicsTriggerResult(
        should_evaluate=should_eval,
        risk_level=risk_level,
        reason=reason,
        decision_context=context,
    )
