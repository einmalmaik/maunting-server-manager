"""Das Gehoer: was genau hinausgeht, und was zurueckkommen darf.

Diese Datei gab es nicht, und das war die groesste Luecke im Sprachmodus: der
Weg vom Mikrofon zum Wortlaut ist das eine Stueck, ohne das nichts von allem
anderen passiert — und er war ungeprueft. Aufgefallen ist es beim Umbau vom
17.08.2026, als der Aufruf von ``/chat/completions`` auf OpenRouters
``/audio/transcriptions`` wechselte und **alle 82 Sprachtests gruen blieben**.
Ein Test, der einen Endpunktwechsel nicht bemerkt, prueft den Endpunkt nicht.

Gepinnt wird deshalb die Anfrage selbst: Adresse, Modellzeile, Nutzlast,
Kopfzeilen. Nicht, weil die Form schoen sein soll, sondern weil jede einzelne
schon einmal falsch war oder es leicht sein koennte — das Modell aus der
falschen Spalte, der Schluessel im falschen Kopf, der Ton am falschen Pfad.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from services import ai_stt
from services.openai_compatible_adapter import AiProviderRequestError, StreamUsage


class _Zugang:
    """Ein Zugang, so viel davon wie `hoeren` anfasst."""

    provider_kind = "openrouter"
    default_model = "openai/gpt-5.6"
    transcription_model = "openai/gpt-transcribe"
    requires_api_key = True


def _ton(sekunden: float) -> bytes:
    """Stille der gewuenschten Laenge — `hoeren` misst Bytes, nicht Inhalt."""
    return b"\x00\x00" * int(ai_stt.ABTASTRATE * sekunden)


def _antwort(text: str = "Starte den Server neu", **rest) -> httpx.Response:
    return httpx.Response(200, json={"text": text, **rest})


async def _hoeren(handler, *, zugang=None, pcm=None, **rest) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await ai_stt.hoeren(
            client,
            provider=zugang or _Zugang(),
            api_key="sk-or-testschluessel",
            pcm=pcm if pcm is not None else _ton(2.0),
            **rest,
        )


@pytest.mark.asyncio
async def test_der_ton_geht_an_den_transkriptionsendpunkt_und_nicht_in_den_chat():
    """Der Pfad ist die Zusage.

    Bis zum 17.08.2026 ging Gesprochenes als ``input_audio``-Inhaltsteil an
    ``/chat/completions`` — in dem Glauben, OpenRouter habe keinen eigenen
    Endpunkt. Es hat einen. Der Umweg funktionierte, kostete aber ein
    Chatmodell, das nachdenkt, statt eines Dienstes, der abschreibt.

    Ein Test darauf ist kein Selbstzweck: die Verwechslung ist beim naechsten
    Mal genauso leicht, weil ``/models`` bis heute kein Transkriptionsmodell
    fuehrt und der Endpunkt deshalb nicht existiert *aussieht*.
    """
    gesehen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["url"] = str(request.url)
        return _antwort()

    await _hoeren(handler)

    assert gesehen["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert "chat/completions" not in gesehen["url"]


@pytest.mark.asyncio
async def test_gehoert_wird_mit_dem_hoerenden_modell_und_nicht_mit_dem_denkenden():
    """``transcription_model``, nicht ``default_model``.

    Die beiden stehen in derselben Zeile der Datenbank nebeneinander, und die
    falsche Spalte faellt im Betrieb nicht auf: ein Chatmodell beantwortet die
    Anfrage anstandslos. Es kostet nur ein Vielfaches und antwortet langsamer.
    """
    gesehen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["body"] = json.loads(request.content)
        return _antwort()

    await _hoeren(handler)

    assert gesehen["body"]["model"] == "openai/gpt-transcribe"
    assert gesehen["body"]["model"] != _Zugang.default_model


@pytest.mark.asyncio
async def test_der_ton_geht_als_wav_mit_kopf_und_nicht_als_rohes_pcm():
    """Rohes PCM sagt nicht, wie schnell es abgespielt gehoert.

    Ohne Kopf versteht die Gegenstelle dieselbe Aufnahme je nach Annahme zu hoch
    oder zu tief — und liefert Kauderwelsch statt einer Fehlermeldung. Genau
    deshalb wird der Kopf hier geprueft und nicht bloss die Anwesenheit von
    Daten.
    """
    gesehen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["body"] = json.loads(request.content)
        return _antwort()

    await _hoeren(handler)

    ton = gesehen["body"]["input_audio"]
    assert ton["format"] == "wav"
    roh = base64.b64decode(ton["data"])
    assert roh[:4] == b"RIFF"
    assert roh[8:12] == b"WAVE"
    # Die Abtastrate steht im Kopf an Byte 24 und muss die des Browsers sein.
    assert int.from_bytes(roh[24:28], "little") == ai_stt.ABTASTRATE


@pytest.mark.asyncio
async def test_der_schluessel_steht_im_authorization_kopf_und_nichts_sonst():
    """Bearer — und keine Kennzeichnung des Panels.

    OpenRouter nimmt ``HTTP-Referer`` und ``X-Title`` fuer seine oeffentliche
    Rangliste entgegen. Ein selbst gehostetes Panel meldet weder seine Adresse
    noch seinen Namen an einen Dritten, damit es dort erscheint. Das ist eine
    Entscheidung und kein Versehen, deshalb steht sie als Zusage hier.
    """
    gesehen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["kopf"] = request.headers
        return _antwort()

    await _hoeren(handler)

    assert gesehen["kopf"]["authorization"] == "Bearer sk-or-testschluessel"
    assert "http-referer" not in gesehen["kopf"]
    assert "x-title" not in gesehen["kopf"]


@pytest.mark.asyncio
async def test_ohne_hinterlegtes_hoermodell_wird_keines_geraten():
    """Der Betreiber zahlt nur, was er ausgewaehlt hat.

    Ein Rueckfall auf ``default_model`` waere bequem und stellte ihm eine
    Rechnung fuer ein Modell, das er nie gesehen hat.
    """
    class OhneHoeren(_Zugang):
        transcription_model = None

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("Es darf gar keine Anfrage hinausgehen")

    with pytest.raises(AiProviderRequestError) as fehler:
        await _hoeren(handler, zugang=OhneHoeren())
    assert fehler.value.code == "AI_PROVIDER_MODEL_MISSING"


@pytest.mark.asyncio
async def test_ein_huster_kostet_keine_anfrage():
    """Unter `MIN_SEKUNDEN` geht nichts hinaus.

    Der Handler wirft, wenn er doch gerufen wird: eine bezahlte Anfrage je
    Raeuspern ist der Fehler, den man erst auf der Rechnung sieht.
    """
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("Zu kurzer Ton darf nicht hinausgehen")

    with pytest.raises(ai_stt.NichtsVerstanden):
        await _hoeren(handler, pcm=_ton(0.1))


@pytest.mark.asyncio
async def test_eine_zu_lange_aufnahme_verliert_ihren_anfang_und_nicht_ihr_ende():
    """Wer 40 Sekunden redet, meint das Zuletztgesagte.

    Hinten abzuschneiden hiesse, die Frage wegzuwerfen und die Einleitung zu
    behalten.
    """
    gesehen: dict = {}
    laenge = ai_stt.MAX_SEKUNDEN + 10

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen["body"] = json.loads(request.content)
        return _antwort()

    # Erkennbarer Anfang, damit „vorne abgeschnitten" pruefbar ist.
    pcm = b"\x11\x11" * ai_stt.ABTASTRATE + _ton(laenge)
    await _hoeren(handler, pcm=pcm)

    roh = base64.b64decode(gesehen["body"]["input_audio"]["data"])
    nutzdaten = roh[44:]
    assert b"\x11\x11" not in nutzdaten, "Der Anfang haette wegfallen muessen"
    sekunden = len(nutzdaten) / (ai_stt.ABTASTRATE * 2)
    assert sekunden <= ai_stt.MAX_SEKUNDEN + 0.01


@pytest.mark.asyncio
async def test_stille_ist_kein_anbieterfehler():
    """Leerer Text heisst „nichts verstanden", nicht „Anbieter kaputt".

    Die beiden verlangen verschiedene Antworten: bei Stille geht der Sprachmodus
    still auf „bereit" zurueck, bei einem Anbieterfehler sagt er es. Ein leerer
    String zwaenge die Bruecke, beides gleich zu behandeln.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return _antwort(text="   ")

    with pytest.raises(ai_stt.NichtsVerstanden):
        await _hoeren(handler)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "AI_PROVIDER_AUTH_FAILED"),
        (403, "AI_PROVIDER_AUTH_FAILED"),
        (404, "AI_PROVIDER_ENDPOINT_NOT_FOUND"),
        (429, "AI_PROVIDER_RATE_LIMITED"),
        (500, "AI_PROVIDER_UNAVAILABLE"),
        (400, "AI_PROVIDER_REQUEST_REJECTED"),
    ],
)
@pytest.mark.asyncio
async def test_fehlercodes_sind_dieselben_wie_im_chat(status: int, code: str):
    """Eine Wahrheit ueber Statuscodes, nicht zwei.

    Das Gehoer geht nicht durch `openai_compatible_adapter` — es spricht ein
    anderes Protokoll. Die Abbildung von Status auf Fehlercode kommt trotzdem
    von dort (`_error_code`), damit ein abgelaufener Schluessel im Sprachmodus
    nicht anders heisst als im Chat.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "kaputt"}})

    with pytest.raises(AiProviderRequestError) as fehler:
        await _hoeren(handler)
    assert fehler.value.code == code


@pytest.mark.asyncio
async def test_eine_kaputte_antwort_reisst_das_gespraech_nicht_mit():
    """Kein JSON heisst Protokollfehler und nicht `AttributeError`."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Wartungsarbeiten</html>")

    with pytest.raises(AiProviderRequestError) as fehler:
        await _hoeren(handler)
    assert fehler.value.code == "AI_PROVIDER_PROTOCOL_ERROR"


@pytest.mark.asyncio
async def test_gemeldete_tokenzahlen_landen_im_messwert():
    """Was der Anbieter meldet, wird uebernommen — mehr nicht.

    Gebucht wird hier nichts — das tut `ai_voice_bridge` nach gelungener
    Abschrift (siehe Modulkopf); wer die Zahlen sehen will, gibt ein
    `StreamUsage` mit.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return _antwort(usage={"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128})

    messwerte = StreamUsage()
    await _hoeren(handler, usage=messwerte)

    assert messwerte.prompt_tokens == 120
    assert messwerte.completion_tokens == 8
    assert messwerte.total_tokens == 128
    assert messwerte.vom_anbieter is True


@pytest.mark.asyncio
async def test_ohne_gemeldete_zahlen_bleibt_der_messwert_leer_statt_zu_raten():
    """Ein Anbieter ohne ``usage`` ist der Normalfall und kein Fehler.

    Eine Ausnahme dafuer liesse ein Gespraech abreissen, dessen Abschrift
    laengst da ist.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return _antwort()

    messwerte = StreamUsage()
    wortlaut = await _hoeren(handler, usage=messwerte)

    assert wortlaut == "Starte den Server neu"
    assert messwerte.total_tokens is None
    assert messwerte.vom_anbieter is False
