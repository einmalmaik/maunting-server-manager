"""Gebucht wird, was der Anbieter meldet — und wenn nicht, steht es dabei.

Diese Datei haelt die Zusagen fest, die aus einer konkreten Beobachtung
entstanden sind: die Verbrauchsanzeige nannte 500.000 Tokens fuer vier
Anfragen, das Dashboard des Anbieters rund 300.000. Die Ursache lag nicht in
der Anzeige, sondern zwei Schichten tiefer — von der Antwort des Anbieters
wurde nur ``total_tokens`` behalten, und die Kosten rechnete das Panel danach
selbst nach, mit *einem* Preis auf *alle* Tokens.

Geprueft wird deshalb entlang der Kette, nicht an ihrem Ende:

* Der Adapter liest, was der Anbieter schickt — vollstaendig.
* Die Abrechnung bucht die gemeldete Zahl und nicht ihre eigene.
* Bleibt der Anbieter stumm, wird geschaetzt **und markiert**.
* Eine Werkzeugschleife summiert ihre Runden, statt eine davon zu nehmen.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from models import AiProvider, User
from services.ai_usage_service import (
    MICROUNITS_PER_CENT,
    abrechnung,
    complete_ai_usage,
    reserve_ai_usage,
    usage_events,
)
from services.openai_compatible_adapter import (
    MIKRO_JE_USD,
    StreamUsage,
    stream_chat_completion,
    usage_addieren,
)


def _provider() -> AiProvider:
    return AiProvider(
        id=41,
        name="Abrechnung",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )


def _antwort(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


async def _lauf(stream: str) -> StreamUsage:
    """Laesst den Adapter einen vorgegebenen SSE-Strom lesen."""
    usage = StreamUsage()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return _antwort(stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for _ in stream_chat_completion(
            client,
            provider=_provider(),
            api_key=None,
            messages=[{"role": "user", "content": "Hi"}],
            usage=usage,
        ):
            pass
    return usage


# ── Der Adapter liest vollstaendig ────────────────────────────────────


@pytest.mark.asyncio
async def test_der_adapter_liest_die_ganze_aufschluesselung() -> None:
    """Alles, was OpenRouter meldet, kommt auch an.

    Vorher wurde hier nur ``total_tokens`` behalten. Aus einer einzigen Summe
    laesst sich nicht zurueckrechnen, was Eingabe war und was Ausgabe — und
    weil beide unterschiedlich viel kosten, war jede spaetere Kostenrechnung
    zwangslaeufig daneben.

    Es braucht dafuer **kein Feld im Request**: die Schalter
    ``usage:{include}`` und ``stream_options:{include_usage}`` sind bei
    OpenRouter abgekuendigt, die Zahlen kommen von selbst.
    """
    usage = await _lauf(
        'data: {"choices":[{"delta":{"content":"Hallo"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":194,"completion_tokens":6,'
        '"total_tokens":200,"prompt_tokens_details":{"cached_tokens":120},'
        '"completion_tokens_details":{"reasoning_tokens":4},"cost":0.0021}}\n\n'
        "data: [DONE]\n\n"
    )

    assert usage.total_tokens == 200
    assert usage.prompt_tokens == 194
    assert usage.completion_tokens == 6
    assert usage.cached_tokens == 120
    assert usage.reasoning_tokens == 4
    # 0,0021 USD — einmal ganzzahlig gemacht und danach nie wieder gerundet.
    assert usage.cost_micro_usd == 2_100
    assert usage.vom_anbieter is True
    assert usage.anfragen == 1


@pytest.mark.asyncio
async def test_der_zwischenspeicher_wird_von_beiden_seiten_gelesen() -> None:
    """Gelesen **und** geschrieben — beide stehen im selben Objekt.

    Warum es beide Zahlen braucht, steht bei der Spalte in
    `models/ai_usage_event.py`.
    """
    usage = await _lauf(
        'data: {"choices":[],"usage":{"prompt_tokens":20000,"completion_tokens":50,'
        '"total_tokens":20050,"prompt_tokens_details":'
        '{"cached_tokens":0,"cache_write_tokens":18000}}}\n\n'
        "data: [DONE]\n\n"
    )

    assert usage.cached_tokens == 0
    assert usage.cache_write_tokens == 18_000


@pytest.mark.asyncio
async def test_ein_anbieter_ohne_zwischenspeicher_meldet_null_und_nicht_nichts() -> None:
    """Fehlt das Feld, ist die Antwort 0 — wie bei den gelesenen Tokens.

    Ein Modell ohne Zwischenspeicher hat nichts geschrieben, und das ist eine
    Aussage und keine Luecke. Die Unterscheidung "nicht gemeldet" gilt bei den
    Tokensummen; bei den Teilmengen des Zwischenspeichers ist beides dasselbe.
    """
    usage = await _lauf(
        'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":25}}\n\n'
        "data: [DONE]\n\n"
    )

    assert usage.cache_write_tokens == 0


@pytest.mark.asyncio
async def test_ohne_usage_zeile_gilt_nichts_als_gemessen() -> None:
    """Ein stummer Anbieter hat nicht null Tokens verbraucht.

    ``None`` und ``0`` sind hier zwei verschiedene Aussagen, und nur eine davon
    stimmt. Wer sie zusammenwirft, verschenkt entweder Kontingent oder erfindet
    Verbrauch.
    """
    usage = await _lauf(
        'data: {"choices":[{"delta":{"content":"Hallo"}}]}\n\n' "data: [DONE]\n\n"
    )

    assert usage.total_tokens is None
    assert usage.cost_micro_usd is None
    assert usage.vom_anbieter is False
    # Stattgefunden hat die Anfrage trotzdem.
    assert usage.anfragen == 1


@pytest.mark.asyncio
async def test_fehlende_summe_wird_aus_den_teilen_gebildet() -> None:
    """Meldet der Anbieter nur die Teile, gilt die Anfrage trotzdem als gemessen.

    Die Kontingente haengen an einer Gesamtzahl. Fehlte sie, waere eine
    vollstaendig gemeldete Anfrage sonst als Schaetzung durchgegangen.
    """
    usage = await _lauf(
        'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":25}}\n\n'
        "data: [DONE]\n\n"
    )

    assert usage.total_tokens == 125
    assert usage.vom_anbieter is True


@pytest.mark.asyncio
async def test_ein_leeres_usage_objekt_ist_keine_messung() -> None:
    """Ein ``usage``-Feld ohne Zahlen darf nicht als gemessen gelten."""
    usage = await _lauf(
        'data: {"choices":[],"usage":{"cost":0.5}}\n\n' "data: [DONE]\n\n"
    )

    assert usage.vom_anbieter is False
    assert usage.cost_micro_usd is None


# ── Runden summieren sich ─────────────────────────────────────────────


def test_werkzeugrunden_werden_summiert_nicht_ersetzt() -> None:
    """Drei Runden sind drei Anfragen — und drei Prompts.

    Genau das steckt hinter „500.000 Tokens fuer vier Anfragen". Eine
    Chatnachricht ist nicht eine Anbieteranfrage: jede Werkzeugrunde ruft den
    Anbieter erneut und schickt den gewachsenen Verlauf komplett mit. Der
    Anbieter rechnet genauso ab — sichtbar war es nur nirgends.
    """
    gesamt = StreamUsage(
        total_tokens=1_000, prompt_tokens=900, completion_tokens=100,
        cached_tokens=400, cache_write_tokens=500, cost_micro_usd=500,
        vom_anbieter=True, anfragen=1,
    )
    for _ in range(2):
        usage_addieren(gesamt, StreamUsage(
            total_tokens=2_000, prompt_tokens=1_900, completion_tokens=100,
            cached_tokens=1_500, cache_write_tokens=0, cost_micro_usd=800,
            vom_anbieter=True, anfragen=1,
        ))

    assert gesamt.total_tokens == 5_000
    assert gesamt.prompt_tokens == 4_700
    assert gesamt.completion_tokens == 300
    assert gesamt.cached_tokens == 3_400
    # Geschrieben wird einmal, gelesen in jeder Folgerunde — genau daran sieht
    # man, dass der Zwischenspeicher innerhalb eines Laufs greift.
    assert gesamt.cache_write_tokens == 500
    assert gesamt.cost_micro_usd == 2_100
    assert gesamt.anfragen == 3
    assert gesamt.vom_anbieter is True


def test_eine_stumme_runde_macht_den_ganzen_lauf_zur_schaetzung() -> None:
    """Halb gemessen ist nicht gemessen.

    Eine Summe, die zur Haelfte aus Zahlen des Anbieters und zur Haelfte aus
    einer Schaetzung besteht, waere sonst nicht von einer vollstaendig
    gemessenen zu unterscheiden — und wer damit seine Rechnung prueft, prueft
    sie gegen eine Vermutung.
    """
    gesamt = StreamUsage(total_tokens=1_000, cost_micro_usd=500, vom_anbieter=True, anfragen=1)
    usage_addieren(gesamt, StreamUsage(anfragen=1))

    assert gesamt.vom_anbieter is False
    # Was gemeldet wurde, bleibt trotzdem erhalten — es ist ja nicht falsch.
    assert gesamt.total_tokens == 1_000
    assert gesamt.anfragen == 2


# ── Die Abrechnung folgt dem Anbieter ─────────────────────────────────


def test_gemeldete_kosten_werden_gebucht_wie_sie_kamen() -> None:
    """Der gepflegte Preis wird nicht einmal befragt, wenn der Anbieter spricht."""
    usage = StreamUsage(total_tokens=200, cost_micro_usd=2_100, vom_anbieter=True, anfragen=1)

    tokens, kosten, herkunft = abrechnung(
        usage,
        reserved_tokens=9_999,
        estimated_actual_tokens=9_999,
        token_price_micro_usd_per_million=500 * MICROUNITS_PER_CENT,
    )

    assert (tokens, kosten, herkunft) == (200, 2_100, "provider")


def test_eine_messung_darf_die_reservierung_unterschreiten() -> None:
    """Eine zu hohe Schaetzung muss schrumpfen duerfen.

    Hier stand ``max(reserviert, gerechnet)``. Der Gedanke war, dass eine
    Ueberschreitung nicht nachtraeglich verschwinden soll — die Wirkung war,
    dass eine zu hoch geratene Reserve **fuer immer** stehen blieb, auch wenn
    hinterher die gemessene Zahl vorlag. Eine Messung sticht eine Schaetzung;
    das ist der ganze Zweck einer Messung.
    """
    usage = StreamUsage(total_tokens=50, cost_micro_usd=10, vom_anbieter=True, anfragen=1)

    tokens, kosten, _ = abrechnung(
        usage, reserved_tokens=500_000, estimated_actual_tokens=500_000
    )

    assert (tokens, kosten) == (50, 10)


def test_ohne_anbieterzahlen_wird_geschaetzt_und_markiert() -> None:
    """Geschaetzt sieht nicht aus wie gemessen."""
    tokens, kosten, herkunft = abrechnung(
        StreamUsage(anfragen=1),
        reserved_tokens=100,
        estimated_actual_tokens=1_000_000,
        token_price_micro_usd_per_million=120 * MICROUNITS_PER_CENT,
    )

    assert tokens == 1_000_000
    # 1,20 USD je Million — der Betrag, der in ganzen Cent nicht eintragbar war.
    assert kosten == 120 * MICROUNITS_PER_CENT
    assert herkunft == "estimate"


def test_ohne_preis_bleiben_die_kosten_ehrlich_bei_null() -> None:
    """MSM erfindet keinen Preis — und sagt, dass es keinen gab."""
    tokens, kosten, herkunft = abrechnung(
        StreamUsage(anfragen=1), reserved_tokens=100, estimated_actual_tokens=700
    )

    assert (tokens, kosten, herkunft) == (700, 0, "none")


def test_nach_einem_abbruch_gilt_konservativ_die_reserve() -> None:
    """Ein Abbruch ohne Anbieterzahl bucht die Reserve, nicht die Schaetzung.

    Nach einem Abbruch ist unbekannt, wieviel der Anbieter bereits geliefert
    hat. Verschenkte Tokens sind das schlechtere Risiko als ein paar zu viel
    gebuchte.
    """
    tokens, _, _ = abrechnung(
        StreamUsage(anfragen=1),
        reserved_tokens=4_000,
        estimated_actual_tokens=9,
        failed=True,
    )

    assert tokens == 4_000


def test_ein_dollar_sind_eine_million_microunits() -> None:
    """Die Einheiten der beiden Schichten muessen zusammenpassen.

    Der Adapter rechnet USD in Microunits um, die Abrechnung bucht sie. Laufen
    die beiden Konstanten auseinander, verschiebt sich jede Kostenangabe um
    einen Faktor 100 — und niemand sieht es der Zahl an.
    """
    assert MIKRO_JE_USD == 100 * MICROUNITS_PER_CENT


# ── Die Einzelaufstellung ─────────────────────────────────────────────


def test_die_aufstellung_zeigt_die_aufschluesselung_und_ihre_herkunft(
    db: Session, regular_user: User
) -> None:
    """Der Nachweis, mit dem sich eine Zeile gegen das Dashboard halten laesst."""
    ereignis = reserve_ai_usage(
        db, regular_user, request_id=uuid4(), estimated_tokens=500,
        estimated_cost_microunits=0, model="model-a",
    )
    complete_ai_usage(
        db, ereignis,
        actual_tokens=200,
        actual_cost_microunits=2_100,
        aufschluesselung=StreamUsage(
            total_tokens=200, prompt_tokens=194, completion_tokens=6,
            cached_tokens=120, cache_write_tokens=60, reasoning_tokens=4,
            cost_micro_usd=2_100, vom_anbieter=True, anfragen=3,
        ),
        cost_source="provider",
    )
    db.commit()

    zeilen, mehr = usage_events(db, user_id=regular_user.id)

    assert mehr is False
    assert len(zeilen) == 1
    zeile = zeilen[0]
    assert zeile.tokens == 200
    assert zeile.prompt_tokens == 194
    # Beide Zahlen des Zwischenspeichers kommen nebeneinander an: erst zusammen
    # ergeben sie eine Trefferquote statt einer Zahl. Gebucht war
    # ``cache_write_tokens`` schon laenger, ausgeliefert wurde sie nicht.
    assert zeile.cached_tokens == 120
    assert zeile.cache_write_tokens == 60
    assert zeile.provider_requests == 3
    assert zeile.cost_micro_usd == 2_100
    assert zeile.cost_source == "provider"
    assert zeile.username == regular_user.username


def test_die_aufstellung_blaettert_und_sagt_ob_mehr_kommt(
    db: Session, regular_user: User
) -> None:
    """``has_more`` statt eines Gesamtzaehlers.

    Ein ``count(*)`` ueber dieselbe Tabelle kostet eine zweite Abfrage fuer
    eine Zahl, die niemand braucht, um weiterzublaettern.
    """
    for _ in range(3):
        ereignis = reserve_ai_usage(
            db, regular_user, request_id=uuid4(), estimated_tokens=10,
            estimated_cost_microunits=0,
        )
        complete_ai_usage(db, ereignis, actual_tokens=10, actual_cost_microunits=0)
    db.commit()

    erste, mehr = usage_events(db, user_id=regular_user.id, limit=2)
    assert len(erste) == 2 and mehr is True

    zweite, mehr = usage_events(db, user_id=regular_user.id, limit=2, offset=2)
    assert len(zweite) == 1 and mehr is False
    # Kein Ueberlappen: die Sortierung ist ueber die ID eindeutig.
    assert {zeile.id for zeile in erste}.isdisjoint({zeile.id for zeile in zweite})


def test_eine_bestandszeile_behauptet_keine_herkunft(
    db: Session, regular_user: User
) -> None:
    """Ohne Aufschluesselung bleibt sie leer — statt eine Null zu erfinden."""
    ereignis = reserve_ai_usage(
        db, regular_user, request_id=uuid4(), estimated_tokens=10,
        estimated_cost_microunits=0,
    )
    complete_ai_usage(db, ereignis, actual_tokens=10, actual_cost_microunits=0)
    db.commit()

    zeile = usage_events(db, user_id=regular_user.id)[0][0]

    assert zeile.cost_source is None
    assert zeile.prompt_tokens is None
    assert zeile.provider_requests is None
