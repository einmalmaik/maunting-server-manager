"""Zwischenspeichern des Prompts: wer es verlangt, und was rausgeht.

Die Zahlen hier sind **gemessen**, am 2026-08-12 über alle 406 Einträge des
OpenRouter-Katalogs. Sie begründen die Bauform:

* **240** Modelle führen ``pricing.input_cache_read``, können also überhaupt
  zwischenspeichern. **166** führen keinen und können es nicht.
* Davon nennen **71** zusätzlich ``pricing.input_cache_write`` — und genau
  diese Menge ist deckungsgleich mit den Familien, die OpenRouter in seiner
  Doku als „explizit“ führt: Anthropic (28), Google (17), Alibaba Qwen (13),
  OpenAI ab GPT-5.6 (6). Keine Ausreißer in beide Richtungen.
* Die übrigen **174** speichern von selbst zwischen. Für sie ist nichts zu tun.

Daraus die Regel, die diese Datei festhält: **Schreibpreis vorhanden heißt
Marke nötig.** Und weil das eine gemessene Deckungsgleichheit ist und keine
zugesicherte, steht sie in genau einer Funktion — geht sie eines Tages
auseinander, ist hier die eine Stelle, die es merkt.

Der zweite Teil ist die Sendeform. Anders als beim Nachdenken ist **Weglassen**
hier richtig: es gibt keinen Anbieterdefault, der sich unbemerkt einschaltet
und abgerechnet wird. Ein Modell ohne Marke speichert nicht zwischen, Punkt.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiProvider, AiToolResult, User
from services import ai_model_catalog
from services.ai_provider_registry import openrouter
from services.ai_context_service import (
    WERKZEUG_KONTEXT_KOPF,
    build_provider_messages,
)
from services.openai_compatible_adapter import StreamUsage, stream_chat_completion


# ── Was der Katalog hergibt ───────────────────────────────────────────


def _eintrag(**preise) -> dict:
    """Ein Katalogeintrag mit genau den Preisfeldern, um die es geht."""
    return {
        "id": "anbieter/modell",
        "name": "Modell",
        "pricing": {"prompt": "0.000002", "completion": "0.00001", **preise},
    }


def test_a_write_price_means_the_model_wants_an_explicit_mark() -> None:
    """Anthropic, Google, Qwen, GPT-5.6 — die 71 mit Schreibpreis.

    Sie rechnen das Anlegen des Zwischenspeichers gesondert ab und legen ihn
    nur an, wenn die Anfrage es verlangt.
    """
    modell = openrouter.katalog_lesen(_eintrag(
        input_cache_read="0.0000002", input_cache_write="0.0000025",
    ))
    assert modell is not None
    assert modell.cache_marke_noetig is True


def test_a_read_price_alone_means_the_provider_already_does_it() -> None:
    """Die 174 mit reinem Lesepreis — GPT bis 5.5, Grok, DeepSeek, Moonshot.

    Hier waere eine Marke bestenfalls wirkungslos. Der Unterschied zu „kann es
    gar nicht“ ist fuer den Sendepfad keiner: beide Male geht nichts raus.
    """
    modell = openrouter.katalog_lesen(
        _eintrag(input_cache_read="0.0000005")
    )
    assert modell is not None
    assert modell.cache_marke_noetig is False


def test_no_cache_price_at_all_means_no_mark() -> None:
    """Die 166 ohne jedes Cache-Feld."""
    modell = openrouter.katalog_lesen(_eintrag())
    assert modell is not None
    assert modell.cache_marke_noetig is False


def test_a_broken_pricing_block_does_not_take_the_entry_down() -> None:
    """Fremddaten duerfen fehlen oder Unsinn sein, ohne den Katalog zu sprengen.

    Bei ueber 400 Eintraegen von einem fremden Dienst ist mit genau so etwas zu
    rechnen — und ein Eintrag ohne Preisblock ist ein brauchbarer Eintrag, nur
    eben einer ohne Zwischenspeicher.
    """
    for kaputt in ({}, {"pricing": None}, {"pricing": []}, {"pricing": {"input_cache_write": 5}}):
        rohdaten = {"id": "a/b", "name": "B", **kaputt}
        modell = openrouter.katalog_lesen(rohdaten)
        assert modell is not None, kaputt
        assert modell.cache_marke_noetig is False, kaputt


def test_the_mark_survives_a_thinking_model() -> None:
    """Denken und Zwischenspeichern sind zwei Aussagen, nicht eine.

    Der Leser hat zwei Ausgaenge — mit und ohne Denk-Objekt. Ohne diesen Test
    kann einer davon das Feld verlieren, ohne dass etwas auffaellt.
    """
    rohdaten = _eintrag(input_cache_write="0.0000025")
    rohdaten["reasoning"] = {"mandatory": False, "supported_efforts": ["high", "low"]}
    modell = openrouter.katalog_lesen(rohdaten)
    assert modell is not None
    assert modell.denkt is True
    assert modell.cache_marke_noetig is True


# ── Was rausgeht ──────────────────────────────────────────────────────


def _provider() -> AiProvider:
    return AiProvider(
        id=901,
        name="Cache",
        provider_kind="openrouter",
        default_model="anthropic/claude-sonnet-5",
        enabled=True,
        requires_api_key=False,
    )


async def _gesendeter_body(**kwargs) -> dict:
    """Fuehrt einen vollstaendigen Stream und gibt den Anfragekoerper zurueck."""
    aufgezeichnet: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        aufgezeichnet.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async for _chunk in stream_chat_completion(
            client,
            provider=_provider(),
            api_key=None,
            messages=[{"role": "user", "content": "Hi"}],
            usage=StreamUsage(),
            **kwargs,
        ):
            pass
    return aufgezeichnet


@pytest.mark.asyncio
async def test_the_mark_goes_out_at_the_top_level_and_without_a_ttl() -> None:
    """Oberste Ebene, nicht mitten in einer Nachricht — das ist der ganze Trick.

    So setzt der Anbieter die Marke selbst an den letzten wiederverwendbaren
    Block und schiebt sie mit dem Gespraech weiter. Marken je Nachricht haetten
    verlangt, dass der Adapter weiss, welcher Teil des Kontexts stabil ist — und
    das weiss er nicht.

    Ohne ``ttl`` und damit die kurze Frist: die lange kostet das Anlegen das
    Doppelte statt des 1,25-Fachen und traegt sich nur, wenn derselbe Prompt
    eine Stunde spaeter unveraendert wiederkommt.
    """
    body = await _gesendeter_body(cache_marke=True)
    assert body["cache_control"] == {"type": "ephemeral"}
    # Nicht in den Nachrichten — dort waere sie eine zweite, widersprechende
    # Angabe.
    assert all("cache_control" not in nachricht for nachricht in body["messages"])


@pytest.mark.asyncio
async def test_without_the_mark_the_field_is_absent_rather_than_false() -> None:
    """Der Unterschied zu ``reasoning``, und er ist Absicht.

    Beim Nachdenken geht das Feld **immer** mit, auch bei „aus“ — sonst greift
    der Anbieterdefault und das Modell denkt auf Rechnung des Betreibers
    weiter. Beim Zwischenspeichern gibt es keinen solchen Default: die Vorgabe
    ist ueberall „kein Zwischenspeicher“. Ein ``cache_control: false`` waere
    hier also kein Schutz, sondern ein erfundenes Feld.
    """
    body = await _gesendeter_body()
    assert "cache_control" not in body
    # Gegenprobe, damit dieser Test nicht bloss beweist, dass gar nichts
    # gesendet wurde: das Denk-Feld geht sehr wohl mit.
    assert body["reasoning"] == {"enabled": False}


@pytest.mark.asyncio
async def test_the_mark_does_not_disturb_thinking_or_tools() -> None:
    """Drei Felder nebeneinander, keines frisst das andere."""
    body = await _gesendeter_body(
        cache_marke=True,
        reasoning=True,
        reasoning_effort="high",
        tools=[{"type": "function", "function": {"name": "list_my_servers"}}],
    )
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["reasoning"] == {"enabled": True, "effort": "high"}
    assert body["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_a_forbidden_catalogue_still_goes_out() -> None:
    """„Keine Werkzeuge benutzen“ und „keinen Katalog schicken“ ist nicht dasselbe.

    Der Unterschied ist der Zwischenspeicher: bei Anthropic steht der
    Werkzeugkatalog ganz vorne im wiederverwendbaren Präfix. Wer ihn in der
    Schlussrunde weglässt, ändert die Anfrage an ihrer ersten Stelle — und
    verliert den Treffer ausgerechnet in der Runde, die den längsten Verlauf
    trägt. `tool_choice="none"` sagt dieselbe Absicht, ohne den Präfix
    anzufassen.
    """
    body = await _gesendeter_body(
        cache_marke=True,
        tools=[{"type": "function", "function": {"name": "list_my_servers"}}],
        tool_choice="none",
    )
    assert body["tool_choice"] == "none"
    assert [eintrag["function"]["name"] for eintrag in body["tools"]] == [
        "list_my_servers"
    ]


# ── Was überhaupt zwischengespeichert werden kann ─────────────────────
#
# Ein Anbieter speichert immer nur den **Präfix** einer Anfrage: alles bis zur
# ersten Abweichung von der vorigen. Damit ist die Reihenfolge der Nachrichten
# keine Geschmacksfrage, sondern entscheidet, wieviel überhaupt
# wiederverwendbar ist.


def _gespraech(db: Session, user: User) -> AiConversation:
    row = AiConversation(id=str(uuid4()), user_id=user.id, title="Zwischenspeicher")
    db.add(row)
    db.commit()
    return row


def test_what_changes_stands_behind_what_stays(
    db: Session, regular_user: User
) -> None:
    """Erst das Stabile, dann das Wechselnde — und ganz zuletzt die Frage.

    Der Werkzeugkontext wechselt nach jedem Lauf, der Lageblock jede Minute.
    Standen sie vor der Historie, endete der wiederverwendbare Teil bei der
    Zusammenfassung: ausgerechnet die Gesprächshistorie — der einzige Teil, der
    von sich aus stabil ist und mit jeder Runde wächst — lag dahinter und wurde
    nie wiederverwendet.

    Der erste Entwurf hängte beide ganz ans Ende, hinter die Frage. Der
    Zwischenspeicher stieg damit von 78 auf 98 Prozent — und die Werkzeugwahl
    kippte: im Benchmark vom 14.08.2026 rief `skill_lernen` vorher in zwei von
    drei Läufen `learn_skill`, danach in null von drei, dafür konsistent
    `list_my_servers`. Stand die Frage nicht mehr am Schluss, las das Modell
    zuletzt den Betriebszustand und antwortete darauf.

    Deshalb gilt beides zugleich, und dieser Test hält beides fest: das
    Wechselnde steht hinter dem Stabilen, **und** die Frage steht zuletzt. Der
    Präfix verliert dadurch nur die letzte Gesprächszeile — ein paar hundert
    Zeichen gegen die zehntausende davor.
    """
    conversation = _gespraech(db, regular_user)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    for index, text in enumerate(["Warum stuerzt er ab?", "Ich sehe nach.", "Und jetzt?"]):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="assistant" if index == 1 else "user",
            content=text, status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.add(AiToolResult(
        id=str(uuid4()), conversation_id=conversation.id,
        tool_name="read_server_status", result_json="Server laeuft",
    ))
    db.commit()

    nachrichten = build_provider_messages(db, conversation, "Und jetzt?")

    def stelle(text: str) -> int:
        return next(
            index for index, item in enumerate(nachrichten)
            if isinstance(item.get("content"), str) and text in item["content"]
        )

    # Der Systemprompt bleibt vorn — er ist der größte stabile Block.
    assert nachrichten[0]["role"] == "system"
    # Die Frage steht zuletzt. Gemessen, nicht gemeint: hängte man die beiden
    # wechselnden Blöcke dahinter, stieg der Zwischenspeicher von 68 auf 98
    # Prozent — und `skill_lernen` rief in null von drei Läufen `learn_skill`,
    # dafür dreimal `list_my_servers`. Das Modell antwortete auf den
    # Betriebszustand statt auf die Frage.
    assert nachrichten[-1]["content"] == "Und jetzt?"
    # Direkt davor das Wechselnde, also hinter der gesamten übrigen Historie.
    assert nachrichten[-2]["role"] == "system"
    assert nachrichten[-2]["content"].startswith("Lage (Auskunft des Panels")
    assert nachrichten[-3]["content"].startswith(WERKZEUG_KONTEXT_KOPF)
    # Der ältere Verlauf liegt davor und ist damit Teil des wiederverwendbaren
    # Präfix — das war vorher nicht so.
    assert stelle("Warum stuerzt er ab?") < stelle(WERKZEUG_KONTEXT_KOPF)
    assert stelle("Ich sehe nach.") < stelle(WERKZEUG_KONTEXT_KOPF)
