"""Die Betreiber-Deckel der Worker: wie viele gleichzeitig, wie viele Runden.

docs/agentic-framework.md (Abschnitt 5) verlangt beides beim Betreiber, weil
er zahlt: ohne Deckel wird „schau die Server nach, mach den Kalender, und noch
drei Sachen" zum unsichtbaren Dauerverbraucher.

**Panelweit und nicht je Rolle** — bewusst gegen das `role_ai_limits`-Muster
entschieden. Dort heisst „keine Rolle konfiguriert" unbegrenzt, und ein
unbegrenzter Worker-Deckel ist genau der Zustand, den es hier nie geben darf:
diese Deckel muessen **ohne jede Konfiguration** gelten. Ausserdem sagt die
Doku ausdruecklich „Der Kunde stellt Worker nicht ein" — es gibt nichts je
Rolle zu verkaufen. Sollte ein Tarif spaeter Worker-Kapazitaet verkaufen,
zieht der Deckel nach `role_ai_limits` um; die Maschinerie dort steht bereit.

Das Muster ist woertlich `ai_context_window.schwelle_prozent`: eine Konstante
als Vorgabe, ein validierender Leser, der bei allem Unlesbaren auf die Vorgabe
zurueckfaellt (die Frage faellt beim Start jedes Worker-Laufs — eine kaputte
Einstellung darf dort nichts umwerfen), und ein Setter, der wirft.

Prozesslokal wie alle `PanelSettingsService`-Werte: bei mehreren
Backend-Prozessen sieht ein anderer Prozess eine Aenderung erst nach
Invalidierung/Neustart — dieselbe Betriebsgrenze wie bei der Faltmarke.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


#: Wie viele Worker ein Benutzer gleichzeitig laufen lassen darf.
#: Gezaehlt werden nicht beendete Laeufe in Unterhaltungen ``kind='worker'`` —
#: auch geparkte (`waiting_*`): ein geparkter Langlaeufer ist ein offener
#: Auftrag, kein freier Platz.
MAX_WORKER_KEY = "ai_worker_max_parallel"
STANDARD_WORKER = 3
MIN_WORKER = 1
#: Obergrenze der Einstellung, nicht des Bedarfs: mehr als 16 gleichzeitige
#: Auftraege **eines** Benutzers sind kein Arbeitsstil mehr, sondern eine
#: Schleife — und jeder davon zahlt auf `concurrent_operations` und
#: `requests_per_minute` desselben Benutzers ein.
MAX_WORKER = 16

#: Wieviele Werkzeugrunden ein einzelner Worker-Lauf hoechstens bekommt.
#: Unterschreitet die harte Code-Kappe `ai_stream_service.MAX_TOOL_ROUNDS`
#: oder trifft sie — nie mehr. Die Gleichheit der Obergrenzen haelt ein Test,
#: kein Import: `ai_stream_service` wird diesen Modul kuenftig lesen, und ein
#: Import in die Gegenrichtung waere ein Zyklus.
RUNDEN_KEY = "ai_worker_rundenbudget"
STANDARD_RUNDEN = 48
MIN_RUNDEN = 4
MAX_RUNDEN = 48


def _lesen(key: str, standard: int, minimum: int, maximum: int) -> int:
    """Ein Deckel aus den Panel-Einstellungen — im Zweifel die Vorgabe."""
    try:
        from services.panel_settings_service import PanelSettingsService

        roh = (PanelSettingsService.get(key, "") or "").strip()
    except Exception as exc:
        logger.warning("Worker-Deckel %s nicht lesbar error=%s", key, type(exc).__name__)
        return standard
    try:
        wert = int(roh)
    except ValueError:
        return standard
    return wert if minimum <= wert <= maximum else standard


def _setzen(key: str, wert: int, minimum: int, maximum: int) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise ValueError("Der Deckel muss eine Zahl sein")
    if not minimum <= wert <= maximum:
        raise ValueError("Der Deckel liegt ausserhalb des zulässigen Bereichs")
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(key, str(wert))
    return wert


def max_worker_je_benutzer() -> int:
    return _lesen(MAX_WORKER_KEY, STANDARD_WORKER, MIN_WORKER, MAX_WORKER)


def set_max_worker_je_benutzer(wert: int) -> int:
    return _setzen(MAX_WORKER_KEY, wert, MIN_WORKER, MAX_WORKER)


def rundenbudget_je_worker() -> int:
    return _lesen(RUNDEN_KEY, STANDARD_RUNDEN, MIN_RUNDEN, MAX_RUNDEN)


def set_rundenbudget_je_worker(wert: int) -> int:
    return _setzen(RUNDEN_KEY, wert, MIN_RUNDEN, MAX_RUNDEN)
