"""Ob und wie die KI global gueltige Skills anlegen darf.

Ein global gelernter Skill wirkt fuer **jeden** Benutzer des Panels — bei einem
Hoster also fuer alle Kunden. Er ist damit die einzige Stelle, an der ein
Gespraech Text in den Kontext fremder Gespraeche bringen kann, und braucht
deshalb eine eigene Entscheidung des Betreibers.

Drei Stufen, absichtlich in dieser Reihenfolge:

- ``off``      — nur der Betreiber legt globale Skills an. Die KI lernt
                 ausschliesslich ins Team und **erfaehrt das im Prompt**, damit
                 sie es nicht vergeblich versucht.
- ``review``   — Standard. Wer `ai.skills.manage` hat, dessen Erkenntnis wirkt
                 sofort; aus einem Kundengespraech entsteht ein Eintrag, der
                 wartet. So geht der Lerneffekt aus Kundengespraechen nicht
                 verloren, aber niemand kann die KI zu einer sofort wirksamen
                 panelweiten Anweisung ueberreden.
- ``instant``  — jedes Gespraech kann sofort global wirken. Am fluessigsten,
                 mit dem entsprechenden Preis.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_global_skill_learning"
POLICIES = ("off", "review", "instant")
DEFAULT_POLICY = "review"


def policy() -> str:
    """Die eingestellte Stufe. Faengt bewusst alles ab.

    Diese Funktion laeuft im Aufbau des Systemprompts. Ein Fehler beim Lesen
    der Einstellung darf den Chat nicht anhalten — im Zweifel gilt die
    vorsichtige Voreinstellung.
    """
    try:
        from services.panel_settings_service import PanelSettingsService

        value = (PanelSettingsService.get(SETTINGS_KEY, DEFAULT_POLICY) or "").strip()
    except Exception as exc:
        logger.warning("Lernpolitik nicht lesbar error=%s", type(exc).__name__)
        return DEFAULT_POLICY
    return value if value in POLICIES else DEFAULT_POLICY


def set_policy(value: str) -> str:
    from services.panel_settings_service import PanelSettingsService

    normalized = (value or "").strip()
    if normalized not in POLICIES:
        raise ValueError("Unbekannte Lernpolitik")
    PanelSettingsService.set(SETTINGS_KEY, normalized)
    return normalized


def resolve_global_status(may_manage: bool) -> str | None:
    """Welchen Status ein global gelernter Skill bekommt — oder ``None``.

    ``None`` heisst: global ist hier gar nicht moeglich, der Aufrufer soll ins
    Team schreiben. Das ist ausdruecklich kein Fehler, sondern der Normalfall
    bei abgeschaltetem globalem Lernen.
    """
    current = policy()
    if current == "off":
        return None
    if current == "instant":
        return "active"
    return "active" if may_manage else "pending"
