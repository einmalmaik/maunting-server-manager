"""Zwei Anbieter, zwei Protokolle — und keine Verwechslung dazwischen.

Bis zum Sprachmodus gab es genau einen Anbieter, und deshalb gab es die Frage
nicht: *welche API spricht dieser Zugang eigentlich?* Mit ihm gibt es sie.
OpenRouter beantwortet Anfragen unter ``/chat/completions``, ElevenLabs liest
unter ``/text-to-speech/{voice}/stream-input`` vor — und zwar über eine
WebSocket-Sitzung statt über Nachrichten. Die beiden sind nicht ineinander
überführbar, und beide Wege müssen den jeweils falschen Zugang **vorher**
abweisen statt ihn an eine Adresse zu schicken, an der er nichts zu suchen hat.

Hier stand bis zum 16.08.2026 OpenAIs Realtime-API als zweiter Anbieter. Sie
konnte beides — denken und sprechen — und tat damit alles doppelt, was der Chat
schon konnte. Geblieben ist die Aufteilung, gewechselt hat der zweite Anbieter:
er spricht jetzt nur noch.

Dazu kommen zwei Eigenschaften, die je nur an einer Seite hängen: die
**Stimme** am Sprachzugang und das **hörende Modell** am Chatzugang. Beide sind
Betreiberentscheidungen, beide sind optional, und bei beiden heisst ``NULL``
„nichts hinterlegt". Der Unterschied ist keine Kosmetik: ohne Stimme gibt es
keinen Sprachmodus, und eine geratene stünde auf der Rechnung des Betreibers.

Was hier zugesichert wird:

* Ein Stimmzugang kommt nicht in den Chat — weder über die Auswahl noch über
  eine geratene Kennung.
* Der Katalogschlüssel geht nur an den Anbieter, der ihn verlangt, und im Kopf,
  den dieser Anbieter versteht.
* Das Vorwärmen beim Start fasst **jeden** Katalog an, auch den hinter einem
  Schlüssel. Bis zum 17.08.2026 stand hier das Gegenteil, und es stimmte auch —
  bis der Abruf lernte, sich den Schlüssel selbst zu holen und ohne einen gar
  nicht erst loszulaufen. Damit war die Ausnahme ohne Grund und der Katalog
  eines OpenAI-Zugangs nach jedem Neustart leer.
* Eine hinterlegte Stimme kommt zurück, wie sie hineinging — **gross wie
  klein**; eine, die den URL-Pfad verlassen könnte, kommt gar nicht erst hinein.
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
)
from services.ai_provider_registry import elevenlabs as elevenlabs_anbieter


# ── Die Registry ──────────────────────────────────────────────────────────


def test_the_two_providers_speak_different_protocols() -> None:
    assert ai_provider_registry.ANBIETER["openrouter"].protokoll == ai_provider_registry.CHAT
    assert (
        ai_provider_registry.ANBIETER["elevenlabs"].protokoll
        == ai_provider_registry.TTS
    )


def test_the_realtime_provider_is_gone() -> None:
    """Der Eintrag ist weg — und mit ihm das Protokoll.

    Kein Nachruf, sondern eine Zusage: ein zurückkehrender Eintrag brächte den
    zweiten Werkzeuglauf mit, den dieser Umbau abgeschafft hat. Wer ihn wieder
    aufnimmt, soll hier stolpern und nicht erst im Betrieb.
    """
    assert "openai_realtime" not in ai_provider_registry.ANBIETER
    assert not hasattr(ai_provider_registry, "REALTIME")


def test_asking_for_a_protocol_never_raises_on_an_unknown_kind() -> None:
    """`spricht()` filtert, `anbieter()` löst auf — und nur eines darf werfen.

    Der Unterschied ist nicht kosmetisch. `spricht()` läuft über Zeilen aus der
    Datenbank, und darunter kann eine aus einer Zukunftsversion sein (Downgrade,
    Migration 20260811_01). Eine Ausnahme nähme dort die ganze Liste mit, statt
    den einen Eintrag auszulassen.
    """
    assert ai_provider_registry.spricht("openrouter", ai_provider_registry.CHAT)
    assert not ai_provider_registry.spricht("openrouter", ai_provider_registry.TTS)
    assert ai_provider_registry.spricht("elevenlabs", ai_provider_registry.TTS)
    assert not ai_provider_registry.spricht("elevenlabs", ai_provider_registry.CHAT)
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
    stimme = AiProvider(
        id=2, name="Stimme", provider_kind="elevenlabs",
        default_model="eleven_flash_v2_5", enabled=True, requires_api_key=True,
    )
    assert ai_provider_service.spricht(chat, ai_provider_registry.CHAT)
    assert not ai_provider_service.spricht(chat, ai_provider_registry.TTS)
    assert ai_provider_service.spricht(stimme, ai_provider_registry.TTS)
    assert not ai_provider_service.spricht(stimme, ai_provider_registry.CHAT)


# ── Der Katalogleser ──────────────────────────────────────────────────────


def test_only_speaking_models_survive_the_elevenlabs_reader() -> None:
    """Der Katalog führt auch Modelle, die nicht vorlesen können.

    Eines davon in der Auswahl für den Sprachmodus wäre ein Eintrag, der beim
    ersten Satz scheitert — und zwar erst dort, mitten im Gespräch.
    """
    kann = elevenlabs_anbieter.katalog_lesen(
        {"model_id": "eleven_flash_v2_5", "name": "Flash v2.5", "can_do_text_to_speech": True}
    )
    assert kann is not None
    assert kann.model_id == "eleven_flash_v2_5"
    assert kann.name == "Flash v2.5"

    umwandler = elevenlabs_anbieter.katalog_lesen(
        {"model_id": "eleven_voice_changer", "can_do_text_to_speech": False}
    )
    assert umwandler is None


def test_a_model_that_does_not_say_what_it_can_is_left_out() -> None:
    """Fehlt das Feld, wird der Eintrag **nicht** übernommen.

    Ein unbekanntes Modell in einer Auswahl ist ein Versprechen, das MSM nicht
    halten kann. Das Gegenteil — im Zweifel aufnehmen — sähe grosszügig aus und
    verschöbe den Fehlschlag in das Gespräch.
    """
    assert elevenlabs_anbieter.katalog_lesen({"model_id": "was-auch-immer"}) is None


def test_the_elevenlabs_reader_admits_what_it_does_not_know() -> None:
    """Kein erfundenes Kontextfenster, keine erfundene Denkstufe.

    Ein Sprachmodell hat beides nicht. `None` heißt im übrigen Code „unbekannt"
    und nie „klein" (`ai_context_window.ermitteln`) — hier heisst es zusätzlich
    „gibt es nicht", und beides führt zum selben Verhalten.
    """
    modell = elevenlabs_anbieter.katalog_lesen(
        {"model_id": "eleven_flash_v2_5", "can_do_text_to_speech": True}
    )
    assert modell is not None
    assert modell.kontext_tokens is None
    assert modell.max_ausgabe_tokens is None
    assert modell.denkt is False
    assert modell.stufen == ()
    assert modell.cache_marke_noetig is False
    # Ohne Namen bleibt die Kennung stehen, statt dass „None" in der Auswahl
    # erscheint.
    assert modell.name == "eleven_flash_v2_5"


def test_a_broken_entry_is_skipped_and_not_fatal() -> None:
    assert elevenlabs_anbieter.katalog_lesen({}) is None
    assert elevenlabs_anbieter.katalog_lesen({"model_id": None}) is None
    assert elevenlabs_anbieter.katalog_lesen({"model_id": 42}) is None
    assert elevenlabs_anbieter.katalog_lesen({"model_id": ""}) is None


# ── Der Schlüssel im Katalogabruf ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_key_goes_only_to_the_provider_that_demands_it() -> None:
    """Ein Geheimnis reist nicht an eine Adresse, die es nicht braucht.

    OpenRouter gibt seinen Katalog offen heraus. Den Schlüssel trotzdem
    mitzuschicken wäre kein Fehler mit sichtbarer Folge — und genau deshalb
    steht hier ein Test: so etwas fällt im Betrieb nie auf.
    """
    async def koepfe_beim_abruf(kind: str, nutzlast) -> httpx.Headers:
        gesehen: list[httpx.Headers] = []

        def antworte(request: httpx.Request) -> httpx.Response:
            gesehen.append(request.headers)
            return httpx.Response(200, json=nutzlast)

        async with httpx.AsyncClient(transport=httpx.MockTransport(antworte)) as client:
            await ai_model_catalog._hole(
                client, ai_model_catalog.anbieter(kind), "sk-geheim"
            )
        return gesehen[-1]

    offen = await koepfe_beim_abruf(
        "openrouter", {"data": [{"id": "openai/gpt-5.6-luna"}]}
    )
    assert offen.get("authorization") is None, (
        "Der Schlüssel ging an OpenRouter, obwohl der Katalog dort offen ist."
    )


@pytest.mark.asyncio
async def test_elevenlabs_gets_its_key_in_its_own_header() -> None:
    """``xi-api-key`` und nicht ``Authorization: Bearer``.

    Ein Bearer-Token beantwortet ElevenLabs mit einem 401 — einem 401, das wie
    ein falscher Schlüssel aussieht und keiner ist. Genau solche Fehlersuchen
    kostet ein fest verdrahteter Kopf, und deshalb steht er am Anbieter.
    """
    gesehen: list[httpx.Headers] = []

    def antworte(request: httpx.Request) -> httpx.Response:
        gesehen.append(request.headers)
        # Eine **nackte Liste**, kein ``data``-Feld. Auch das ist Hausordnung
        # dieses Anbieters und stand `_hole` einmal im Weg.
        return httpx.Response(
            200, json=[{"model_id": "eleven_flash_v2_5", "can_do_text_to_speech": True}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(antworte)) as client:
        modelle = await ai_model_catalog._hole(
            client, ai_model_catalog.anbieter("elevenlabs"), "sk_geheim"
        )

    assert gesehen[-1].get("xi-api-key") == "sk_geheim"
    assert gesehen[-1].get("authorization") is None
    assert [modell.model_id for modell in modelle] == ["eleven_flash_v2_5"]


# ── Das Vorwärmen beim Start ──────────────────────────────────────────────


def test_prewarming_covers_key_bound_catalogs_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seit dem 17.08.2026 wärmt der Start **alle** Kataloge vor, auch die mit Schlüssel.

    Vorher blieben sie aussen vor, und die Begründung war richtig: ohne
    Schlüssel endete der Abruf in einem 401, wurde als Fehlversuch vermerkt, und
    `FEHLER_RUHE` verzögerte anschliessend den ersten echten Abruf um eine
    Minute — für einen Fehler, den niemand gemacht hatte.

    Der Preis dafür stand nur nicht daneben: an einem OpenAI-Zugang war der
    Katalog nach jedem Neustart leer, bis jemand die Provider-Einstellungen
    öffnete. Und leer heisst dort mehr als „keine Auswahl": keine Denkstufe,
    kein Kontextfenster, für jede Nachricht bis dahin.

    Der 401 ist inzwischen unmöglich geworden — der Abruf besorgt sich den
    Schlüssel selbst und unterbleibt, wenn es keinen gibt. Damit fällt der
    Grund für die Ausnahme weg. Die Stelle, an der das wirklich hängt, hält
    `test_ai_model_catalog.test_without_a_key_a_key_bound_catalog_is_not_even_asked`
    — dort, wo der Abruf steht, und nicht hier, wo nur das Anstossen steht.
    """
    angestossen: list[str] = []
    monkeypatch.setattr(
        ai_model_catalog,
        "_auffrischen_anstossen",
        lambda kind, schluessel=None: angestossen.append(kind) or True,
    )

    ai_model_catalog.vorwaermen_anstossen()

    # Gegen die Registry und nicht gegen zwei Namen: „alle" ist die Zusage,
    # und eine Namensliste im Test hiesse, dass ein neuer Anbieter still
    # herausfällt — der schluesselpflichtige `openai` fehlte hier genau so.
    assert set(angestossen) == set(ai_provider_registry.ANBIETER)
    assert any(
        ai_provider_registry.anbieter(kind).katalog_braucht_schluessel
        for kind in angestossen
    ), "Ohne einen schluesselpflichtigen Anbieter prueft dieser Test seinen Namen nicht"


# ── Die Stimme am Zugang ──────────────────────────────────────────────────


def _anlegen(
    client: TestClient, cookies: dict, csrf: str | None, **felder
) -> httpx.Response:
    """Einen Stimmzugang so anlegen, wie der Betreiber es tut: über das Formular.

    Über die Schnittstelle und nicht über den Dienst, weil die eine Hälfte
    dieser Zusagen genau dort entsteht: die 422 für eine unzulässige Kennung
    kommt aus `schemas.ai_provider.Stimme`, und ein Dienstaufruf ginge daran
    vorbei.
    """
    daten: dict = {
        "name": "Stimmzugang",
        "provider_kind": "elevenlabs",
        "default_model": "eleven_flash_v2_5",
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
    angelegt = _anlegen(
        client, owner_cookies, csrf_token, default_voice="21m00Tcm4TlvDq8ikWAM"
    )

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["default_voice"] == "21m00Tcm4TlvDq8ikWAM"
    assert db.query(AiProvider).one().default_voice == "21m00Tcm4TlvDq8ikWAM"
    # Und über die Liste, die das Einstellungsformular beim Öffnen liest: sonst
    # stünde die Wahl in der Datenbank und das Feld daneben leer da.
    gelesen = client.get("/api/ai/settings/providers", cookies=owner_cookies).json()
    assert gelesen[0]["default_voice"] == "21m00Tcm4TlvDq8ikWAM"


def test_the_case_of_a_voice_id_is_preserved(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Der teuerste denkbare Einzeiler an dieser Stelle wäre ein ``.lower()``.

    Bis zum 16.08.2026 stand hier einer, und er war richtig: die acht
    OpenAI-Stimmen hiessen ``alloy`` und ``verse``, klein geschrieben, und die
    Oberfläche zeigte sie gross. Eine ElevenLabs-Kennung ist dagegen gross- und
    kleinempfindlich — derselbe Einzeiler hätte jede zweite unbrauchbar gemacht,
    und zwar erst bei der Verbindung, als 404 der Gegenstelle.
    """
    angelegt = _anlegen(
        client, owner_cookies, csrf_token, default_voice="  EXAVITQu4vr4xnSDxMaL "
    )

    assert angelegt.status_code == 201, angelegt.text
    # Rand-Leerzeichen fallen weg — beim Kopieren kommt regelmässig eines mit.
    # Die Schreibweise bleibt.
    assert db.query(AiProvider).one().default_voice == "EXAVITQu4vr4xnSDxMaL"


@pytest.mark.parametrize(
    "gefaehrlich",
    [
        "../../../v1/user",
        "abc/def",
        "abc?model_id=teuer",
        "abc#anker",
        "abc def",
        "a" * 65,
    ],
)
def test_a_voice_id_can_never_leave_the_url_path(
    gefaehrlich: str, client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Die Kennung steht in einem **Pfadsegment** — das ist keine Formfrage.

    ``/v1/text-to-speech/{voice}/stream-input``: ein ``/`` darin wäre ein
    anderer Endpunkt, ein ``?`` ein angehängter Parameter, ein ``..`` ein
    Schritt nach oben. Anders als bei den acht Stimmen davor gibt es hier keine
    Liste, gegen die sich prüfen liesse — die Kennungen gehören dem Konto des
    Betreibers. Geprüft wird deshalb die Form, und die Form ist hier die
    Schranke.
    """
    antwort = _anlegen(client, owner_cookies, csrf_token, default_voice=gefaehrlich)

    assert antwort.status_code == 422
    assert db.query(AiProvider).count() == 0


def test_a_dangerous_voice_id_is_refused_on_the_way_in_as_well(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Und beim Ändern bleibt die alte Wahl stehen, statt beschädigt zu werden."""
    zugang = _anlegen(
        client, owner_cookies, csrf_token, default_voice="21m00Tcm4TlvDq8ikWAM"
    ).json()

    antwort = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_voice="../andere"
    )

    assert antwort.status_code == 422
    assert db.query(AiProvider).one().default_voice == "21m00Tcm4TlvDq8ikWAM"


def test_the_service_refuses_a_dangerous_voice_id_without_the_contract(db) -> None:
    """Der zweite Riegel, für die Schreibwege, die am Vertrag vorbeiführen.

    Ein Seed, ein Test, ein späterer Importweg rufen `create_provider` direkt
    auf. Zwei Prüfungen an zwei Stellen sind hier keine doppelte Kosmetik: die
    eine sichert das Formular, die andere die Funktion.
    """
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db,
            name="Stimmzugang",
            provider_kind="elevenlabs",
            default_model="eleven_flash_v2_5",
            enabled=True,
            requires_api_key=True,
            operator_api_key=None,
            default_voice="../woanders",
        )


def test_nothing_chosen_stays_nothing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Es gibt keine Standardstimme, und das ist der ganze Punkt.

    Eine einzutragen wäre bequem — ein Feld weniger, das ``None`` sein kann.
    Der Preis stünde auf der Rechnung des Betreibers: die Stimmen gehören
    seinem Konto, MSM kennt sie nicht, und jede geratene wäre eine Auswahl, die
    er nie getroffen hat. Ohne Stimme gibt es deshalb keinen Sprachmodus.
    """
    angelegt = _anlegen(client, owner_cookies, csrf_token)

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["default_voice"] is None
    assert db.query(AiProvider).one().default_voice is None


def test_an_empty_field_arrives_as_nothing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """``""`` ist der Weg, auf dem „nichts eingetragen" hier ankommt.

    Ein Formularfeld ohne Eingabe schickt einen leeren String und kein ``null``.
    Landete der so in der Spalte, ergäbe er beim Verbinden einen Pfad mit einem
    leeren Segment — und ``None`` heisst dort etwas völlig anderes als ``""``.
    """
    angelegt = _anlegen(client, owner_cookies, csrf_token, default_voice="")

    assert angelegt.status_code == 201, angelegt.text
    assert db.query(AiProvider).one().default_voice is None


def test_a_field_left_out_leaves_the_voice_standing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Ein Chatzugang schickt das Feld gar nicht mit — und darf nichts löschen."""
    zugang = _anlegen(
        client, owner_cookies, csrf_token, default_voice="21m00Tcm4TlvDq8ikWAM"
    ).json()

    geaendert = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_model="eleven_turbo_v2_5"
    )

    assert geaendert.status_code == 200, geaendert.text
    assert db.query(AiProvider).one().default_voice == "21m00Tcm4TlvDq8ikWAM"


def test_an_explicit_null_takes_the_voice_back(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Der Betreiber muss seine Wahl auch zurücknehmen können.

    „Nicht mitgeschickt" und „ausdrücklich ``null``" sehen im Vertrag beide wie
    ``None`` aus; auseinander hält sie erst `model_dump(exclude_unset=True)` im
    Router.
    """
    zugang = _anlegen(
        client, owner_cookies, csrf_token, default_voice="21m00Tcm4TlvDq8ikWAM"
    ).json()

    geaendert = _aendern(
        client, owner_cookies, csrf_token, zugang["id"], default_voice=None
    )

    assert geaendert.status_code == 200, geaendert.text
    assert db.query(AiProvider).one().default_voice is None


# ── Das hörende Modell am Chatzugang ──────────────────────────────────────


def _chatzugang(
    client: TestClient, cookies: dict, csrf: str | None, **felder
) -> httpx.Response:
    daten: dict = {
        "name": "Chatzugang",
        "provider_kind": "openrouter",
        "default_model": "openai/gpt-5.6-luna",
    }
    daten.update(felder)
    return client.post(
        "/api/ai/settings/providers",
        json=daten,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def test_the_transcription_model_survives_the_round_trip(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Eine **zweite** Modellspalte, und sie darf einen Schrägstrich enthalten.

    Genau darin unterscheidet sie sich von der Stimme: ``google/gemini-2.5-flash``
    ist eine gültige Modellkennung und wäre eine unzulässige Stimm-Kennung. Ein
    gemeinsamer Validator für beide wäre entweder zu eng für das Modell oder zu
    weit für die Stimme — und „zu weit für die Stimme" heisst: ein Pfad, der
    woandershin zeigt.
    """
    angelegt = _chatzugang(
        client, owner_cookies, csrf_token, transcription_model="google/gemini-2.5-flash"
    )

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["transcription_model"] == "google/gemini-2.5-flash"
    assert db.query(AiProvider).one().transcription_model == "google/gemini-2.5-flash"


def test_no_transcription_model_is_left_at_nothing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    """Ohne hinterlegtes Modell gibt es keinen Sprachmodus — und kein geratenes.

    Eines einzusetzen hiesse, dem Betreiber ein Modell in Rechnung zu stellen,
    das er nie ausgewählt hat.
    """
    angelegt = _chatzugang(client, owner_cookies, csrf_token)

    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["transcription_model"] is None
    assert db.query(AiProvider).one().transcription_model is None


def test_an_empty_transcription_model_arrives_as_nothing(
    client: TestClient, owner_cookies: dict, csrf_token: str, db
) -> None:
    angelegt = _chatzugang(client, owner_cookies, csrf_token, transcription_model="   ")

    assert angelegt.status_code == 201, angelegt.text
    assert db.query(AiProvider).one().transcription_model is None
