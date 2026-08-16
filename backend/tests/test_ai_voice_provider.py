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

Dazu kommt seit dem Sprachmodus eine zweite Eigenschaft, die nur ein
Sprachzugang hat: **seine Stimme**. Sie ist die einzige Einstellung des Panels,
die der Kunde nicht sieht, sondern hört, und sie gehört deshalb dem Betreiber —
so wie Logo und Farbe. Was hier daran hängt, ist eine Unterscheidung, die man
leicht wegoptimiert: ``NULL`` heisst „nichts hinterlegt" und ausdrücklich nicht
„alloy". Steht der Standard erst einmal in der Spalte, wird er zu einer Auswahl,
die niemand getroffen hat, und ein späterer Wechsel von `STANDARDSTIMME` läuft
bei jedem bestehenden Zugang ins Leere.

Was hier zugesichert wird:

* Ein Realtime-Zugang kommt nicht in den Chat — weder über die Auswahl noch
  über eine geratene Kennung.
* Der Katalogschlüssel geht nur an den Anbieter, der ihn verlangt.
* Das Vorwärmen beim Start fasst schlüsselpflichtige Kataloge nicht an.
* Eine hinterlegte Stimme kommt zurück, wie sie hineinging; eine erfundene
  kommt gar nicht erst hinein; und keine hinterlegte Stimme bleibt keine.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from models import AiProvider
from services import (
    ai_model_catalog,
    ai_provider_registry,
    ai_provider_service,
    ai_voice_session,
)


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


# ── Die Stimme am Zugang ──────────────────────────────────────────────────


def _anlegen(
    client: TestClient, cookies: dict, csrf: str | None, **felder
) -> httpx.Response:
    """Einen Sprachzugang so anlegen, wie der Betreiber es tut: über das Formular.

    Über die Schnittstelle und nicht über den Dienst, weil die eine Hälfte
    dieser Zusagen genau dort entsteht: die 422 für eine erfundene Stimme kommt
    aus `schemas.ai_provider.Stimme`, und ein Dienstaufruf ginge daran vorbei.
    """
    daten: dict = {
        "name": "Sprachzugang",
        "provider_kind": "openai_realtime",
        "default_model": "gpt-realtime-2.1",
    }
    daten.update(felder)
    return client.post(
        "/api/ai/settings/providers",
        json=daten,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def _aendern(
    client: TestClient, cookies: dict, csrf: str | None, zugang_id: int, **felder
) -> httpx.Response:
    return client.patch(
        f"/api/ai/settings/providers/{zugang_id}",
        json=felder,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def test_a_chosen_voice_survives_the_round_trip(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Hin, in die Spalte, und wieder zurück — ohne dass jemand etwas umdeutet."""
    angelegt = _anlegen(client, owner_cookies, csrf_token, default_voice="verse")

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["default_voice"] == "verse"
    assert db.query(AiProvider).one().default_voice == "verse"
    # Und über die Liste, die das Einstellungsformular beim Öffnen liest: sonst
    # stünde die Wahl in der Datenbank und das Feld daneben leer da.
    gelesen = client.get("/api/ai/settings/providers", cookies=owner_cookies).json()
    assert gelesen[0]["default_voice"] == "verse"


@pytest.mark.parametrize("stimme", ai_voice_session.STIMMEN)
def test_every_voice_the_model_can_speak_is_accepted(
    stimme: str, client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Acht Namen, eine Liste — und drei Stellen, die sie lesen.

    `STIMMEN` steht in `ai_voice_session`, geprüft wird gegen sie im Vertrag
    **und** im Dienst. Zwei Prüfungen gegen eine Liste sind in Ordnung; zwei
    Listen wären es nicht. Dieser Test fällt genau dann, wenn irgendwo eine
    Kopie entstanden ist, die eine Stimme weniger kennt.
    """
    antwort = _anlegen(client, owner_cookies, csrf_token, default_voice=stimme)

    assert antwort.status_code == 201, antwort.text
    assert db.query(AiProvider).one().default_voice == stimme


@pytest.mark.parametrize("erfunden", ["nova", "karl", "alloy2", "Alloy Deluxe"])
def test_an_invented_voice_never_reaches_the_column(
    erfunden: str, client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Abgewiesen am Feld, in das er gerade getippt hat — nicht im Gespräch.

    ``nova`` steht hier nicht zufällig an erster Stelle: es ist eine echte
    OpenAI-Stimme, nur eben aus der Text-zu-Sprache-Familie und nicht aus dem
    Realtime-Modell. Genau so entsteht der Fehlgriff, und er fiele ohne diese
    Prüfung erst der Gegenstelle auf — die weist das ``session.update`` dann
    ab, und das Gespräch liefe ohne Anweisungen und ohne Werkzeuge weiter.
    """
    antwort = _anlegen(client, owner_cookies, csrf_token, default_voice=erfunden)

    assert antwort.status_code == 422
    assert db.query(AiProvider).count() == 0


def test_an_invented_voice_is_refused_on_the_way_in_as_well(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Und beim Ändern bleibt die alte Wahl stehen, statt beschädigt zu werden."""
    zugang = _anlegen(client, owner_cookies, csrf_token, default_voice="coral").json()

    antwort = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_voice="nova"
    )

    assert antwort.status_code == 422
    assert db.query(AiProvider).one().default_voice == "coral"


def test_the_service_refuses_an_invented_voice_without_the_contract(db) -> None:
    """Der zweite Riegel, für die Schreibwege, die am Vertrag vorbeiführen.

    Ein Seed, ein Test, ein späterer Importweg rufen `create_provider` direkt
    auf. Ohne diese Prüfung stünde die erfundene Stimme dann in der Spalte, und
    der Betreiber suchte den Fehler in einem Sprachgespräch statt in seiner
    Einrichtung.
    """
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db,
            name="Sprachzugang",
            provider_kind="openai_realtime",
            default_model="gpt-realtime-2.1",
            enabled=True,
            requires_api_key=True,
            operator_api_key=None,
            default_voice="nova",
        )


def test_nothing_chosen_is_not_the_same_as_alloy(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Der ganze Grund, warum die Spalte ``nullable`` ist.

    Es wäre bequem, beim Anlegen die Standardstimme einzutragen — ein Feld
    weniger, das ``None`` sein kann. Der Preis stünde erst Jahre später auf der
    Rechnung: eine neue `STANDARDSTIMME` gälte dann für keinen einzigen
    bestehenden Zugang, und niemand fände den Grund.
    """
    angelegt = _anlegen(client, owner_cookies, csrf_token)

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["default_voice"] is None
    assert db.query(AiProvider).one().default_voice is None


def test_a_dropdown_without_a_choice_sends_an_empty_string(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """``""`` ist der Weg, auf dem „nichts gewählt" hier ankommt.

    Ein Auswahlfeld ohne Wahl schickt einen leeren String und kein ``null``.
    Landete der so in der Spalte, ginge er beim Verbinden als Stimme an OpenAI
    — und ``None`` heisst dort etwas völlig anderes als ``""``.
    """
    angelegt = _anlegen(client, owner_cookies, csrf_token, default_voice="")

    assert angelegt.status_code == 201, angelegt.text
    assert db.query(AiProvider).one().default_voice is None


def test_the_operator_may_type_what_he_reads(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Nachsichtig lesen, streng speichern.

    In der Oberfläche steht die Stimme gross („Alloy — neutral, ausgeglichen"),
    die API verlangt sie klein. Wer den Wert von Hand setzt, tippt ab, was er
    sieht; ein 422 dafür wäre eine Belehrung ohne Anlass. In der Spalte steht
    trotzdem genau eine Schreibweise.
    """
    angelegt = _anlegen(client, owner_cookies, csrf_token, default_voice="  Verse ")

    assert angelegt.status_code == 201, angelegt.text
    assert db.query(AiProvider).one().default_voice == "verse"


def test_a_field_left_out_leaves_the_voice_standing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Ein Chatzugang schickt das Feld gar nicht mit — und darf nichts löschen."""
    zugang = _anlegen(client, owner_cookies, csrf_token, default_voice="echo").json()

    geaendert = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_model="gpt-realtime"
    )

    assert geaendert.status_code == 200, geaendert.text
    assert db.query(AiProvider).one().default_voice == "echo"


def test_an_explicit_null_takes_the_voice_back(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Der Betreiber muss seine Wahl auch zurücknehmen können.

    „Nicht mitgeschickt" und „ausdrücklich ``null``" sehen im Vertrag beide wie
    ``None`` aus; auseinander hält sie erst `model_dump(exclude_unset=True)` im
    Router. Ohne diese Unterscheidung gäbe es keinen Weg zurück zur Vorgabe —
    ausser den Zugang zu löschen.
    """
    zugang = _anlegen(client, owner_cookies, csrf_token, default_voice="echo").json()

    geaendert = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_voice=None
    )

    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["default_voice"] is None
    assert db.query(AiProvider).one().default_voice is None
