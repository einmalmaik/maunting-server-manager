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
from services import ai_limit_service, ai_model_catalog, ai_provider_registry
from services.ai_provider_registry import Modell


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
#: führt. Wird aus der **Auswahl** entfernt, weil „aus“ dort bereits eine eigene
#: Option ist; zwei Knöpfe für dieselbe Wirkung sind eine Falle, keine Wahl.
#:
#: In der **Anfrage** ist er sehr wohl nötig, und zwar bei jedem Anbieter ohne
#: Schalter — dort ist „aus“ nichts anderes als diese Stufe. Wer beides
#: verwechselt, schaltet in der Oberfläche ab und lässt draußen weiterdenken;
#: siehe `_aus`.
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


# Hier stand ``stufe_fuer_rang(wert)`` — das Wort zu einem Rang. Geschrieben
# fuer einen Aufrufer, der nie kam: die Oberflaeche pflegt die Woerter selbst
# (`AiTab.tsx`, per Test gegen `RANGFOLGE` gehalten), und der Server rechnet
# ueberall vom Wort zum Rang, nie zurueck. Entfernt, weil eine ungenutzte
# Funktion beim naechsten Umbau mitwandert und dabei aussieht, als hinge etwas
# an ihr.


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


def _kennt_schalter(kind: str) -> bool:
    """Ob dieser Anbieter „aus“ auch **ohne** Stufenwort ausdrücken kann.

    Gefragt wird der Wortschatz, nicht der Name: wer ``reasoning`` in
    `ai_provider_registry.basis.Anbieter.anfrage_erweiterungen` führt, bekommt
    ``{"enabled": false}`` und hat damit ein Aus, das keine Stufe braucht. Wer
    ihn nicht führt, hat nur Stufen — und dort ist „nichts gesendet“ eben nicht
    „aus“, sondern „deine Vorgabe“.

    Ein unbekannter Schlüssel ergibt ``False`` und nicht ``KeyError``: gebraucht
    wird die Antwort nur für eine Protokollzeile, und die darf einen Lauf nicht
    mitnehmen.
    """
    try:
        return "reasoning" in ai_provider_registry.anbieter(kind).anfrage_erweiterungen
    except KeyError:
        return False


def _vertraegt_werkzeuge_mit_stufe(kind: str) -> bool:
    """Ob dieser Anbieter ``tools`` und eine Denkstufe in **einer** Anfrage nimmt.

    Gefragt wird wieder der Anbieter selbst und nicht sein Name — die Marke
    steht in `ai_provider_registry.basis.Anbieter.werkzeuge_mit_denkstufe`, samt
    der Messung, die sie begründet. Der Wortlaut der Ablehnung („use
    /v1/responses or set reasoning_effort to 'none'") steht dort ebenfalls.

    Ein unbekannter Schlüssel ergibt ``True`` und nicht ``KeyError``: das ist
    der Zustand von vor dieser Marke, also das Verhalten jedes Anbieters, der
    sie nicht führt. Eine Einschränkung zu raten, wäre schlimmer als sie zu
    verpassen — sie kostet Denkleistung, die der Anbieter angeboten hätte.
    """
    try:
        return ai_provider_registry.anbieter(kind).werkzeuge_mit_denkstufe
    except KeyError:
        return True


def _aus(modell: Modell) -> tuple[bool, str | None]:
    """„Nicht nachdenken“ — und, falls das Modell ein Wort dafür führt, welches.

    Zwei Anbieter sagen dasselbe verschieden. OpenRouter kennt einen Schalter
    (``reasoning: {"enabled": false}``), und dort genügt die ``False`` links;
    das Wort daneben wird gar nicht erst gelesen. OpenAI kennt **keinen**
    Schalter, sondern nur eine Stufe — dort heißt „aus“ ``reasoning_effort:
    "none"``, und ohne dieses Wort geht schlicht nichts hinaus. Das Modell
    denkt dann in OpenAIs Voreinstellung weiter, bei ``gpt-5.5`` also
    ``medium``: der Betreiber hat abgeschaltet und bezahlt trotzdem.

    Deshalb steht das Wort hier und nicht im Adapter. Ob ein Modell es
    verträgt, weiß nur der Katalog — ``gpt-5.1`` führt ``none`` in seinen
    Stufen, ``gpt-5.1-codex-mini`` nicht, und beide sind abschaltbar im Sinne
    von `darf_abschalten`. Aus ``zwingend`` lässt es sich also nicht
    erschließen; es ist eine Angabe und keine Folgerung.

    Führt das Modell kein solches Wort, bleibt es bei ``None`` — dann sendet
    MSM zum Nachdenken gar nichts, wie überall sonst auch, wenn es etwas nicht
    weiß.
    """
    return False, (AUS_STUFE if AUS_STUFE in modell.stufen else None)


def klemmen(
    modell: Modell,
    *,
    wunsch: str | None,
    aktiv: bool,
    deckel: int | None,
    mit_werkzeugen: bool = False,
    kind: str | None = None,
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

    **Weglassen ist keine Grenze.** Wer keine Stufe mitschickt, bekommt nicht
    die billigste, sondern die Vorgabe des Anbieters — bei OpenRouter ist das
    für die meisten Modelle ``medium``, für manche ``high``. Deshalb darf ein
    fehlendes ``effort`` nur dort stehen, wo das Modell wirklich keine Stufen
    kennt. Überall sonst wird eine Stufe genannt, auch wenn die Rechnung dafür
    einen Umweg braucht.

    **Auch „aus“ kann eine Stufe sein.** Die zweite Rückgabe ist dann
    ``AUS_STUFE``, und der erste Wert bleibt trotzdem ``False`` — beides
    zusammen sagt dasselbe, nur für zwei verschiedene Mundarten. Welche ein
    Anbieter spricht, entscheidet er selbst (`ai_provider_registry.basis`); wer
    einen Schalter kennt, liest das Wort nicht. Warum es überhaupt gebraucht
    wird, steht bei `_aus`.

    **``mit_werkzeugen`` ist die einzige Grenze, die nicht vom Modell kommt,
    sondern vom Endpunkt.** OpenAIs ``/chat/completions`` lehnt eine Anfrage
    ab, die ``tools`` und eine echte Stufe zugleich trägt (Marke
    `werkzeuge_mit_denkstufe`; die Messung steht in der Anbieterdatei). Sie
    trotzdem hinauszuschicken hiesse, ein ``400`` zu erzeugen, dessen Ursache
    MSM vorher kennt — deshalb fällt hier auf „aus“ zurück, mit derselben
    Mundart wie überall (`_aus`), und der Lauf antwortet statt zu scheitern.

    Das ist ausdrücklich eine **Einschränkung und kein Ersatz**: bei diesem
    Anbieter denkt das Modell im Werkzeuglauf nicht. Der Betreiber sieht die
    Stufen in der Oberfläche trotzdem — sie gelten für die Läufe ohne
    Werkzeuge, und wer beides zugleich braucht, nimmt denselben Modellnamen
    über OpenRouter.
    """
    if not modell.denkt:
        return False, None
    if not aktiv and darf_abschalten(modell):
        return _aus(modell)
    # Vor allem anderen: verträgt dieser Anbieter überhaupt eine Stufe neben
    # Werkzeugen? Wenn nicht, ist jede Rechnung darunter gegenstandslos — die
    # Anfrage würde abgelehnt, egal welche Stufe herauskäme. `darf_abschalten`
    # bleibt trotzdem die Bedingung: ein Modell mit Denkzwang lässt sich nicht
    # abschalten, und dort ist die Vorgabe des Anbieters die einzige Wahl, die
    # der Endpunkt noch zulässt.
    if mit_werkzeugen and kind is not None and not _vertraegt_werkzeuge_mit_stufe(kind):
        if darf_abschalten(modell):
            return _aus(modell)
        return True, None

    # Ohne Deckel: was das Modell überhaupt kann. Mit Deckel: was diese Rolle
    # davon darf. Der Vergleich der beiden trennt zwei Zustände, die sich von
    # außen gleich anfühlen und völlig verschieden zu behandeln sind.
    kann = waehlbare_stufen(modell, None)
    erlaubt = waehlbare_stufen(modell, deckel)

    if not erlaubt:
        if kann:
            # Der Deckel hat **alles** weggeschnitten: die Rolle darf höchstens
            # `low`, das Modell fängt erst bei `high` an. Bisher fiel dieser
            # Fall mit „Modell ohne Stufen" zusammen und ergab „an, ohne
            # Stufe" — also genau die Vorgabe des Anbieters, die über dem
            # Deckel liegt. Die Rolle durfte `low` und bezahlte `high`.
            if darf_abschalten(modell):
                return _aus(modell)
            # Denkzwang: abschalten geht nicht. Dann wenigstens die flachste
            # Stufe, die das Modell kennt, statt der Vorgabe des Anbieters.
            return True, kann[0]
        # Das Modell kennt gar keine Stufen — für 145 der 272 denkenden
        # Modelle ist das der Normalfall. Hier ist „an, ohne Stufe" richtig
        # und nicht bloß die Notlösung.
        if deckel is not None and deckel <= MIN_RANG and darf_abschalten(modell):
            return _aus(modell)
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
    # Der Wunsch ist einzuordnen, aber nicht wählbar — und das geht in **zwei**
    # Richtungen. Bisher wurde jede Abweichung als „zu hoch" behandelt und auf
    # `erlaubt[-1]` gesetzt; wer bei einem Modell ab `high` um `low` bat, bekam
    # dadurch `max`. Die Bitte um wenig darf nicht das Teuerste auslösen.
    if gewuenschter_rang > rang(erlaubt[-1]):
        return True, erlaubt[-1]
    return True, erlaubt[0]


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
    mit_werkzeugen: bool = True,
) -> tuple[bool, str | None]:
    """Was für diesen Benutzer, diesen Provider und diesen Wunsch tatsächlich gilt.

    Kennt der Katalog das Modell nicht — er war beim ersten Start nicht
    erreichbar, oder der Betreiber hat einen Namen eingetragen, den es nicht
    mehr gibt — bleibt es beim reinen An/Aus **ohne Stufe**. Das erfindet keine
    Tiefe, die niemand geprüft hat.

    ``mit_werkzeugen`` ist hier ``True`` als Vorgabe, und das ist die ehrliche
    Beschreibung des einzigen Aufrufers: ein Chatlauf trägt den Werkzeugkatalog
    immer mit. Der Parameter steht trotzdem da, weil die Vorgabe eine Annahme
    über den Aufrufer ist und keine Eigenschaft der Funktion — ein Lauf ohne
    Werkzeuge bekäme sonst still eine Stufe weniger, als ihm zusteht. Was die
    Angabe bewirkt, steht bei `klemmen`.

    **Und es ist die Stelle, an der ein Deckel von 0 nicht überall greift.**
    Hier stand einmal, ohne Stufe sei „die einzige Annahme, die bei jedem
    Anbieter dieselbe Bedeutung hat". Das ist falsch, und `_aus` zwölf Zeilen
    weiter oben sagt auch warum: bei einem Anbieter mit Schalter heißt „keine
    Stufe" tatsächlich aus, bei einem Anbieter mit nur Stufen heißt es „nimm
    deine Vorgabe". Bei OpenAI denkt das Modell also weiter, obwohl die Rolle
    gar nicht nachdenken darf.

    Ausgeschrieben wird trotzdem nichts. ``none`` blind mitzuschicken kostet
    mehr, als es einbringt: ein Modell ohne Denkvermögen weist die Zeile
    **hart** ab (``Unrecognized request argument supplied: reasoning_effort``),
    und ob dieses Modell eines ist, weiß genau die Quelle nicht, die hier
    schweigt. Aus einem stummen Katalog würde so ein toter Chat. Der Deckel
    greift wieder, sobald der Katalog antwortet; bis dahin steht es im
    Protokoll, statt still zu passieren.
    """
    modell = await ai_model_catalog.finde(
        client, provider.provider_kind, provider.default_model,
    )
    deckel = ai_limit_service.resolve_effective_limits(db, user).max_reasoning_effort
    if modell is None:
        if deckel is not None and deckel <= MIN_RANG:
            if not _kennt_schalter(provider.provider_kind):
                logger.warning(
                    "Denkdeckel greift nicht: Modell %s bei %s ist dem Katalog "
                    "unbekannt, und dieser Anbieter kennt kein Aus ohne Stufe",
                    provider.default_model, provider.provider_kind,
                )
            return False, None
        return bool(aktiv), None
    return klemmen(
        modell,
        wunsch=wunsch,
        aktiv=aktiv,
        deckel=deckel,
        mit_werkzeugen=mit_werkzeugen,
        kind=provider.provider_kind,
    )


async def aus_fuer(
    client: httpx.AsyncClient,
    provider: AiProvider,
    *,
    api_key: str | None = None,
    model_id: str | None = None,
) -> tuple[bool, str | None]:
    """„Nicht nachdenken" für einen Nebenauftrag — in der Mundart des Anbieters.

    Für alles, was kein Chat ist: Zusammenfassen (`ai_compaction_service`),
    einen Mailsatz formulieren (`ai_mail_text`), ein Diktat abschreiben
    (`ai_stt_chat`). Keiner dieser Aufträge braucht Überlegung, alle drei gingen
    aber mit ``reasoning=False`` und **ohne** Stufe hinaus.

    Bei OpenRouter stimmte das: der Schalter geht mit, das Modell denkt nicht.
    Bei OpenAI ging damit gar nichts hinaus — und „nichts" heißt dort „nimm
    deine Vorgabe". Jede Faltung, jede Betreibermail und jedes abgeschriebene
    Wort wurde also mit Denkschritten bezahlt, die niemand bestellt hatte und
    niemand zu Gesicht bekam. `ai_stt_chat` hatte den Fall sogar im Kommentar
    stehen; nur folgte dem Satz keine Zeile.

    Die Entscheidung selbst fällt nicht hier, sondern in `klemmen` — mit
    ``aktiv=False`` und einem Deckel von ``MIN_RANG``, also genau der Lage
    „diese Anfrage darf nicht nachdenken". Damit gilt hier dieselbe Regel wie
    im Chat, einschließlich der Ausnahme für Modelle mit Denkzwang: bei denen
    geht die **flachste** Stufe hinaus statt der teuren Vorgabe.

    Schweigt der Katalog, bleibt es bei ``(False, None)`` — aus demselben Grund
    wie bei `vorgabe`, und mit demselben Preis.
    """
    modell = await ai_model_catalog.finde(
        client,
        provider.provider_kind,
        model_id or provider.default_model,
        schluessel=api_key,
    )
    if modell is None:
        return False, None
    return klemmen(modell, wunsch=None, aktiv=False, deckel=MIN_RANG)
