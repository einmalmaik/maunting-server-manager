"""Der Modellkatalog: lesen, zwischenspeichern, und Ausfälle überstehen.

Der Katalog ist die einzige Quelle für die Denkfähigkeiten eines Modells. Das
macht zwei Eigenschaften wichtiger als alles andere:

* **Er darf nichts erfinden.** Ein Eintrag ohne Stufenliste heißt „kennt keine
  Stufen“ und nicht „hat wohl die üblichen“.
* **Er darf nichts anhalten.** Fällt der Anbieter aus, gilt der letzte Stand
  weiter — ein veralteter Katalog ist unbrauchbarer als ein frischer, aber
  unendlich viel brauchbarer als gar keiner.

Die Beispieldaten unten sind gekürzte, aber echte Ausschnitte aus dem
OpenRouter-Katalog vom 2026-08-11.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

import httpx
import pytest

from services import ai_model_catalog


ANTWORT = {
    "data": [
        {
            "id": "anthropic/claude-opus-5",
            "name": "Claude Opus 5",
            # Der Katalog nennt das Fenster zweimal. Oben steht das groesste
            # ueber alle Anbieter dieses Modells, in ``top_provider`` das des
            # Anbieters, zu dem im Standardfall geroutet wird — und nur das
            # bekommt man auch.
            "context_length": 1_000_000,
            "top_provider": {
                "context_length": 200_000,
                "max_completion_tokens": 64_000,
                "is_moderated": True,
            },
            "reasoning": {
                "mandatory": False,
                # ``default_enabled`` steht hier, weil der Anbieter es liefert —
                # MSM liest es absichtlich nicht. Der Sendepfad nennt
                # ``enabled`` immer selbst (siehe test_ai_provider_diagnostics),
                # die Voreinstellung des Modells waere also eine Quelle, auf die
                # sich nichts stuetzen darf.
                "default_enabled": True,
                "supported_efforts": ["max", "xhigh", "high", "medium", "low"],
                "default_effort": "high",
            },
        },
        {
            # Der häufigste Fall: denkt, kennt aber keine Stufen.
            "id": "qwen/qwen3.7-flash",
            "name": "Qwen3.7 Flash",
            # Nur oben ein Fenster, ``top_provider`` ohne Ausgabegrenze — auch
            # das kommt im echten Katalog vor.
            "context_length": 128_000,
            "top_provider": {"context_length": None, "max_completion_tokens": None},
            "reasoning": {
                "mandatory": False,
                "default_enabled": True,
                "supports_max_tokens": True,
            },
        },
        {
            "id": "google/gemini-3.6-flash",
            "name": "Gemini 3.6 Flash",
            "reasoning": {
                "mandatory": True,
                "default_enabled": True,
                "supported_efforts": ["high", "medium", "low", "minimal"],
                "default_effort": "high",
            },
        },
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini"},
        # Ein kaputter Eintrag darf die anderen nicht mitnehmen.
        {"name": "Ohne Kennung"},
    ]
}


@pytest.fixture(autouse=True)
def _leerer_cache():
    ai_model_catalog.cache_leeren_fuer_tests()
    yield
    ai_model_catalog.cache_leeren_fuer_tests()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_the_catalog_reports_exactly_what_the_provider_says() -> None:
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        modelle = await ai_model_catalog.modelle(client, "openrouter")

    nach_id = {m.model_id: m for m in modelle}
    # Der kaputte Eintrag fehlt, die vier gültigen sind da.
    assert len(modelle) == 4

    opus = nach_id["anthropic/claude-opus-5"]
    assert opus.denkt is True
    assert opus.stufen == ("max", "xhigh", "high", "medium", "low")
    assert opus.standard_stufe == "high"
    assert opus.zwingend is False

    # Denkt, kennt aber keine Stufen — leere Liste, nicht "denkt nicht".
    qwen = nach_id["qwen/qwen3.7-flash"]
    assert qwen.denkt is True
    assert qwen.stufen == ()

    assert nach_id["google/gemini-3.6-flash"].zwingend is True

    # Kein reasoning-Objekt ist eine Aussage, keine Lücke.
    assert nach_id["openai/gpt-4o-mini"].denkt is False


@pytest.mark.asyncio
async def test_the_window_comes_from_the_provider_that_will_actually_serve() -> None:
    """``top_provider`` schlägt den oberen Wert — sonst planen wir zu grosszügig.

    Oben steht das grösste Fenster über alle Anbieter dieses Modells, in
    ``top_provider`` das des Anbieters, zu dem im Standardfall geroutet wird.
    Nach dem oberen zu rechnen hiesse, eine Anfrage zu bauen, die beim
    tatsächlichen Anbieter nicht mehr hineinpasst.
    """
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        modelle = await ai_model_catalog.modelle(client, "openrouter")

    nach_id = {m.model_id: m for m in modelle}
    opus = nach_id["anthropic/claude-opus-5"]
    assert opus.kontext_tokens == 200_000
    assert opus.max_ausgabe_tokens == 64_000

    # ``top_provider.context_length: null`` ist keine Aussage über das Fenster,
    # sondern eine Lücke — dann gilt der obere Wert.
    qwen = nach_id["qwen/qwen3.7-flash"]
    assert qwen.kontext_tokens == 128_000
    assert qwen.max_ausgabe_tokens is None


@pytest.mark.asyncio
async def test_a_model_without_any_window_is_still_a_valid_entry() -> None:
    """Der Auto Router führt gar kein Fenster — er entscheidet erst zur Laufzeit.

    Ihn deswegen zu verwerfen hiesse, ein wählbares Modell aus der Liste zu
    nehmen. Es fällt später nur auf den Rückfallwert zurück.
    """
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        modelle = await ai_model_catalog.modelle(client, "openrouter")

    ohne = {m.model_id: m for m in modelle}["openai/gpt-4o-mini"]
    assert ohne.kontext_tokens is None


@pytest.mark.asyncio
async def test_the_catalog_is_fetched_once_and_then_cached() -> None:
    aufrufe = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal aufrufe
        aufrufe += 1
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openrouter")
        await ai_model_catalog.modelle(client, "openrouter")
        await ai_model_catalog.finde(client, "openrouter", "anthropic/claude-opus-5")
    assert aufrufe == 1


@pytest.mark.asyncio
async def test_refresh_bypasses_the_cache() -> None:
    """Der häufigste Fall ist nicht das unbekannte Modell, sondern der alte Stand."""
    aufrufe = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal aufrufe
        aufrufe += 1
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openrouter")
        await ai_model_catalog.modelle(client, "openrouter", erzwingen=True)
    assert aufrufe == 2


@pytest.mark.asyncio
async def test_a_failing_provider_never_discards_a_usable_catalog() -> None:
    """Der letzte Stand überlebt den Fehlversuch — sonst fiele der Chat mit aus."""
    antworten = [httpx.Response(200, json=ANTWORT), httpx.Response(503, json={})]

    def handler(_request: httpx.Request) -> httpx.Response:
        return antworten.pop(0) if antworten else httpx.Response(503, json={})

    async with _client(handler) as client:
        erst = await ai_model_catalog.modelle(client, "openrouter")
        assert len(erst) == 4
        danach = await ai_model_catalog.modelle(client, "openrouter", erzwingen=True)

    assert [m.model_id for m in danach] == [m.model_id for m in erst]


@pytest.mark.asyncio
async def test_an_empty_answer_is_treated_as_a_failure_not_as_an_empty_catalog() -> None:
    """Null Modelle sind kein Ergebnis, sondern eine unverstandene Antwort.

    Ohne diese Unterscheidung würde ein Anbieter, der bei einer Störung ein
    leeres `data` liefert, einen brauchbaren Stand überschreiben — und danach
    wäre jedes Modell „unbekannt“.
    """
    antworten = [
        httpx.Response(200, json=ANTWORT),
        httpx.Response(200, json={"data": []}),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return antworten.pop(0) if antworten else httpx.Response(200, json={"data": []})

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openrouter")
        danach = await ai_model_catalog.modelle(client, "openrouter", erzwingen=True)
    assert len(danach) == 4


@pytest.mark.asyncio
async def test_without_any_catalog_the_result_is_empty_not_an_exception() -> None:
    """Beim allerersten Start gibt es nichts zu retten — aber auch keinen Absturz."""
    async with _client(lambda _r: httpx.Response(503, json={})) as client:
        assert await ai_model_catalog.modelle(client, "openrouter") == []
        assert await ai_model_catalog.finde(client, "openrouter", "irgendwas") is None


@pytest.mark.asyncio
async def test_a_hanging_provider_costs_one_attempt_not_one_per_call() -> None:
    """Ein Fehlschlag wird gemerkt — sonst wartet jeder Aufruf erneut die volle Zeit.

    Vorher versuchte jeder Aufruf den Abruf neu, und zwar unter dem modulweiten
    Schloss und mit ``timeout=ABRUF_TIMEOUT``. Ein GET /api/ai/providers fragt
    den Katalog je aktiviertem Provider, das Absenden einer Chatnachricht ueber
    ``ai_reasoning.vorgabe`` noch einmal: aus einem nicht erreichbaren Anbieter
    wurden so 30 Sekunden **mal Anzahl der Aufrufe** fuer eine einzige Anfrage,
    seriell auch fuer alle anderen Benutzer. Der Zaehler hier ist genau dieses
    Vielfache — deshalb zaehlt der Test Versuche und nicht Sekunden.
    """
    versuche = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        return httpx.Response(503, json={})

    async with _client(handler) as client:
        assert await ai_model_catalog.modelle(client, "openrouter") == []
        assert await ai_model_catalog.modelle(client, "openrouter") == []
        assert await ai_model_catalog.finde(client, "openrouter", "irgendwas") is None
    assert versuche == 1


@pytest.mark.asyncio
async def test_the_pause_ends_and_the_reload_button_never_waits_for_it() -> None:
    """Die Ruhefrist darf nicht zum zweiten Zwischenspeicher werden.

    Zwei Wege zurueck zum Anbieter muessen offen bleiben: der Knopf „Modelle neu
    laden“ sofort, und der gewoehnliche Aufruf nach Ablauf der Frist. Ohne den
    ersten sagte der Knopf eine Minute lang nichts Neues, obwohl der Betreiber
    gerade nachgesehen hat; ohne den zweiten waere eine einzige Stoerung
    dauerhaft.
    """
    versuche = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        return httpx.Response(503, json={})

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openrouter")
        assert versuche == 1
        # Innerhalb der Frist: gar keine Frage an den Anbieter.
        await ai_model_catalog.modelle(client, "openrouter")
        assert versuche == 1
        # Der Knopf des Betreibers wartet nicht auf die Frist.
        await ai_model_catalog.modelle(client, "openrouter", erzwingen=True)
        assert versuche == 2
        # Nach Ablauf fragt auch der gewoehnliche Weg wieder. Die Zeit wird
        # zurueckgedreht statt abgewartet — der Zustand liegt im selben Modul,
        # und eine Minute Testlaufzeit waere der falsche Preis dafuer.
        ai_model_catalog._cache["openrouter"].fehler_am -= ai_model_catalog.FEHLER_RUHE
        await ai_model_catalog.modelle(client, "openrouter")
        assert versuche == 3


@pytest.mark.asyncio
async def test_a_keyless_call_to_a_keyed_catalog_never_even_tries() -> None:
    """Ohne Schluessel waere der Abruf ein garantiertes 401 — also gibt es keinen.

    Die Sendepfade (`finde` aus Stream, Kontextfenster, Denkstufen) fragen ohne
    Schluessel. Vor dieser Regel lief ihr Versuch in ein 401, setzte
    ``fehler_am`` — und der **Schluessel-Weg** der Einstellungsseite lieferte
    eine Ruhefrist lang die leere Liste aus, obwohl der Schluessel gespeichert
    war. Die Oberflaeche behauptete dann „erst Schluessel speichern".
    """
    abrufe = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal abrufe
        # Der fremde Katalog (`faehigkeiten_aus`) zaehlt nicht mit: er gehoert
        # zur Ergaenzung der Faehigkeiten und haengt an keinem Schluessel.
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json={"data": []})
        abrufe += 1
        assert request.headers.get("Authorization") == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "gpt-5.6"}]})

    async with _client(handler) as client:
        # Ohne Schluessel: keine Anfrage, kein Fehlervermerk — nur der Bestand,
        # und der ist hier leer.
        assert await ai_model_catalog.modelle(client, "openai") == []
        assert abrufe == 0
        # Der Schluessel-Weg direkt danach ist nicht vergiftet: er ruft ab,
        # statt eine gespeicherte Absage auszuliefern.
        modelle = await ai_model_catalog.modelle(
            client, "openai", schluessel="sk-test"
        )
        assert [m.model_id for m in modelle] == ["gpt-5.6"]
        assert abrufe == 1
        # Und der schluessellose Aufrufer bekommt danach den Bestand — nicht
        # nichts, und ohne einen weiteren Abruf anzustossen.
        bestand = await ai_model_catalog.modelle(client, "openai")
        assert [m.model_id for m in bestand] == ["gpt-5.6"]
        assert abrufe == 1


@pytest.mark.asyncio
async def test_an_unknown_model_is_none_rather_than_a_guess() -> None:
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        assert await ai_model_catalog.finde(client, "openrouter", "gibt/es-nicht") is None
        assert await ai_model_catalog.finde(client, "openrouter", "") is None


@pytest.mark.asyncio
async def test_an_unknown_provider_kind_fails_loudly() -> None:
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        with pytest.raises(KeyError):
            await ai_model_catalog.modelle(client, "anbieter-von-morgen")


# ── Niemand wartet auf diesen Abruf ──────────────────────────────────────
#
# Lief die Frist ab, wartete der Absender einer Chatnachricht auf einen
# HTTP-Abruf zu einem fremden Dienst, obwohl ein brauchbarer Stand danebenlag.
# Gemessen ist dieser Abruf schnell (0,08-0,17 s am 2026-08-13) — die Tests hier
# messen deshalb bewusst **keine Sekunden am echten Anbieter**, sondern
# verhalten sich zu einem Anbieter, der haengt. Denn genau dann, und nur dann,
# war die alte Bauart teuer: ABRUF_TIMEOUT betraegt 30 Sekunden, und das
# Schloss lag frueher ueber allen Anbietern zusammen.
#
# Die Tests zaehlen also zweierlei: Versuche am Anbieter wie oben, und **wer
# wartet**. Das Zweite ist der eigentliche Punkt.


async def _auffrischung_abwarten(kind: str = "openrouter") -> None:
    """Auf die laufende Hintergrundauffrischung warten, falls es eine gibt.

    Der Verweis wird geholt, *bevor* gewartet wird: die Aufgabe raeumt sich beim
    Beenden selbst aus dem Woerterbuch, ein spaeterer Zugriff liefe ins Leere.
    """
    aufgabe = ai_model_catalog._auffrischungen.get(kind)
    if aufgabe is not None:
        await asyncio.gather(aufgabe, return_exceptions=True)


@pytest.mark.asyncio
async def test_an_expired_catalog_goes_out_before_the_provider_answers() -> None:
    """Der abgelaufene Stand ist die Antwort, nicht der Anlass zum Warten.

    Die Frist trennt "frisch" von "nicht mehr frisch" — nicht "brauchbar" von
    "unbrauchbar". Ein Kontextfenster schrumpft ueber Nacht nicht, und die
    Denkstufen eines Modells aendern sich auch nicht. Wer nach Ablauf der Frist
    auf den Abruf wartet, tauscht eine Minute Stille gegen eine Genauigkeit,
    die niemand braucht.
    """
    weiter = asyncio.Event()
    versuche = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        if versuche > 1:
            # Der Anbieter antwortet ab jetzt nicht mehr von selbst. Genau so
            # sah der Vorfall aus: der Abruf lief, nur eben lange.
            await weiter.wait()
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        ai_model_catalog.laufzeit_setzen(client)
        erst = await ai_model_catalog.modelle(client, "openrouter")
        assert versuche == 1

        ai_model_catalog._cache["openrouter"].geholt_am -= ai_model_catalog.CACHE_TTL

        # Der Anbieter haengt — und der Aufrufer bekommt trotzdem sofort etwas.
        # Die Schranke ist grosszuegig: sie soll "sofort" von "eine Minute"
        # unterscheiden, nicht Millisekunden messen.
        gestartet = perf_counter()
        wieder = await asyncio.wait_for(
            ai_model_catalog.modelle(client, "openrouter"), 1.0
        )
        assert perf_counter() - gestartet < 0.5
        assert [m.model_id for m in wieder] == [m.model_id for m in erst]

        # Und die Auffrischung laeuft wirklich, sie wurde nicht nur behauptet.
        aufgabe = ai_model_catalog._auffrischungen["openrouter"]
        weiter.set()
        await aufgabe
        assert versuche == 2


@pytest.mark.asyncio
async def test_many_waiting_calls_share_a_single_refresh() -> None:
    """Fuenf gleichzeitige Nachrichten fragen den Anbieter nicht fuenfmal.

    Ohne diese Zusage waere der Umbau ein Rueckschritt: vorher stauten sich die
    Aufrufe am Schloss, hinterher schickte jeder seinen eigenen Abruf los.
    """
    versuche = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        ai_model_catalog.laufzeit_setzen(client)
        await ai_model_catalog.modelle(client, "openrouter")
        ai_model_catalog._cache["openrouter"].geholt_am -= ai_model_catalog.CACHE_TTL

        ergebnisse = await asyncio.gather(
            *(ai_model_catalog.modelle(client, "openrouter") for _ in range(5))
        )
        await _auffrischung_abwarten()

    assert all(len(treffer) == 4 for treffer in ergebnisse)
    assert versuche == 2


@pytest.mark.asyncio
async def test_the_very_first_call_gives_up_instead_of_holding_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne jeden Stand wird kurz gewartet — nicht die volle ``ABRUF_TIMEOUT``.

    Das ist der einzige Fall, in dem ueberhaupt noch jemand wartet, und er ist
    nach dem Vorwaermen selten. Wenn er eintritt, gilt: eine leere Liste ist
    verkraftbar (``ai_context_window`` meldet "Fenster unbekannt",
    ``ai_reasoning`` bleibt beim reinen An/Aus), 30 Sekunden Stille nicht.

    Der Abruf laeuft dabei **weiter**. Ihn abzubrechen hiesse, die Wartezeit zu
    bezahlen und das Ergebnis wegzuwerfen.
    """
    monkeypatch.setattr(ai_model_catalog, "ERSTE_WARTE", 0.05)
    weiter = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await weiter.wait()
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        ai_model_catalog.laufzeit_setzen(client)
        gestartet = perf_counter()
        assert await ai_model_catalog.modelle(client, "openrouter") == []
        assert perf_counter() - gestartet < 1.0

        aufgabe = ai_model_catalog._auffrischungen["openrouter"]
        assert not aufgabe.done()
        weiter.set()
        await aufgabe
        # Der naechste Aufrufer erbt, wofuer der erste gewartet hat.
        assert len(await ai_model_catalog.modelle(client, "openrouter")) == 4


@pytest.mark.asyncio
async def test_warming_up_means_the_first_message_finds_a_full_catalog() -> None:
    """Der Start holt den Katalog, damit ihn der erste Chat nicht holen muss."""
    versuche = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        ai_model_catalog.laufzeit_setzen(client)
        ai_model_catalog.vorwaermen_anstossen()
        await _auffrischung_abwarten()
        assert versuche == 1

        # Und jetzt der Sendepfad: kein einziger weiterer Versuch.
        assert len(await ai_model_catalog.modelle(client, "openrouter")) == 4
        assert versuche == 1


@pytest.mark.asyncio
async def test_without_a_background_client_nothing_changes() -> None:
    """Ohne hinterlegten Client bleibt es beim alten, wartenden Weg.

    Wichtig, weil MSM sonst in jedem Skript und in jedem Test ohne Anwendung
    ewig denselben veralteten Stand ausliefern wuerde, ohne ihn je zu erneuern.
    Ein Abruf, der niemandem gehoert, waere schlimmer als ein Aufrufer, der
    wartet.
    """
    versuche = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        # Kein laufzeit_setzen: ``_HTTP`` bleibt None.
        await ai_model_catalog.modelle(client, "openrouter")
        ai_model_catalog._cache["openrouter"].geholt_am -= ai_model_catalog.CACHE_TTL
        await ai_model_catalog.modelle(client, "openrouter")

    assert versuche == 2
    assert not ai_model_catalog._auffrischungen


@pytest.mark.asyncio
async def test_shutdown_waits_for_the_refresh_it_cancels() -> None:
    """Beim Herunterfahren endet die Auffrischung, bevor der Client zugeht.

    Sie haelt denselben Client wie der Sendepfad. Wird der unter ihr weggezogen,
    endet sie in einem Fehler auf einem geschlossenen Client — das haelt nichts
    auf, hinterlaesst aber eine Meldung beim Herunterfahren, die aussieht, als
    sei etwas kaputt. ``cancel()`` allein genuegt dafuer nicht: es bittet nur
    darum. Erst das Abwarten macht die Zusage wahr.
    """
    weiter = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await weiter.wait()
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        ai_model_catalog.laufzeit_setzen(client)
        ai_model_catalog.vorwaermen_anstossen()
        aufgabe = ai_model_catalog._auffrischungen["openrouter"]
        await asyncio.sleep(0)
        assert not aufgabe.done()

        await ai_model_catalog.aufraeumen()

        assert aufgabe.done()
        assert not ai_model_catalog._auffrischungen
        # Und danach stoesst nichts mehr etwas an: ohne Client kein Hintergrund.
        assert ai_model_catalog._auffrischen_anstossen("openrouter") is False


# ── Der Schlüssel für einen schlüsselpflichtigen Katalog ─────────────────
#
# OpenAI und ElevenLabs geben ihre Listen nur gegen einen Schlüssel heraus. Der
# steht in der Datenbank und ist mit dem DIS-Sidecar verschlüsselt — zur Hand
# hat ihn nur die Provider-Einstellungsseite. Alle übrigen Leser des Katalogs
# (`ai_reasoning.vorgabe`, `ai_context_window.ermitteln`, die Providerliste im
# Chat) haben ihn nicht.
#
# Bis zum 17.08.2026 hiess das: ohne Einstellungsseite kein Katalog. Der Abruf
# lief trotzdem los, kassierte ein 401, und der Vermerk darüber hielt die
# nächste Minute frei von weiteren Versuchen — ein Fehlschlag, der sich selbst
# am Leben hielt. Praktisch war der OpenAI-Katalog nach jedem Neustart leer.


@pytest.mark.asyncio
async def test_without_a_key_a_key_bound_catalog_is_not_even_asked() -> None:
    """Kein Schlüssel heisst **kein Abruf** — und nicht „Abruf, der scheitert".

    Das ist die eigentliche Zusage hinter dem Vorwärmen aller Anbieter beim
    Start. Ein 401 an dieser Stelle wäre kein Ausfall des Anbieters, sondern
    eine Frage, die MSM gar nicht hätte stellen dürfen; ihn danach als Ausfall
    zu vermerken machte aus dem Missverständnis eine Wartezeit.
    """
    versuche = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal versuche
        versuche += 1
        return httpx.Response(401, json={})

    async with _client(handler) as client:
        assert await ai_model_catalog.modelle(client, "openai") == []
    assert versuche == 0


@pytest.mark.asyncio
async def test_the_catalog_gets_its_own_key_when_the_caller_has_none() -> None:
    """Der Abruf fragt selbst nach dem Schlüssel, statt auf einen Aufrufer zu warten.

    Der eingehängte Weg dorthin (`schluesselquelle_setzen`) ist eine Funktion
    und kein Import: der Katalog soll von Datenbank und DIS-Sidecar nichts
    wissen. Gefragt wird **nur**, wenn wirklich abgerufen wird — sonst kostete
    jede Chatnachricht eine Entschlüsselung für eine Angabe, die sechs Stunden
    gilt.
    """
    gefragt: list[str] = []
    gesehen: dict[str, str | None] = {}

    def quelle(kind: str) -> str | None:
        gefragt.append(kind)
        return "sk-aus-der-datenbank"

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen[request.url.host] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}]})

    ai_model_catalog.schluesselquelle_setzen(quelle)
    async with _client(handler) as client:
        modelle = await ai_model_catalog.modelle(client, "openai")

    assert [m.model_id for m in modelle] == ["gpt-5.5"]
    assert gefragt == ["openai"]
    assert gesehen["api.openai.com"] == "Bearer sk-aus-der-datenbank"
    # Und nur dorthin. OpenRouter gibt seine Liste offen heraus; ein
    # OpenAI-Schluessel an fremder Adresse waere ein Geheimnis auf Reisen.
    assert gesehen.get("openrouter.ai") is None


@pytest.mark.asyncio
async def test_the_quiet_period_after_a_missing_key_does_not_bind_who_brings_one() -> None:
    """Der Vermerk „kein Schlüssel" sperrt den, der einen hat, ausdrücklich nicht.

    Die Ruhefrist gilt Ausfällen des Anbieters — die kann niemand herbeireden,
    und ein zweiter Versuch im selben Atemzug kostet dieselbe Wartezeit noch
    einmal. „Kein Schlüssel zu beschaffen" ist kein solcher Ausfall: wer selbst
    einen mitbringt, stellt nicht denselben Versuch noch einmal, sondern einen
    anderen.

    Ohne diese Unterscheidung sah die Einstellungsseite eine Minute lang „dieser
    Anbieter kennt keine Modelle" — mit dem Schlüssel im Feld daneben. Genau die
    Seite, die den Fehlschlag hätte beheben können, bekam ihn vorgehalten.
    """
    abrufe: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Der fremde Katalog wird hier nicht mitgezaehlt: er gehoert zur
        # Ergaenzung der Faehigkeiten und haengt an keinem Schluessel.
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json={"data": []})
        abrufe.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}]})

    async with _client(handler) as client:
        # Erster Aufruf: keine Quelle eingehängt, also kein Schlüssel und kein
        # Abruf — und ein Vermerk, der die nächste Minute beansprucht.
        assert await ai_model_catalog.modelle(client, "openai") == []
        assert abrufe == []
        vermerk = ai_model_catalog._cache["openai"]
        assert vermerk.fehler_am is not None and vermerk.schluessel_fehlte is True

        # Zweiter Aufruf, unmittelbar danach, mit Schlüssel: geht durch.
        modelle = await ai_model_catalog.modelle(
            client, "openai", schluessel="sk-von-der-einstellungsseite"
        )

    assert [m.model_id for m in modelle] == ["gpt-5.5"]
    assert abrufe == ["Bearer sk-von-der-einstellungsseite"]


@pytest.mark.asyncio
async def test_a_real_outage_still_holds_the_quiet_period_even_with_a_key() -> None:
    """Die Gegenprobe — sonst wäre die Ausnahme ein Loch in der Ruhefrist.

    Ein 500 des Anbieters bleibt ein 500, gleich wer als Nächster fragt. Nur der
    Grund „kein Schlüssel" ist in der Hand des Fragenden; ein Ausfall ist es
    nicht, und ihn erneut zu versuchen kostet nur dieselbe Wartezeit noch einmal.
    """
    versuche: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        versuche.append((request.headers.get("authorization") or "").removeprefix("Bearer "))
        return httpx.Response(500, json={})

    ai_model_catalog.schluesselquelle_setzen(lambda kind: "sk-konto-a")
    async with _client(handler) as client:
        assert await ai_model_catalog.modelle(client, "openai") == []
        assert versuche == ["sk-konto-a"]
        assert ai_model_catalog._cache["openai"].schluessel_fehlte is False

        # Derselbe Zugang, unmittelbar danach: er wartet.
        assert await ai_model_catalog.modelle(
            client, "openai", schluessel="sk-konto-a"
        ) == []
        assert versuche == ["sk-konto-a"], (
            "Ein Ausfall des Anbieters ruht auch vor einem Schlüssel"
        )

        # Ein **anderer** Zugang nicht: ein abgelehnter Schlüssel ist die Sache
        # seines Kontos, und ein Konto sperrt das andere nicht aus.
        assert await ai_model_catalog.modelle(
            client, "openai", schluessel="sk-konto-b"
        ) == []

    assert versuche == ["sk-konto-a", "sk-konto-b"]


@pytest.mark.asyncio
async def test_a_key_bound_list_is_never_handed_to_another_account() -> None:
    """Zwei Zugänge desselben Anbieters teilen sich keinen Stand.

    OpenAIs ``/v1/models`` antwortet nicht mit „was es gibt", sondern mit „was
    **dieses Konto** sehen darf" — samt seiner Feinabstimmungen, deren Kennungen
    der Betreiber selbst vergeben hat und die den Firmennamen tragen. Der
    Speicher liegt trotzdem unter dem Anbieter und nicht unter dem Schlüssel;
    getrennt wird durch einen Abdruck **im** Eintrag. Fehlt er, sieht Konto B
    die Modellauswahl von Konto A, und das ist ein Datenleck in einem
    Auswahlfeld.
    """
    listen = {
        "sk-konto-a": {"data": [{"id": "ft:muster-intern-v3"}]},
        "sk-konto-b": {"data": [{"id": "gpt-5.5"}]},
    }
    abrufe: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json={"data": []})
        kopf = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        abrufe.append(kopf)
        return httpx.Response(200, json=listen[kopf])

    async with _client(handler) as client:
        a = await ai_model_catalog.modelle(client, "openai", schluessel="sk-konto-a")
        b = await ai_model_catalog.modelle(client, "openai", schluessel="sk-konto-b")
        # Und A bekommt beim nächsten Mal wieder seine eigene.
        a_nochmal = await ai_model_catalog.modelle(
            client, "openai", schluessel="sk-konto-a"
        )

    assert [m.model_id for m in a] == ["ft:muster-intern-v3"]
    assert [m.model_id for m in b] == ["gpt-5.5"]
    assert [m.model_id for m in a_nochmal] == ["ft:muster-intern-v3"]
    assert abrufe == ["sk-konto-a", "sk-konto-b", "sk-konto-a"]


@pytest.mark.asyncio
async def test_a_failed_lookup_does_not_hand_out_the_other_accounts_list() -> None:
    """Auch der Fehlerweg trennt die Konten — er ist der leisere von beiden.

    „Der alte Stand überlebt den Fehlversuch" ist eine gute Regel, solange es
    einen alten Stand gibt. Bei einem kontogebundenen Katalog liegt dort aber
    der Stand, den zuletzt **irgendwer** geholt hat. Reicht der Fehlerweg ihn
    weiter, bekommt Konto B die Modelle von Konto A — und zwar an genau der
    Stelle, an der niemand hinsieht, weil sie nach „Anbieter gerade nicht
    erreichbar" aussieht.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json={"data": []})
        schluessel = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        if schluessel == "sk-konto-a":
            return httpx.Response(200, json={"data": [{"id": "ft:muster-intern-v3"}]})
        return httpx.Response(500, json={})

    async with _client(handler) as client:
        a = await ai_model_catalog.modelle(client, "openai", schluessel="sk-konto-a")
        b = await ai_model_catalog.modelle(client, "openai", schluessel="sk-konto-b")

    assert [m.model_id for m in a] == ["ft:muster-intern-v3"]
    assert b == [], "Ein Fehlschlag darf nicht die Liste des anderen Kontos zurückgeben"


@pytest.mark.asyncio
async def test_an_open_catalog_is_shared_by_everyone_who_asks() -> None:
    """Die Gegenprobe: OpenRouters Liste ist für jeden dieselbe.

    Ohne diesen Test wäre die Trennung oben auch dann erfüllt, wenn jeder
    Zwischenspeicher an jedem Schlüssel hinge — und dann kostete jede Frage
    einen Abruf. Getrennt wird nur, wo die Antwort wirklich vom Fragenden
    abhängt (`Anbieter.katalog_braucht_schluessel`).
    """
    abrufe = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal abrufe
        abrufe += 1
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openrouter", schluessel="sk-eins")
        await ai_model_catalog.modelle(client, "openrouter", schluessel="sk-zwei")
        await ai_model_catalog.modelle(client, "openrouter")

    assert abrufe == 1


@pytest.mark.asyncio
async def test_a_key_from_the_caller_spares_the_lookup() -> None:
    """Wer den Schlüssel schon hat, reicht ihn herein — die Einstellungsseite tut das.

    Ein zweiter Gang zum DIS-Sidecar für dasselbe Geheimnis wäre reine
    Wartezeit; dessen Frist beträgt 15 Sekunden.
    """
    gefragt: list[str] = []
    ai_model_catalog.schluesselquelle_setzen(lambda kind: gefragt.append(kind) or "x")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-5.5"}]})

    async with _client(handler) as client:
        await ai_model_catalog.modelle(client, "openai", schluessel="sk-vom-aufrufer")

    assert gefragt == []


# ── Fähigkeiten aus einem fremden Katalog ────────────────────────────────
#
# OpenAIs ``/v1/models`` nennt je Modell ``id``, ``object``, ``created``,
# ``owned_by`` und ``shutdown_date`` — kein Kontextfenster, keine Denkstufen.
# Eine Tabelle im Programm wäre der Rückfall in genau das, wogegen es diesen
# Katalog gibt. OpenRouter beschreibt dieselben Modelle unter ``openai/…`` und
# mit demselben Wortschatz für die Stufen.

#: Ein OpenRouter-Ausschnitt mit zwei OpenAI-Modellen. ``gpt-5.1-codex-mini``
#: steht hier nicht zur Zierde: es ist abschaltbar und führt trotzdem kein
#: ``none`` in seinen Stufen — der Beleg dafür, dass „aus" eine Angabe ist und
#: keine Folgerung aus ``mandatory``.
FREMD = {
    "data": [
        {
            "id": "openai/gpt-5.5",
            "name": "OpenAI: GPT-5.5",
            "context_length": 1_050_000,
            "top_provider": {"context_length": 1_050_000, "max_completion_tokens": 128_000},
            "reasoning": {
                "mandatory": False,
                "supported_efforts": ["none", "low", "medium", "high"],
                "default_effort": "medium",
            },
            "pricing": {"input_cache_write": "0.00000125"},
        },
        {
            "id": "openai/gpt-5.1-codex-mini",
            "name": "OpenAI: GPT-5.1 Codex Mini",
            "top_provider": {"context_length": 400_000},
            "reasoning": {
                "mandatory": False,
                "supported_efforts": ["high", "medium", "low"],
            },
        },
    ]
}

EIGEN = {"data": [{"id": "gpt-5.5"}, {"id": "gpt-5.1-codex-mini"}, {"id": "whisper-1"}]}


def _zwei_kataloge(fremd=None):
    """Ein Transport, der OpenAI und OpenRouter auseinanderhält."""

    def verteile(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            return fremd(request) if fremd else httpx.Response(200, json=FREMD)
        return httpx.Response(200, json=EIGEN)

    return _client(verteile)


@pytest.mark.asyncio
async def test_capabilities_are_filled_in_from_the_foreign_catalog() -> None:
    """Denkstufen und Fenster kommen aus einem Katalog — nur nicht aus dem eigenen."""
    ai_model_catalog.schluesselquelle_setzen(lambda kind: "sk-test")
    async with _zwei_kataloge() as client:
        modelle = await ai_model_catalog.modelle(client, "openai")

    nach_id = {m.model_id: m for m in modelle}
    gpt = nach_id["gpt-5.5"]
    assert gpt.denkt is True
    assert gpt.stufen == ("none", "low", "medium", "high")
    assert gpt.standard_stufe == "medium"
    assert gpt.kontext_tokens == 1_050_000
    assert gpt.max_ausgabe_tokens == 128_000

    # Die Kennung bleibt die eigene: an einem OpenAI-Zugang heisst das Modell
    # ``gpt-5.5`` und nicht ``openai/gpt-5.5``. Der Name auch — ``OpenAI:
    # GPT-5.5`` ist die Beschriftung eines Vermittlers, den der Betreiber
    # gerade nicht benutzt.
    assert gpt.name == "gpt-5.5"

    # Was der fremde Katalog nicht führt, bleibt unbekannt. Auch das ist eine
    # Zusage: ergänzt wird, nicht geraten.
    assert nach_id["whisper-1"].denkt is False
    assert nach_id["whisper-1"].kontext_tokens is None


@pytest.mark.asyncio
async def test_the_billing_marker_of_a_middleman_does_not_travel() -> None:
    """``cache_marke_noetig`` bleibt zurück — es ist keine Eigenschaft des Modells.

    OpenRouter setzt es, wenn sein Katalog ``input_cache_write`` führt, also
    wenn **dort** ein Preis fürs Zwischenspeichern anfällt. Die Marke, die
    daraufhin mitginge, heisst ``cache_control`` und ist eine
    OpenRouter-Erweiterung; OpenAI antwortet darauf mit einem 400 und lehnt
    damit die ganze Anfrage ab.
    """
    ai_model_catalog.schluesselquelle_setzen(lambda kind: "sk-test")
    async with _zwei_kataloge() as client:
        durch_openrouter = await ai_model_catalog.modelle(client, "openrouter")
        eigen = await ai_model_catalog.finde(client, "openai", "gpt-5.5")

    # Beim Vermittler steht die Marke — dort ist sie richtig.
    fremd = {m.model_id: m for m in durch_openrouter}["openai/gpt-5.5"]
    assert fremd.cache_marke_noetig is True
    assert eigen is not None and eigen.cache_marke_noetig is False


@pytest.mark.asyncio
async def test_a_missing_foreign_catalog_costs_knowledge_not_the_provider() -> None:
    """Die Ergänzung ist eine Ergänzung und keine Bedingung.

    Ein Anbieter, den MSM direkt anspricht, darf nicht daran hängen, dass ein
    anderer erreichbar ist. Fällt der fremde Katalog aus, bleibt es beim
    eigenen Wissen — also bei „unbekannt", und unbekannt heisst nie „klein".
    """
    ai_model_catalog.schluesselquelle_setzen(lambda kind: "sk-test")
    async with _zwei_kataloge(lambda _r: httpx.Response(503, json={})) as client:
        modelle = await ai_model_catalog.modelle(client, "openai")

    assert [m.model_id for m in modelle] == [
        "gpt-5.1-codex-mini",
        "gpt-5.5",
        "whisper-1",
    ]
    assert all(m.kontext_tokens is None and not m.denkt for m in modelle)


@pytest.mark.asyncio
async def test_the_foreign_lookup_cannot_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zwei Anbieter, die aufeinander zeigen, drehen sich nicht im Kreis.

    Der fremde Katalog wird über `_besorgen` geholt und nicht über `modelle` —
    `modelle` ist genau `_besorgen` plus die Ergänzung, ein Aufruf von dort
    wäre also der Kreis. So gibt es genau einen Sprung und keinen zweiten, ohne
    Zähler und ohne Merkliste.

    Gebaut wird der Kreis hier absichtlich: im Programm gibt es ihn nicht, und
    genau deshalb würde ihn sonst nichts bemerken, bis ihn jemand einträgt.

    **Gezählt wird, nicht abgewartet.** Der Test stand hier schon einmal, und er
    blieb grün, wenn man `_besorgen` durch `modelle` ersetzte — also genau bei
    dem Fehler, den er verhindern soll. Der Grund ist das ``except Exception``
    in `_mit_faehigkeiten`: die Rekursion läuft in die Tiefenbegrenzung, der
    `RecursionError` ist eine Ausnahme wie jede andere, wird dort gefangen und
    zu „der fremde Katalog fiel aus" — und der eigene Katalog kam trotzdem
    zurück. Nur eben ohne Ergänzung, in Sekundenbruchteilen, unter der
    Zeitgrenze. Deshalb prüft dieser Test jetzt zwei Dinge, die ein Kreis
    beide bricht: **wie oft** die Ergänzung betreten wird, und **ob** sie
    tatsächlich etwas eingetragen hat.
    """
    from dataclasses import replace as _ersetzen

    from services import ai_provider_registry

    monkeypatch.setitem(
        ai_provider_registry.ANBIETER,
        "openrouter",
        _ersetzen(
            ai_provider_registry.anbieter("openrouter"),
            faehigkeiten_aus="openai",
            faehigkeiten_praefix="",
        ),
    )
    ai_model_catalog.schluesselquelle_setzen(lambda kind: "sk-test")

    betreten: list[str] = []
    echt = ai_model_catalog._mit_faehigkeiten

    async def zaehlend(client, spec, eigene):
        betreten.append(spec.kind)
        return await echt(client, spec, eigene)

    monkeypatch.setattr(ai_model_catalog, "_mit_faehigkeiten", zaehlend)

    async with _zwei_kataloge() as client:
        durch = await asyncio.wait_for(
            ai_model_catalog.modelle(client, "openai"), 5.0
        )

    assert {m.model_id for m in durch} >= {"gpt-5.5", "whisper-1"}

    # Ein Sprung, kein zweiter: `_besorgen` holt die fremde Liste und kommt
    # zurück, ohne selbst wieder zu ergänzen.
    assert betreten == ["openai"], (
        f"Die Ergänzung wurde {len(betreten)}× betreten ({betreten}) — über "
        "`modelle` statt `_besorgen` zeigen zwei Anbieter aufeinander und "
        "drehen sich, bis die Tiefenbegrenzung greift."
    )

    # Und sie hat wirklich ergänzt. Ohne diese Zeile bliebe der Test grün,
    # solange der Kreis nur schnell genug in eine gefangene Ausnahme läuft.
    nach_id = {m.model_id: m for m in durch}
    assert nach_id["gpt-5.5"].denkt is True
    assert nach_id["gpt-5.5"].kontext_tokens == 1_050_000


@pytest.mark.asyncio
async def test_a_hanging_provider_does_not_stall_a_healthy_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anbieter warten nicht mehr aufeinander.

    Vorher hielten sich alle Anbieter **ein** Schloss. Ein haengender staute
    damit die Abrufe der uebrigen mit, obwohl sie nichts miteinander zu tun
    haben. Der Test baut den zweiten Anbieter, den es heute noch nicht gibt —
    laut ``ai_provider_registry`` ist das eine Datei mit einem Eintrag und einem
    Leser; hier steht beides zusammen, weil eine Datei fuer einen Test zu viel
    waere.
    """
    from services import ai_provider_registry
    from services.ai_provider_registry import Anbieter, openrouter

    zweiter = Anbieter(
        kind="zweiter",
        label="Zweiter",
        base_url="https://zweiter.example",
        catalog_url="https://zweiter.example/models",
        key_url="https://zweiter.example/keys",
    )
    echt = ai_model_catalog.anbieter
    monkeypatch.setitem(ai_provider_registry._LESER, "zweiter", openrouter.katalog_lesen)
    monkeypatch.setattr(
        ai_model_catalog,
        "anbieter",
        lambda kind: zweiter if kind == "zweiter" else echt(kind),
    )

    weiter = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if "zweiter.example" not in str(request.url):
            await weiter.wait()
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        haengt = asyncio.ensure_future(ai_model_catalog.modelle(client, "openrouter"))
        await asyncio.sleep(0)

        # Der gesunde Anbieter antwortet, waehrend der andere haengt.
        gesund = await asyncio.wait_for(
            ai_model_catalog.modelle(client, "zweiter"), 1.0
        )
        assert len(gesund) == 4

        weiter.set()
        assert len(await haengt) == 4


# ── Anbieter ohne eigenen Katalog (Azure) ─────────────────────────────


@pytest.mark.asyncio
async def test_a_provider_without_a_catalog_never_asks_anyone() -> None:
    """Kein Katalog ist kein Fehlschlag, sondern eine Tatsache.

    Bei Azure heisst ein Modell so, wie der Betreiber sein Deployment genannt
    hat, und eine Liste dafuer gibt es nicht. Ein Abrufversuch endete in einem
    Fehler, der als Stoerung vermerkt wuerde — samt Ruhefrist und einer
    Oberflaeche, die einen Ausfall meldet, den es nicht gibt.
    """
    gefragt: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        gefragt.append(str(request.url))
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        assert await ai_model_catalog.modelle(client, "azure_openai") == []
        assert await ai_model_catalog.modelle(client, "azure_anthropic") == []

    assert gefragt == []


@pytest.mark.asyncio
async def test_a_deployment_named_after_its_model_inherits_the_capabilities() -> None:
    """**Die Zusage dieses Nachschlags.**

    Ohne ihn bliebe an einem Azure-Zugang jedes Modell unbekannt, und der Chat
    rechnete mit `ai_context_window.RUECKFALL_NUTZBAR_TOKENS` — 6.000 Token an
    einem Modell mit 200.000. Getroffen wird die Konvention, die Microsoft in
    seinen eigenen Beispielen verwendet: das Deployment heisst wie das Modell.
    """
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        modell = await ai_model_catalog.finde(
            client, "azure_anthropic", "claude-opus-5"
        )

    assert modell is not None
    # Die **eigene** Kennung bleibt: unter ihr spricht MSM das Deployment an.
    assert modell.model_id == "claude-opus-5"
    assert modell.kontext_tokens == 200_000
    assert modell.stufen == ("max", "xhigh", "high", "medium", "low")
    assert modell.denkt is True


@pytest.mark.asyncio
async def test_a_freely_named_deployment_stays_unknown_instead_of_guessed() -> None:
    """„Unbekannt" heisst hier wie ueberall nie „klein" und nie „kann er nicht"."""
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        assert await ai_model_catalog.finde(
            client, "azure_anthropic", "prod-chat"
        ) is None


@pytest.mark.asyncio
async def test_a_provider_with_its_own_catalog_never_borrows_an_entry() -> None:
    """Sonst saehe ein Tippfehler aus wie ein gueltiges Modell.

    OpenAI borgt sich seine **Faehigkeiten** von OpenRouter, aber nie die
    **Existenz** eines Modells: was sein eigener Katalog nicht fuehrt, gibt es
    fuer diesen Zugang nicht (`ai_provider_registry.openai`, „erfindet nichts").
    """
    async def handler(request: httpx.Request) -> httpx.Response:
        if "api.openai.com" in str(request.url):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(200, json=ANTWORT)

    async with _client(handler) as client:
        # Steht in OpenRouters Katalog als ``openai/gpt-4o-mini``, aber nicht in
        # OpenAIs eigener Liste unter diesem Namen.
        assert await ai_model_catalog.finde(
            client, "openai", "gpt-4o-vertippt", schluessel="sk-test"
        ) is None
        # Und der Fall, den es wirklich gibt, funktioniert weiterhin.
        eigenes = await ai_model_catalog.finde(
            client, "openai", "gpt-4o-mini", schluessel="sk-test"
        )
        assert eigenes is not None


@pytest.mark.asyncio
async def test_a_failing_foreign_catalog_leaves_the_answer_unknown() -> None:
    """Der fremde Katalog ist eine Ergaenzung und keine Bedingung."""
    async with _client(lambda _r: httpx.Response(503)) as client:
        assert await ai_model_catalog.finde(
            client, "azure_anthropic", "claude-opus-5"
        ) is None


#: Dass das Vorwaermen kataloglose Anbieter ueberspringt, steht **nicht** hier,
#: sondern in `test_ai_voice_provider.test_prewarming_covers_key_bound_catalogs_too`
#: — dort, wo die Zusage ueber das Vorwaermen ohnehin gefuehrt wird. Zwei Tests
#: ueber dieselbe Zeile waeren zwei Wahrheiten, und die zweite liefe beim
#: naechsten Umbau der ersten hinterher.


@pytest.mark.asyncio
async def test_the_form_gets_reasoning_levels_where_there_is_no_catalog() -> None:
    """Die Luecke, die den Worker eines Azure-Zugangs stumpf liess.

    Das Formular las die Denkstufen aus der **Katalogliste**, und die ist bei
    Azure leer. Der Chat wusste es laengst besser — er fragt `finde` —, also
    hatte dasselbe Modell je nach Bildschirm Stufen oder keine. Ein Anbieter,
    an dem sich die Stufe des Hintergrund-Workers nicht einstellen laesst, ist
    genau so weit unbrauchbar wie einer ohne Nachdenken.

    Der Endpunkt geht denselben Weg wie der Chat. Kennt der geliehene Katalog
    die Kennung nicht, kommt `None` — keine erfundene Auswahl.
    """
    from routers.ai_providers import find_catalog_model

    class _Anfrage:
        def __init__(self, client: httpx.AsyncClient) -> None:
            self.app = type("A", (), {"state": type("S", (), {"ai_http_client": client})})

    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        antwort = await find_catalog_model(
            kind="azure_anthropic", name="  claude-opus-5  ",
            request=_Anfrage(client), _=None,  # type: ignore[arg-type]
        )
        assert antwort is not None
        assert antwort.model_id == "claude-opus-5"
        assert antwort.reasoning is True
        # Genau die Stufen des geliehenen Eintrags, in der Rangfolge von
        # `ai_reasoning` — nie eine Liste aus dem Code.
        assert antwort.efforts == ["low", "medium", "high", "xhigh", "max"]

        # Ein frei benanntes Deployment bleibt unbekannt, statt geraten zu werden.
        assert await find_catalog_model(
            kind="azure_anthropic", name="prod-chat",
            request=_Anfrage(client), _=None,  # type: ignore[arg-type]
        ) is None
        # Und eine leere Kennung fragt gar nicht erst.
        assert await find_catalog_model(
            kind="azure_anthropic", name="   ",
            request=_Anfrage(client), _=None,  # type: ignore[arg-type]
        ) is None
