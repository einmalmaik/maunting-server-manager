"""Denkstufen: die Ordnung, die Wahlmöglichkeiten und die Klemmung.

Ein Modell nennt seine Denkstufen als Wörter — ``high``, ``minimal``, ``max``.
Die Wörter allein reichen für die Anzeige, aber nicht für ein Recht: der Satz
„diese Rolle darf höchstens mittel“ verlangt eine **Ordnung**, und die steht
nirgends in der Antwort des Anbieters.

Deshalb hier eine Rangfolge. Sie ist der einzige Ort, an dem MSM den Stufen
eine Bedeutung gibt; überall sonst werden die Wörter des Modells unverändert
durchgereicht.

**Warum der Rollendeckel eine Zahl ist.** ``ai_limit_service._resolve_field``
löst Rollengrenzen mit ``max()`` auf und kennt dabei zwei Sonderregeln — „None
gewinnt als unbegrenzt“ und „keine Rolle konfiguriert heißt unbegrenzt“. Als
Rang reiht sich die Denkgrenze in ``LIMIT_FIELDS`` ein und erbt beides
unverändert, einschließlich „eine zusätzliche, privilegierte Rolle erhöht das
Kontingent“. Als Wort gespeichert bräuchte sie eine zweite Auflösung neben der
bestehenden — und zwei Auflösungen für dasselbe Rechtemodell driften
auseinander.

**Warum die Wahlmöglichkeiten aus dem Katalog kommen.** Gemessen führen 127
Modelle eine Stufenliste, in 20 verschiedenen Zusammenstellungen; 145 weitere
denken, ohne Stufen zu kennen. Eine feste Auswahl im Programm wäre bei der
Mehrheit falsch — mal zu großzügig, mal zu knapp.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from models import AiProvider, User
from services import ai_limit_service, ai_model_catalog
from services.ai_model_catalog import Modell


logger = logging.getLogger(__name__)


#: Die Stufen in aufsteigender Tiefe. Vollständig gegen den OpenRouter-Katalog
#: vom 2026-08-11 abgeglichen: mehr Wörter kommen dort nicht vor.
#:
#: ``none`` steht bewusst **nicht** darin. Es erscheint zwar in manchen
#: Stufenlisten, bedeutet dort aber „nicht nachdenken“ — und das ist keine Tiefe
#: null, sondern der ausgeschaltete Zustand. Er wird über ``aktiv`` ausgedrückt,
#: damit es nicht zwei Wege gibt, dasselbe zu sagen.
RANGFOLGE: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

#: Der Wert, den ein Anbieter für „nicht nachdenken“ in seiner Stufenliste
#: führt. Wird aus der Auswahl entfernt, weil „aus“ bereits eine eigene Option
#: ist; zwei Knöpfe für dieselbe Wirkung sind eine Falle, keine Wahl.
AUS_STUFE = "none"

MIN_RANG = 0
MAX_RANG = len(RANGFOLGE)


def rang(stufe: str) -> int | None:
    """Der Rang einer Stufe, oder ``None`` wenn MSM sie nicht kennt.

    Ein unbekanntes Wort ist kein Fehler: Anbieter führen jederzeit neue Stufen
    ein. Es wird nur nicht angeboten — MSM kann eine Stufe, die es nicht
    einordnen kann, nicht gegen einen Rollendeckel prüfen, und eine
    ungeprüfte Stufe anzubieten hieße, den Deckel zu umgehen.
    """
    try:
        return RANGFOLGE.index(stufe) + 1
    except ValueError:
        return None


def stufe_fuer_rang(wert: int) -> str | None:
    """Das Wort zu einem Rang. ``0`` ist „aus“ und hat keines."""
    if wert <= MIN_RANG or wert > MAX_RANG:
        return None
    return RANGFOLGE[wert - 1]


def waehlbare_stufen(modell: Modell, deckel: int | None) -> list[str]:
    """Die Denkstufen, die dieser Benutzer bei diesem Modell wählen darf.

    Drei Filter nacheinander: was das Modell kann, was MSM einordnen kann, was
    der Deckel zulässt. Die Reihenfolge des Modells bleibt dabei **nicht**
    erhalten — der Katalog liefert absteigend, die Oberfläche zeigt aufsteigend,
    und eine Auswahl, die von tief nach flach läuft, liest sich verkehrt.
    """
    if not modell.denkt:
        return []
    erlaubt: list[tuple[int, str]] = []
    for stufe in modell.stufen:
        if stufe == AUS_STUFE:
            continue
        wert = rang(stufe)
        if wert is None:
            logger.info(
                "Unbekannte Denkstufe %r bei Modell %s — nicht angeboten",
                stufe, modell.model_id,
            )
            continue
        if deckel is not None and wert > deckel:
            continue
        erlaubt.append((wert, stufe))
    return [stufe for _wert, stufe in sorted(erlaubt)]


def darf_nachdenken(modell: Modell, deckel: int | None) -> bool:
    """Ob Nachdenken bei diesem Modell überhaupt zur Wahl steht.

    Ein Deckel von 0 verbietet es. Bei einem Modell mit ``zwingend`` lässt sich
    das nicht durchsetzen — der Anbieter denkt dann ohnehin. Genau deshalb
    meldet diese Funktion in dem Fall ``True``: die Oberfläche soll den
    Zustand zeigen, statt ein „aus“ zu versprechen, das nicht eintritt.
    """
    if not modell.denkt:
        return False
    if modell.zwingend:
        return True
    return deckel is None or deckel > MIN_RANG


def darf_abschalten(modell: Modell) -> bool:
    """Ob „aus“ eine gültige Wahl ist. Bei 82 der 402 Modelle ist sie es nicht."""
    return modell.denkt and not modell.zwingend


def klemmen(
    modell: Modell, *, wunsch: str | None, aktiv: bool, deckel: int | None
) -> tuple[bool, str | None]:
    """Was tatsächlich an den Anbieter geht: (nachdenken, Stufe).

    Hier laufen alle Grenzen zusammen — die Wahl des Benutzers, die Fähigkeiten
    des Modells und der Deckel seiner Rolle. Die Funktion ist bewusst die
    **einzige** Stelle, die das entscheidet: eine zweite Klemmung in der
    Oberfläche wäre eine zweite Wahrheit, und die serverseitige ist die, die
    zählt.

    Ein Wunsch, der über dem Deckel liegt, wird auf den Deckel **gesenkt** statt
    abgewiesen. Der Benutzer bekommt so eine Antwort statt einer Fehlermeldung,
    und die Grenze wirkt trotzdem — sie ist eine Kostengrenze, kein Verbot.
    """
    if not modell.denkt:
        return False, None
    if not aktiv and darf_abschalten(modell):
        return False, None

    erlaubt = waehlbare_stufen(modell, deckel)
    if not erlaubt:
        # Das Modell kennt keine Stufen (die Mehrheit) oder der Deckel lässt
        # keine übrig. Bei einem zwingend denkenden Modell bleibt es trotzdem
        # an — abschalten geht dort nicht, und ein `enabled: false` würde
        # abgewiesen statt befolgt.
        if not aktiv and modell.zwingend:
            return True, None
        if deckel is not None and deckel <= MIN_RANG and darf_abschalten(modell):
            return False, None
        return bool(aktiv) or modell.zwingend, None

    if wunsch in erlaubt:
        return True, wunsch
    gewuenschter_rang = rang(wunsch) if wunsch else None
    if gewuenschter_rang is None:
        # Kein oder ein unverständlicher Wunsch: die Vorgabe des Modells, falls
        # sie erlaubt ist, sonst die tiefste zulässige Stufe. Nicht die höchste
        # — eine fehlende Angabe darf nicht das Teuerste auslösen.
        if modell.standard_stufe in erlaubt:
            return True, modell.standard_stufe
        return True, erlaubt[0]
    # Über dem Deckel: auf die höchste erlaubte senken.
    return True, erlaubt[-1]


# ── Zusammenführung ───────────────────────────────────────────────────
#
# Alles darüber rechnet auf übergebenen Werten und kennt weder Datenbank noch
# Netz — das macht es prüfbar, ohne etwas zu stellen. Darunter steht die eine
# Funktion, die die drei Quellen zusammenholt. Sie ist bewusst die einzige:
# jede weitere Stelle, die selbst Katalog und Deckel kombiniert, wäre eine
# zweite Auslegung derselben Regel.


async def vorgabe(
    client: httpx.AsyncClient,
    db: Session,
    *,
    user: User,
    provider: AiProvider,
    aktiv: bool,
    wunsch: str | None,
) -> tuple[bool, str | None]:
    """Was für diesen Benutzer, diesen Provider und diesen Wunsch tatsächlich gilt.

    Kennt der Katalog das Modell nicht — er war beim ersten Start nicht
    erreichbar, oder der Betreiber hat einen Namen eingetragen, den es nicht
    mehr gibt — bleibt es beim reinen An/Aus **ohne Stufe**. Das ist die
    einzige Annahme, die bei jedem Anbieter dieselbe Bedeutung hat, und sie
    erfindet keine Tiefe, die niemand geprüft hat.
    """
    modell = await ai_model_catalog.finde(
        client, provider.provider_kind, provider.default_model
    )
    deckel = ai_limit_service.resolve_effective_limits(db, user).max_reasoning_effort
    if modell is None:
        if deckel is not None and deckel <= MIN_RANG:
            return False, None
        return bool(aktiv), None
    return klemmen(modell, wunsch=wunsch, aktiv=aktiv, deckel=deckel)
