"""In welcher Währung der Betreiber seine KI-Kosten liest.

Gebucht wird in **US-Cent-Microunits** — überall, ausnahmslos. Der Grund steht
in `ai_usage_service.MICROUNITS_PER_CENT`: OpenRouter meldet die tatsächlich
belasteten Kosten in USD, und eine Umrechnung *vor* der Buchung wäre eine
zweite Fehlerquelle in genau der Zahl, die stimmen soll. Ein Kurs ändert sich
täglich; eine gebuchte Zeile darf sich nicht ändern.

Dieses Modul ist deshalb bewusst klein und sitzt ganz am Rand: es hält zwei
Angaben, mit denen die **Anzeige** aus einer USD-Zahl einen Betrag in der
Währung des Betreibers macht. Umgerechnet wird erst dort, wo jemand liest.

Warum der Kurs vom Betreiber kommt und nicht aus dem Netz: ein Kursdienst wäre
ein weiterer Fremdzugriff, den ein selbstgehostetes Panel weder erklären noch
abschalten kann — für eine Zahl, die niemand auf den Cent braucht. Wer es
genauer will, trägt den Kurs seiner Bank ein; wer in USD abrechnet, lässt
beides, wie es ist.

Alles hier fängt jede Ausnahme ab und fällt auf die Vorgabe zurück. Diese
Funktionen laufen in jeder Verbrauchsantwort, und eine unlesbare Einstellung
darf keine Ansicht umwerfen — schon gar nicht die, mit der jemand nachsieht,
warum die KI ihn abgewiesen hat.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging


logger = logging.getLogger(__name__)

#: Panelweite Anzeigewährung.
WAEHRUNG_KEY = "ai_cost_display_currency"
#: Was ein US-Dollar in der Anzeigewährung wert ist, als Dezimalzahl.
KURS_KEY = "ai_cost_usd_rate"

STANDARD_WAEHRUNG = "USD"
WAEHRUNGEN = ("EUR", "USD")

STANDARD_KURS = Decimal("1")
#: Grenzen für den Kurs. Weit genug für jede reale Währung, eng genug, dass ein
#: verrutschtes Komma auffällt, bevor es in einer Kostenanzeige landet.
MIN_KURS = Decimal("0.01")
MAX_KURS = Decimal("100")
#: Mehr Nachkommastellen als vier trägt kein Wechselkurs, den ein Mensch pflegt.
KURS_STELLEN = 4


@dataclass(frozen=True)
class Kostenpolitik:
    """Wie Beträge angezeigt werden. Nicht, wie sie gebucht werden.

    ``kurs`` ist der Faktor von USD in die Anzeigewährung: bei ``EUR`` und einem
    Kurs von ``0,92`` werden aus 2,00 USD 1,84 EUR. Bei ``USD`` ist er immer
    genau 1 — die Umrechnung von einer Währung in sich selbst ist keine.
    """

    waehrung: str
    kurs: Decimal

    @property
    def umrechnung_noetig(self) -> bool:
        """Ob neben dem Betrag noch der USD-Wert stehen sollte."""
        return self.waehrung != "USD"


def _roh(key: str) -> str:
    from services.panel_settings_service import PanelSettingsService

    return (PanelSettingsService.get(key, "") or "").strip()


def waehrung() -> str:
    """Die Anzeigewährung. Im Zweifel USD — die Währung der Buchung."""
    try:
        wert = _roh(WAEHRUNG_KEY).upper()
    except Exception as exc:
        logger.warning("Anzeigewaehrung nicht lesbar error=%s", type(exc).__name__)
        return STANDARD_WAEHRUNG
    return wert if wert in WAEHRUNGEN else STANDARD_WAEHRUNG


def kurs() -> Decimal:
    """Was ein US-Dollar in der Anzeigewährung wert ist.

    Bei Anzeigewährung USD ohne Rückfrage 1. Ein dort hinterlegter Kurs wäre
    eine Angabe, die sich selbst widerspricht, und die Anzeige würde ihr folgen.
    """
    if waehrung() == "USD":
        return STANDARD_KURS
    try:
        roh = _roh(KURS_KEY)
    except Exception as exc:
        logger.warning("Wechselkurs nicht lesbar error=%s", type(exc).__name__)
        return STANDARD_KURS
    try:
        wert = Decimal(roh.replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return STANDARD_KURS
    if not wert.is_finite() or not MIN_KURS <= wert <= MAX_KURS:
        return STANDARD_KURS
    return wert


def politik() -> Kostenpolitik:
    """Beides zusammen — was jede Verbrauchsantwort mitschickt."""
    return Kostenpolitik(waehrung=waehrung(), kurs=kurs())


def setzen(*, neue_waehrung: str, neuer_kurs: str | None) -> Kostenpolitik:
    """Setzt Währung und Kurs. Wirft bei allem, was nicht passt.

    Anders als beim Lesen wird hier **nichts** stillschweigend zurechtgebogen:
    beim Lesen schützt die Vorgabe eine Ansicht, beim Schreiben würde sie eine
    Eingabe verschlucken. Wer 0,92 tippen wollte und 92 abgeschickt hat, soll
    das erfahren und nicht später in seiner Kostenanzeige suchen.

    ``neuer_kurs`` darf bei Anzeigewährung USD fehlen: dort gibt es keinen.
    """
    ziel = (neue_waehrung or "").strip().upper()
    if ziel not in WAEHRUNGEN:
        raise ValueError("Unbekannte Anzeigewaehrung")

    if ziel == "USD":
        wert = STANDARD_KURS
    else:
        if neuer_kurs is None or not str(neuer_kurs).strip():
            raise ValueError("Fuer diese Waehrung wird ein Wechselkurs gebraucht")
        try:
            wert = Decimal(str(neuer_kurs).strip().replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Wechselkurs muss eine Zahl sein") from exc
        if not wert.is_finite() or not MIN_KURS <= wert <= MAX_KURS:
            raise ValueError("Wechselkurs ausserhalb des zulaessigen Bereichs")
        # Auf vier Stellen festgelegt, bevor gespeichert wird: sonst steht in
        # der Einstellung eine Genauigkeit, die die Anzeige nie zeigt, und die
        # Zahl im Formular ist nach dem Speichern eine andere als davor.
        wert = round(wert, KURS_STELLEN).normalize()

    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(WAEHRUNG_KEY, ziel)
    PanelSettingsService.set(KURS_KEY, format(wert, "f"))
    return Kostenpolitik(waehrung=ziel, kurs=wert)
