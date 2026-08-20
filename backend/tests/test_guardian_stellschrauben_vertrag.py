"""Panel-Stellschrauben gegen den Agent-Vertrag — zwei Grenzmengen, ein Test.

Der Anlass ist Vorfall 66 vom 20.08.2026: `GUARDIAN_STELLSCHRAUBEN` erlaubte
bei 7 der 12 Schluessel Werte, die der Agent-Vertrag
(msm-agent/services/guardian_contract.py, pydantic mit harten ge/le-Grenzen,
kein Klemmen) mit 422 ablehnt. Werkzeugschema, Payload-Pruefung und
Compiler-Klemmung lasen alle dieselbe zu weite Quelle — jede KI-Uebersteuerung
in der Differenzzone scheiterte deterministisch als
AI_ACTION_GUARDIAN_SYNC_FAILED, wurde zurueckgerollt, und die Heilungsschleife
versuchte es im 13-Minuten-Takt erneut. Kein Test hielt die beiden Mengen
aneinander.

Der Agent-Vertrag wird **per AST gelesen**, nicht importiert: `msm-agent` hat
ein eigenes `services`-Paket, dessen Import mit dem des Backends kollidierte.
Gelesen wird genau das, was pydantic prueft — die ge/le-Schluesselworte der
Field-Aufrufe.
"""

from __future__ import annotations

import ast
from pathlib import Path

from services.ai_action_service import provider_tool_definitions
from services.guardian_runtime_compiler import GUARDIAN_STELLSCHRAUBEN


_VERTRAG = (
    Path(__file__).resolve().parents[2]
    / "msm-agent" / "services" / "guardian_contract.py"
)

#: Welche Stellschraube auf welches Vertragsfeld zeigt. Die Zuordnung ist die
#: aus `_uebersteuern` (guardian_runtime_compiler) — dort wird geschrieben,
#: hier wird geprueft, dass das Geschriebene angenommen wird.
_FELDER: dict[str, tuple[str, str]] = {
    "startup_grace_period_seconds": ("StartupConfig", "grace_period_seconds"),
    "startup_timeout_seconds": ("StartupConfig", "timeout_seconds"),
    "probe_interval_seconds": ("ProbeConfig", "interval_seconds"),
    "probe_timeout_seconds": ("ProbeConfig", "timeout_seconds"),
    "probe_failure_threshold": ("ProbeConfig", "failure_threshold"),
    "probe_success_threshold": ("ProbeConfig", "success_threshold"),
    "recovery_max_attempts": ("RecoveryConfig", "max_attempts"),
    "recovery_attempt_window_seconds": ("RecoveryConfig", "attempt_window_seconds"),
    "recovery_cooldown_seconds": ("RecoveryConfig", "cooldown_seconds"),
    "verification_min_healthy_seconds": (
        "VerificationConfig", "minimum_healthy_duration_seconds",
    ),
    "verification_required_successes": (
        "VerificationConfig", "required_consecutive_successes",
    ),
    "verification_timeout_seconds": (
        "VerificationConfig", "verification_timeout_seconds",
    ),
}

#: ``recovery_max_attempts = 0`` ist die eine gewollte Ausnahme: der Vertrag
#: kennt keine 0 (min 1), und der Compiler uebersetzt sie in eine leere
#: Policy-Liste (`_uebersteuern`). Fuer die Untergrenzen-Pruefung zaehlt
#: deshalb 1 als wirksames Minimum dieses Schluessels.
_UEBERSETZT_NULL = {"recovery_max_attempts"}


def _vertragsgrenzen() -> dict[tuple[str, str], tuple[float, float]]:
    baum = ast.parse(_VERTRAG.read_text(encoding="utf-8"))
    grenzen: dict[tuple[str, str], tuple[float, float]] = {}
    for knoten in baum.body:
        if not isinstance(knoten, ast.ClassDef):
            continue
        for zeile in knoten.body:
            if not isinstance(zeile, ast.AnnAssign) or not isinstance(
                zeile.target, ast.Name
            ):
                continue
            aufruf = zeile.value
            if not (
                isinstance(aufruf, ast.Call)
                and isinstance(aufruf.func, ast.Name)
                and aufruf.func.id == "Field"
            ):
                continue
            werte = {
                kw.arg: kw.value.value
                for kw in aufruf.keywords
                if kw.arg in ("ge", "le") and isinstance(kw.value, ast.Constant)
            }
            if "ge" in werte and "le" in werte:
                grenzen[(knoten.name, zeile.target.id)] = (
                    float(werte["ge"]), float(werte["le"]),
                )
    return grenzen


def test_jede_stellschraube_liegt_im_agent_vertrag() -> None:
    vertrag = _vertragsgrenzen()
    fehler: list[str] = []
    for name, (unten, oben) in GUARDIAN_STELLSCHRAUBEN.items():
        ort = _FELDER[name]
        assert ort in vertrag, f"Vertragsfeld {ort} nicht gefunden — Zuordnung pruefen"
        ge, le = vertrag[ort]
        wirksam_unten = max(unten, 1) if name in _UEBERSETZT_NULL else unten
        if wirksam_unten < ge:
            fehler.append(f"{name}: Panel-Minimum {wirksam_unten} < Agent ge={ge}")
        if oben > le:
            fehler.append(f"{name}: Panel-Maximum {oben} > Agent le={le}")
    assert not fehler, (
        "Panel erlaubt Werte, die der Agent mit 422 ablehnt — jede solche "
        "Uebersteuerung endet als AI_ACTION_GUARDIAN_SYNC_FAILED:\n"
        + "\n".join(fehler)
    )


def test_jede_stellschraube_ist_zugeordnet() -> None:
    """Ein spaeterer Zusatz ohne Vertragszuordnung faellt hier auf."""
    assert set(_FELDER) == set(GUARDIAN_STELLSCHRAUBEN)


def test_das_werkzeugschema_traegt_dieselben_grenzen() -> None:
    """Die dritte Abschrift: das Schema von `propose_guardian_tuning`.

    Es ist von Hand gepflegt (die anderen beiden Leser — Payload-Pruefung und
    Compiler-Klemmung — lesen `GUARDIAN_STELLSCHRAUBEN` direkt). Driftet es,
    schlaegt das Modell Werte vor, die der Vorschlagspfad sofort abweist —
    eine verlorene Runde je Versuch.
    """
    werkzeug = next(
        eintrag["function"]
        for eintrag in provider_tool_definitions()
        if eintrag["function"]["name"] == "propose_guardian_tuning"
    )
    eigenschaften = werkzeug["parameters"]["properties"]
    for name, (unten, oben) in GUARDIAN_STELLSCHRAUBEN.items():
        schema = eigenschaften[name]
        assert schema["minimum"] == unten, f"{name}: Schema-Minimum weicht ab"
        assert schema["maximum"] == oben, f"{name}: Schema-Maximum weicht ab"
