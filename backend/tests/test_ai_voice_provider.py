"""Zwei Anbieter, zwei Protokolle — und keine Verwechslung dazwischen.

Bis hierher gab es genau einen Anbieter, und deshalb gab es die Frage nicht:
*welche API spricht dieser Zugang eigentlich?* Mit dem Sprachmodus gibt es sie.
OpenRouter beantwortet Anfragen unter `/chat/completions`, OpenAI beantwortet
den Sprachweg unter `/realtime` — und zwar mit Ereignissen statt mit Nachrichten.
Die beiden sind nicht ineinander überführbar.

Der Anlass ist nachprüfbar und keine Vorsichtsmaßnahme: OpenRouter hat **keine**
Realtime-API. Am 2026-08-15 nachgesehen — `POST /api/v1/realtime` antwortet mit
404, während `/chat/completions` mit 401 antwortet, und die vollständige
OpenAPI-Spezifikation kennt weder `websocket` noch `webrtc` oder `realtime`.
Ein Sprachzugang muss deshalb zu einem zweiten Anbieter gehen, und beide Wege
müssen den jeweils falschen Zugang **vorher** abweisen statt ihn an eine Adresse
zu schicken, an der sein Modell nicht antwortet.

Was hier zugesichert wird:

* Ein Realtime-Zugang kommt nicht in den Chat — weder über die Auswahl noch
  über eine geratene Kennung.
* Der Katalogschlüssel geht nur an den Anbieter, der ihn verlangt.
* Das Vorwärmen beim Start fasst schlüsselpflichtige Kataloge nicht an.
"""

from __future__ import annotations

import httpx
import pytest

from models import AiProvider
from services import ai_model_catalog, ai_provider_registry, ai_provider_service


# ── Die Registry ──────────────────────────────────────────────────────────


def test_the_two_providers_speak_different_protocols() -> None:
    assert ai_provider_registry.ANBIETER["openrouter"].protokoll == ai_provider_registry.CHAT
    assert (
        ai_provider_registry.ANBIETER["openai_realtime"].protokoll
        == ai_provider_registry.REALTIME
    )


def test_asking_for_a_protocol_never_raises_on_an_unknown_kind() -> None:
    """`spricht()` filtert, `anbieter()` löst auf — und nur eines darf werfen.

    Der Unterschied ist nicht kosmetisch. `spricht()` läuft über Zeilen aus der
    Datenbank, und darunter kann eine aus einer Zukunftsversion sein (Downgrade,
    Migration 20260811_01). Eine Ausnahme nähme dort die ganze Liste mit, statt
    den einen Eintrag auszulassen.
    """
    assert ai_provider_registry.spricht("openrouter", ai_provider_registry.CHAT)
    assert not ai_provider_registry.spricht("openrouter", ai_provider_registry.REALTIME)
    assert ai_provider_registry.spricht("openai_realtime", ai_provider_registry.REALTIME)
    assert not ai_provider_registry.spricht("openai_realtime", ai_provider_registry.CHAT)
    # Kein KeyError, sondern ein schlichtes Nein.
    assert not ai_provider_registry.spricht("gibtsnicht", ai_provider_registry.CHAT)

    with pytest.raises(KeyError):
        ai_provider_registry.anbieter("gibtsnicht")


def test_a_provider_row_is_checked_through_the_service() -> None:
    """Die Router fragen `ai_provider_service`, nicht `provider_kind` selbst."""
    chat = AiProvider(
        id=1, name="Chat", provider_kind="openrouter",
        default_model="openai/gpt-5.6-luna", enabled=True, requires_api_key=True,
    )
    sprache = AiProvider(
        id=2, name="Sprache", provider_kind="openai_realtime",
        default_model="gpt-realtime-2.1", enabled=True, requires_api_key=True,
    )
    assert ai_provider_service.spricht(chat, ai_provider_registry.CHAT)
    assert not ai_provider_service.spricht(chat, ai_provider_registry.REALTIME)
    assert ai_provider_service.spricht(sprache, ai_provider_registry.REALTIME)
    assert not ai_provider_service.spricht(sprache, ai_provider_registry.CHAT)


# ── Der Katalogleser ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kennung, erwartet",
    [
        ("gpt-realtime-2.1", True),
        ("gpt-realtime-2.1-mini", True),
        ("gpt-realtime", True),
        # Alles andere im Konto des Betreibers gehört nicht in eine
        # Sprachauswahl — und OpenAIs Katalog führt es trotzdem mit auf.
        ("gpt-5.6", False),
        ("gpt-audio", False),
        ("text-embedding-3-small", False),
        ("dall-e-3", False),
        ("whisper-1", False),
    ],
)
def test_only_realtime_models_survive_the_openai_reader(kennung: str, erwartet: bool) -> None:
    gelesen = ai_model_catalog._modell_aus_openai_realtime({"id": kennung})
    assert (gelesen is not None) is erwartet, kennung


def test_the_openai_reader_admits_what_it_does_not_know() -> None:
    """Kein erfundenes Kontextfenster, keine erfundene Denkstufe.

    OpenAIs ``/v1/models`` liefert je Eintrag nur ``id``, ``object``, ``created``
    und ``owned_by``. `None` heißt im übrigen Code „unbekannt" und nie „klein"
    (`ai_context_window.ermitteln`) — genau deshalb darf hier keine Zahl aus
    einer Dokumentation stehen, die morgen eine andere ist.
    """
    modell = ai_model_catalog._modell_aus_openai_realtime({"id": "gpt-realtime-2.1"})
    assert modell is not None
    assert modell.kontext_tokens is None
    assert modell.max_ausgabe_tokens is None
    assert modell.denkt is False
    assert modell.stufen == ()
    assert modell.cache_marke_noetig is False


def test_a_broken_entry_is_skipped_and_not_fatal() -> None:
    assert ai_model_catalog._modell_aus_openai_realtime({}) is None
    assert ai_model_catalog._modell_aus_openai_realtime({"id": None}) is None
    assert ai_model_catalog._modell_aus_openai_realtime({"id": 42}) is None


# ── Der Schlüssel im Katalogabruf ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_key_goes_only_to_the_provider_that_demands_it() -> None:
    """Ein Geheimnis reist nicht an eine Adresse, die es nicht braucht.

    OpenRouter gibt seinen Katalog offen heraus. Den Schlüssel trotzdem
    mitzuschicken wäre kein Fehler mit sichtbarer Folge — und genau deshalb
    steht hier ein Test: so etwas fällt im Betrieb nie auf.
    """
    async def kopf_beim_abruf(kind: str, kennung: str) -> str | None:
        """Welchen ``Authorization``-Kopf trägt der Katalogabruf dieses Anbieters?"""
        gesehen: list[str | None] = []

        def antworte(request: httpx.Request) -> httpx.Response:
            gesehen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"data": [{"id": kennung}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(antworte)) as client:
            await ai_model_catalog._hole(
                client, ai_model_catalog.anbieter(kind), "sk-geheim"
            )
        return gesehen[-1]

    assert await kopf_beim_abruf("openai_realtime", "gpt-realtime-2.1") == "Bearer sk-geheim"
    assert await kopf_beim_abruf("openrouter", "openai/gpt-5.6-luna") is None, (
        "Der Schlüssel ging an OpenRouter, obwohl der Katalog dort offen ist."
    )


# ── Das Vorwärmen beim Start ──────────────────────────────────────────────


def test_prewarming_leaves_key_bound_catalogs_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """`vorwaermen_anstossen()` läuft ohne Datenbank — und damit ohne Schlüssel.

    Ein Versuch ohne ihn endete in einem 401, würde als Fehlversuch vermerkt,
    und `FEHLER_RUHE` verzögerte anschließend den ersten echten Abruf um eine
    Minute. Für einen Fehler, den niemand gemacht hat.
    """
    angestossen: list[str] = []
    monkeypatch.setattr(
        ai_model_catalog,
        "_auffrischen_anstossen",
        lambda kind, schluessel=None: angestossen.append(kind) or True,
    )

    ai_model_catalog.vorwaermen_anstossen()

    assert "openrouter" in angestossen
    assert "openai_realtime" not in angestossen
