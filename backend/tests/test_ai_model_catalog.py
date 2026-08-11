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

import httpx
import pytest

from services import ai_model_catalog


ANTWORT = {
    "data": [
        {
            "id": "anthropic/claude-opus-5",
            "name": "Claude Opus 5",
            "reasoning": {
                "mandatory": False,
                "default_enabled": True,
                "supported_efforts": ["max", "xhigh", "high", "medium", "low"],
                "default_effort": "high",
            },
        },
        {
            # Der häufigste Fall: denkt, kennt aber keine Stufen.
            "id": "qwen/qwen3.7-flash",
            "name": "Qwen3.7 Flash",
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
async def test_an_unknown_model_is_none_rather_than_a_guess() -> None:
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        assert await ai_model_catalog.finde(client, "openrouter", "gibt/es-nicht") is None
        assert await ai_model_catalog.finde(client, "openrouter", "") is None


@pytest.mark.asyncio
async def test_an_unknown_provider_kind_fails_loudly() -> None:
    async with _client(lambda _r: httpx.Response(200, json=ANTWORT)) as client:
        with pytest.raises(KeyError):
            await ai_model_catalog.modelle(client, "anbieter-von-morgen")
