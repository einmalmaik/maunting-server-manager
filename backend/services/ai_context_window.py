"""Wieviel Kontext dieses Modell traegt — und ab wann gefaltet wird.

Vorher standen beide Antworten als feste Zahlen im Programm: 24.000 Zeichen
gingen an den Anbieter, ab 40.000 faltbaren Zeichen wurde zusammengefasst. Das
war richtig, solange alle Modelle aehnlich grosse Fenster hatten. Der Katalog
vom 2026-08-11 fuehrt Fenster von 4.096 bis 1.000.000 Token nebeneinander —
gegenueber dem groessten davon benutzte MSM **ein halbes Prozent** und vergass
den Rest des Gespraechs, ohne dass es jemand sehen konnte.

Drei Entscheidungen, die den Aufbau erklaeren:

**Zeichen, nicht Token.** Der ganze Kontextaufbau rechnet in Zeichen; ein echter
Tokenizer je Modell waere eine Abhaengigkeit pro Anbieterfamilie und muesste mit
jedem neuen Modell nachgezogen werden. ``ZEICHEN_JE_TOKEN`` uebersetzt an genau
einer Stelle, ``SICHERHEIT`` faengt auf, dass vier Zeichen je Token bei Deutsch
und JSON zu optimistisch sind.

**Nie schlechter als vorher.** Jeder Weg, auf dem etwas fehlschlagen kann — der
Katalog ist nicht erreichbar, der Betreiber hat einen Modellnamen eingetragen,
den es nicht gibt, der Eintrag fuehrt kein Fenster — endet in ``unbekannt()``
und damit in exakt den Zahlen, die vorher fuer alle galten. Ein Ausfall macht
den Chat kleiner, nicht kaputt.

**Die Faltmarke gehoert dem Betreiber.** Wie voll das Fenster werden darf, bevor
zusammengefasst wird, ist eine Abwaegung zwischen Kosten und Gedaechtnis, und
die faellt bei einem Hoster anders aus als bei einer Privatinstallation.
Deshalb eine Einstellung und keine Konstante — aber nur **eine**, panelweit:
je Rolle verschieden waere dieselbe Unterhaltung je nach Leser anders gefaltet.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx

from models import AiProvider
from services import ai_model_catalog
from services.ai_provider_registry import Modell


logger = logging.getLogger(__name__)


#: Wie MSM von Zeichen auf Token umrechnet. Dieselbe Faustregel, die
#: ``ai_context_service.estimate_reserved_tokens`` fuer die Kontingentvorschau
#: benutzt — zwei verschiedene Umrechnungen fuer dieselbe Groesse waeren zwei
#: Wahrheiten.
ZEICHEN_JE_TOKEN = 4
#: Abschlag auf das rechnerisch Moegliche. Vier Zeichen je Token gilt fuer
#: fliessendes Englisch; deutsche Komposita und die JSON-Bloecke der
#: Werkzeugergebnisse liegen darunter, die Schaetzung faellt dort also zu
#: guenstig aus. Ohne den Abschlag laendet man gelegentlich knapp ueber dem
#: Fenster, und das ist kein gekuerzter Kontext, sondern eine Absage des
#: Anbieters mitten im Gespraech.
SICHERHEIT = 0.9
#: Platz fuer die Antwort, wenn der Katalog keine Ausgabegrenze nennt. Sie geht
#: vom Fenster ab: Eingabe und Ausgabe teilen es sich.
RESERVE_AUSGABE_TOKENS = 8_192
#: Was gilt, wenn ueber das Modell nichts bekannt ist. 6.000 Token sind die
#: 24.000 Zeichen, die vor dieser Aenderung fuer jedes Modell galten — der
#: Rueckfall ist damit wortwoertlich der alte Zustand.
RUECKFALL_NUTZBAR_TOKENS = 6_000

#: Panelweite Marke: ab wieviel Prozent des nutzbaren Fensters gefaltet wird.
SETTINGS_KEY = "ai_context_compaction_percent"
STANDARD_SCHWELLE = 75
MIN_SCHWELLE = 50
MAX_SCHWELLE = 95


@dataclass(frozen=True)
class Fenster:
    """Das Kontextfenster eines Modells, umgerechnet in MSMs Waehrung.

    ``bekannt`` trennt zwei Zustaende, die sich in den Zahlen gleich anfuehlen
    und voellig verschieden zu behandeln sind: „dieses Modell hat ein kleines
    Fenster“ und „wir wissen nichts ueber dieses Modell“. Die Oberflaeche zeigt
    im zweiten Fall keinen Prozentwert — ein erfundener waere schlimmer als
    keiner, weil man ihm ansaehe, dass er stimmt.
    """

    bekannt: bool
    #: Das volle Fenster laut Katalog. ``0``, wenn unbekannt.
    fenster_tokens: int
    #: Was die Eingabe davon fuellen darf — abzueglich Antwort und Sicherheit.
    nutzbar_tokens: int
    #: Dasselbe in Zeichen. Das ist die Zahl, mit der der Kontextaufbau rechnet.
    zeichen: int


def unbekannt() -> Fenster:
    """Der Rueckfall: genau das Verhalten von vor dieser Aenderung."""
    return Fenster(
        bekannt=False,
        fenster_tokens=0,
        nutzbar_tokens=RUECKFALL_NUTZBAR_TOKENS,
        zeichen=RUECKFALL_NUTZBAR_TOKENS * ZEICHEN_JE_TOKEN,
    )


def aus_modell(modell: Modell | None) -> Fenster:
    """Rechnet ein Katalogmodell in ein nutzbares Budget um.

    Rein rechnend, ohne Netz und ohne Datenbank — deshalb pruefbar, ohne etwas
    zu stellen.

    Die Reserve wird auf ein Viertel des Fensters geklemmt, und das ist kein
    theoretischer Fall: der Katalog fuehrt Modelle, deren
    ``max_completion_tokens`` dem ``context_length`` **entspricht** (Nemotron
    3.5 Lightning: 262144 zu 262144). Ohne die Klemmung bliebe dort fuer die
    Eingabe nichts uebrig, und ein Modell mit einem Viertelmillionen-Fenster
    fiele auf den Rueckfall zurueck.

    Ein **bekanntes** kleines Fenster wird nicht auf ``RUECKFALL_NUTZBAR_TOKENS``
    angehoben, auch wenn das Ergebnis dann unter dem frueheren Wert liegt. Der
    Rueckfall ist eine Annahme fuer den Fall, dass wir nichts wissen; gegen ein
    Modell mit 4.096 Token angewandt waere er schlicht falsch, und die Anfrage
    liefe nicht knapper, sondern gar nicht.
    """
    if modell is None or not modell.kontext_tokens:
        return unbekannt()
    fenster = modell.kontext_tokens
    reserve = min(modell.max_ausgabe_tokens or RESERVE_AUSGABE_TOKENS, fenster // 4)
    nutzbar = max(int((fenster - reserve) * SICHERHEIT), 1)
    return Fenster(
        bekannt=True,
        fenster_tokens=fenster,
        nutzbar_tokens=nutzbar,
        zeichen=nutzbar * ZEICHEN_JE_TOKEN,
    )


async def ermitteln(client: httpx.AsyncClient, provider: AiProvider) -> Fenster:
    """Das Fenster des eingestellten Modells dieses Providers.

    Die **einzige** Stelle, die Katalog und Rechnung zusammenfuehrt — genau wie
    ``ai_reasoning.vorgabe`` bei den Denkstufen. Jede weitere waere eine zweite
    Auslegung derselben Regel.

    Der Katalogabruf laeuft aus dem Zwischenspeicher und kostet im Normalfall
    nichts. Faellt er aus, faengt ``ai_model_catalog`` das bereits ab und
    liefert den letzten Stand oder eine leere Liste; hier kommt dann ``None``
    an und daraus ``unbekannt()``.
    """
    try:
        modell = await ai_model_catalog.finde(
            client, provider.provider_kind, provider.default_model
        )
    except Exception as exc:
        # Der Katalog faengt seine eigenen Fehler ab; was hier noch ankommt,
        # ist unerwartet. Es darf trotzdem keinen Chat anhalten.
        logger.warning("Kontextfenster nicht ermittelbar error=%s", type(exc).__name__)
        return unbekannt()
    return aus_modell(modell)


def schwelle_prozent() -> int:
    """Ab wieviel Prozent Fuellstand gefaltet wird.

    Faengt bewusst alles ab: diese Funktion laeuft am Ende jedes Streams, und
    ein unlesbarer Einstellungswert darf dort nichts umwerfen. Im Zweifel gilt
    die Vorgabe.
    """
    try:
        from services.panel_settings_service import PanelSettingsService

        roh = (PanelSettingsService.get(SETTINGS_KEY, "") or "").strip()
    except Exception as exc:
        logger.warning("Faltmarke nicht lesbar error=%s", type(exc).__name__)
        return STANDARD_SCHWELLE
    try:
        wert = int(roh)
    except ValueError:
        return STANDARD_SCHWELLE
    return wert if MIN_SCHWELLE <= wert <= MAX_SCHWELLE else STANDARD_SCHWELLE


def set_schwelle_prozent(wert: int) -> int:
    """Setzt die Marke. Wirft bei allem ausserhalb 50–95.

    Die Grenzen sind keine Willkuer: unter 50 % faltet der Chat staendig und
    verliert mehr Verlauf, als er Kosten spart; ueber 95 % bleibt kein Platz
    mehr fuer die Antwort und die Anfrage, die das Falten ausloest.
    """
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise ValueError("Faltmarke muss eine Zahl sein")
    if not MIN_SCHWELLE <= wert <= MAX_SCHWELLE:
        raise ValueError("Faltmarke ausserhalb des zulaessigen Bereichs")
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(SETTINGS_KEY, str(wert))
    return wert


def faltmarke_zeichen_aus_budget(context_chars: int) -> int:
    """Ab wievielen Zeichen faltbaren Materials zusammengefasst wird.

    Nimmt das Budget als Zahl und nicht als ``Fenster``: ab dem Anlegen eines
    Laufs reist es als ``context_chars`` durch den JSON-Zustand, und ein
    Dataclass-Objekt ueberlebte diese Reise nicht.
    """
    return context_chars * schwelle_prozent() // 100
