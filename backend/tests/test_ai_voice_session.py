"""Die Sprachsitzung, soweit sie sich ohne Anbieter prüfen lässt.

**Was diese Datei nicht kann, und das gehört an den Anfang:** sie prüft nicht,
ob OpenAI die hier gebauten Ereignisse annimmt. Dafür bräuchte es einen
Betreiberschlüssel, und den hat die Testsuite nicht — ein Netzabruf gegen einen
fremden, kostenpflichtigen Dienst wäre in einer Testsuite ohnehin falsch.

Geprüft wird deshalb alles, was **auf dieser Seite der Leitung** liegt: die Form
der Ereignisse, die Übersetzung zwischen den beiden Leitungen, die Grenzen und
das, was der Browser zu sehen bekommt. Der Rest ist Sache eines Rauchtests mit
einem echten Schlüssel; er steht in `docs/self-hosting.md`.
"""

from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState

from models import AiConversation, AiMessage, User
from services import ai_voice_session as sitzung


# ── Doppelgänger für die beiden Leitungen ─────────────────────────────────


class FalscherBrowser:
    """Der Browser-WebSocket, so viel davon wie die Sitzung anfasst."""

    def __init__(self, rahmen: list[dict] | None = None) -> None:
        self._eingang = list(rahmen or [])
        self.binaer: list[bytes] = []
        self.texte: list[dict] = []
        self.client_state = WebSocketState.CONNECTED

    async def receive(self) -> dict:
        if self._eingang:
            return self._eingang.pop(0)
        # Nichts mehr zu sagen — aber auch nicht weg. Ohne dieses Warten
        # endete die Pumpe sofort und jeder Test über die Gegenrichtung
        # liefe ins Leere.
        await asyncio.sleep(3600)
        raise AssertionError("unerreichbar")

    async def send_bytes(self, daten: bytes) -> None:
        self.binaer.append(daten)

    async def send_text(self, text: str) -> None:
        self.texte.append(json.loads(text))

    def art(self, art: str) -> list[dict]:
        return [eintrag for eintrag in self.texte if eintrag.get("art") == art]


class FalscheGegenstelle:
    """Die ausgehende Verbindung zu OpenAI."""

    def __init__(self, ereignisse: list[dict] | None = None) -> None:
        self.gesendet: list[dict] = []
        self._ausgang = list(ereignisse or [])
        self.geschlossen = False

    async def send(self, rohtext: str) -> None:
        self.gesendet.append(json.loads(rohtext))

    async def close(self) -> None:
        self.geschlossen = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._ausgang:
            return json.dumps(self._ausgang.pop(0))
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    def gesendet_vom_typ(self, typ: str) -> list[dict]:
        return [eintrag for eintrag in self.gesendet if eintrag.get("type") == typ]


# ── Adresse und Konfiguration ─────────────────────────────────────────────


def test_the_websocket_address_comes_from_the_registry_base_url() -> None:
    """Eine Adresse, nicht zwei. Ein Umzug des Anbieters wird einmal nachgezogen."""
    assert sitzung.verbindungsadresse("https://api.openai.com/v1", "gpt-realtime-2.1") == (
        "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
    )
    # Ein Schrägstrich am Ende darf keinen doppelten erzeugen.
    assert sitzung.verbindungsadresse("https://api.openai.com/v1/", "m") == (
        "wss://api.openai.com/v1/realtime?model=m"
    )


def test_the_session_is_configured_before_anyone_speaks() -> None:
    konfig = sitzung.sitzungskonfiguration(
        modell="gpt-realtime-2.1", anweisungen="Du bist das Panel.", stimme="alloy"
    )
    assert konfig["type"] == "session.update"
    inneres = konfig["session"]
    assert inneres["model"] == "gpt-realtime-2.1"
    assert inneres["instructions"] == "Du bist das Panel."
    # Ohne Transkription der Eingabe wüsste MSM nicht, was gesagt wurde — und
    # könnte eine gesprochene Bestätigung weder anzeigen noch belegen.
    assert inneres["audio"]["input"]["transcription"]["model"]
    # Die Gegenstelle entscheidet am Inhalt, ob ein Satz zu Ende ist, nicht an
    # einer Pause. Das ist der Unterschied zwischen Nachdenken und Fertigsein.
    assert inneres["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert inneres["audio"]["output"]["voice"] == "alloy"
    assert inneres["audio"]["input"]["format"]["rate"] == sitzung.ABTASTRATE
    assert inneres["audio"]["output"]["format"]["rate"] == sitzung.ABTASTRATE


def test_without_tools_the_catalog_key_stays_absent() -> None:
    """Ein leerer Werkzeugkatalog ist kein Katalog.

    ``tools: []`` neben ``tool_choice: "auto"`` wäre die Aufforderung, aus dem
    Nichts zu wählen. Solange die Werkzeugbrücke nicht eingehängt ist, steht
    schlicht nichts da.
    """
    konfig = sitzung.sitzungskonfiguration(modell="m", anweisungen="a", stimme="alloy")
    assert "tools" not in konfig["session"]
    assert "tool_choice" not in konfig["session"]


def test_the_tool_catalog_travels_once_per_session() -> None:
    """Der Posten, der im Chat 94 Prozent des Prompts ausmacht — hier einmal."""
    werkzeuge = [{"type": "function", "name": "list_my_servers", "parameters": {}}]
    konfig = sitzung.sitzungskonfiguration(
        modell="m", anweisungen="a", stimme="alloy", werkzeuge=werkzeuge
    )
    assert konfig["session"]["tools"] == werkzeuge
    assert konfig["session"]["tool_choice"] == "auto"


# ── Die Pumpe zum Anbieter ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_from_the_browser_arrives_base64_encoded() -> None:
    """Binär auf unserer Leitung, Base64 auf der fremden.

    Der Unterschied spart ein Drittel Bandbreite auf dem Stück Weg, das MSM
    gehört — die Gegenstelle verlangt Base64 in einem JSON-Ereignis, unser
    eigener Browser nicht.
    """
    ton = b"\x01\x02\x03\x04"
    browser = FalscherBrowser([{"type": "websocket.receive", "bytes": ton}])
    oben = FalscheGegenstelle()
    lage = sitzung.Lage()

    aufgabe = asyncio.create_task(sitzung._browser_nach_openai(browser, oben, lage))
    await asyncio.sleep(0.05)
    aufgabe.cancel()

    angehaengt = oben.gesendet_vom_typ("input_audio_buffer.append")
    assert len(angehaengt) == 1
    assert base64.b64decode(angehaengt[0]["audio"]) == ton
    assert lage.rahmen_hin == 1


@pytest.mark.asyncio
async def test_an_oversized_frame_is_dropped_and_does_not_end_the_session() -> None:
    """Ein zu grosser Rahmen kostet den Rahmen, nicht das Gespräch."""
    zu_gross = b"\x00" * (sitzung.MAX_TONRAHMEN_BYTES + 1)
    gut = b"\x11\x22"
    browser = FalscherBrowser([
        {"type": "websocket.receive", "bytes": zu_gross},
        {"type": "websocket.receive", "bytes": gut},
    ])
    oben = FalscheGegenstelle()
    lage = sitzung.Lage()

    aufgabe = asyncio.create_task(sitzung._browser_nach_openai(browser, oben, lage))
    await asyncio.sleep(0.05)
    aufgabe.cancel()

    angehaengt = oben.gesendet_vom_typ("input_audio_buffer.append")
    assert len(angehaengt) == 1
    assert base64.b64decode(angehaengt[0]["audio"]) == gut


@pytest.mark.asyncio
async def test_the_browser_may_interrupt_and_nothing_else() -> None:
    """Der Browser steuert den Redefluss — er diktiert nicht die Sitzung.

    Der zweite Teil ist der wichtigere: ein durchgereichtes ``session.update``
    aus dem Browser hiesse, dass die Werkzeugliste und die Anweisungen aus dem
    Browser stammen könnten. Genau dafür sitzt das Panel in der Mitte.
    """
    browser = FalscherBrowser([
        {"type": "websocket.receive", "text": json.dumps({"art": "unterbrechen"})},
        {"type": "websocket.receive", "text": json.dumps({
            "type": "session.update", "session": {"tools": [{"name": "boesartig"}]},
        })},
        {"type": "websocket.receive", "text": "kein json"},
        {"type": "websocket.receive", "text": json.dumps({"art": "gibtsnicht"})},
    ])
    oben = FalscheGegenstelle()
    lage = sitzung.Lage()

    aufgabe = asyncio.create_task(sitzung._browser_nach_openai(browser, oben, lage))
    await asyncio.sleep(0.05)
    aufgabe.cancel()

    assert len(oben.gesendet_vom_typ("response.cancel")) == 1
    assert oben.gesendet_vom_typ("session.update") == [], (
        "Ein session.update aus dem Browser ist bei der Gegenstelle angekommen."
    )
    # Und der Unsinn hat die Pumpe nicht umgebracht.
    assert len(oben.gesendet) == 1


# ── Die Pumpe zum Browser ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ereignisname",
    ["response.output_audio.delta", "response.audio.delta"],
)
async def test_both_spellings_of_the_audio_event_produce_sound(ereignisname: str) -> None:
    """Der Name hat sich beim Wechsel zur allgemeinen Verfügbarkeit geändert.

    Ein falscher Name fällt nicht als Fehler auf, sondern als **Stille** — und
    Stille sieht aus wie ein kaputtes Mikrofon, wie ein kaputter Lautsprecher
    und wie ein leeres Kontingent zugleich. Deshalb werden beide Schreibweisen
    angenommen, und deshalb steht das hier fest.
    """
    ton = b"\xaa\xbb\xcc"
    browser = FalscherBrowser()
    lage = sitzung.Lage()
    await sitzung._ereignis_verarbeiten(
        {"type": ereignisname, "delta": base64.b64encode(ton).decode()},
        browser, FalscheGegenstelle(), lage, None,
    )
    assert browser.binaer == [ton]
    assert lage.rahmen_zurueck == 1


@pytest.mark.asyncio
async def test_the_provider_wording_never_reaches_the_browser() -> None:
    """Eine Fehlermeldung des Anbieters kann verraten, was MSM ihm geschickt hat.

    Modellname, Kontingentstand, im schlechtesten Fall Teile der Anweisungen.
    Der Benutzer erfährt, dass es hakte; woran, erfährt das Protokoll.
    """
    browser = FalscherBrowser()
    await sitzung._ereignis_verarbeiten(
        {
            "type": "error",
            "error": {
                "code": "insufficient_quota",
                "message": "Your key sk-abc123 has no credit left for gpt-realtime-2.1",
            },
        },
        browser, FalscheGegenstelle(), sitzung.Lage(), None,
    )
    assert browser.art("stoerung") == [{"art": "stoerung"}]
    gesagtes = json.dumps(browser.texte)
    assert "sk-abc123" not in gesagtes
    assert "credit" not in gesagtes
    assert "gpt-realtime" not in gesagtes


@pytest.mark.asyncio
@pytest.mark.parametrize("kennung", sorted(sitzung._UNKRITISCHE_FEHLER))
async def test_an_error_our_own_normal_operation_causes_is_not_a_fault(
    kennung: str,
) -> None:
    """Der gemeldete Anlass: „Sprachverbindung verloren" bei stehender Leitung.

    Ausgeloest hat es das Dazwischenreden. Der Browser schickt dabei ein
    ``response.cancel``, und er schickt es auch dann, wenn gerade keine Antwort
    laeuft — zwischen dem letzten Tonrahmen und seiner Anzeige liegt ein
    Netzsprung, er kann es nicht wissen. Die Gegenstelle antwortet mit
    ``response_cancel_not_allowed``, und daraus wurde eine Stoerungsmeldung
    mitten in einem Gespraech, das ungestoert weiterlief.

    Die beiden anderen sind dieselbe Sorte Rennen um Millisekunden. Alle drei
    stehen im Protokoll, weil eine Haeufung sehr wohl ein Befund waere — auf dem
    Schirm des Sprechenden haben sie nichts verloren.
    """
    browser = FalscherBrowser()

    await sitzung._ereignis_verarbeiten(
        {"type": "error", "error": {"code": kennung, "message": "irgendwas"}},
        browser, FalscheGegenstelle(), sitzung.Lage(), None,
    )

    assert browser.texte == [], f"{kennung} wurde als Stoerung gemeldet"


@pytest.mark.asyncio
@pytest.mark.parametrize("kennung", ["server_error", "session_expired", "unbekannt"])
async def test_every_other_error_still_reaches_the_browser(kennung: str) -> None:
    """Die Gegenprobe, und sie ist die wichtigere Haelfte.

    Eine Ausnahmeliste, die zu weit wird, macht aus jedem Fehler Stille — und
    Stille ist im Sprachmodus die schlechteste Fehlermeldung, weil sie
    aussieht wie ein kaputtes Mikrofon. Was nicht ausdruecklich als
    Normalbetrieb benannt ist, bleibt eine Stoerung.
    """
    browser = FalscherBrowser()

    await sitzung._ereignis_verarbeiten(
        {"type": "error", "error": {"code": kennung}},
        browser, FalscheGegenstelle(), sitzung.Lage(), None,
    )

    assert browser.art("stoerung") == [{"art": "stoerung"}]


@pytest.mark.asyncio
async def test_transcripts_reach_the_browser_in_both_directions() -> None:
    browser = FalscherBrowser()
    leer = FalscheGegenstelle()
    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "  Wie laeuft mein Server?  "},
        browser, leer, sitzung.Lage(), None,
    )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.output_audio_transcript.delta", "delta": "Er laeuft."},
        browser, leer, sitzung.Lage(), None,
    )
    assert browser.art("gehoert") == [{"art": "gehoert", "text": "Wie laeuft mein Server?"}]
    assert browser.art("antworttext") == [{"art": "antworttext", "text": "Er laeuft."}]


class FalschesKontingent:
    """Zählt mit und sagt auf Wunsch Nein."""

    def __init__(self, weiter: bool = True) -> None:
        self.weiter = weiter
        self.meldungen: list[dict] = []

    def melden(self, usage: dict) -> bool:
        self.meldungen.append(usage)
        return self.weiter


@pytest.mark.asyncio
async def test_usage_is_reported_to_the_quota() -> None:
    """Bei 64 USD je Million Ausgabetokens ist Mitzählen keine Formalie."""
    lage = sitzung.Lage()
    browser = FalscherBrowser()
    kontingent = FalschesKontingent()
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {"usage": {"total_tokens": 1234}}},
        browser, FalscheGegenstelle(), lage, None, kontingent,
    )
    assert kontingent.meldungen == [{"total_tokens": 1234}]
    assert lage.offen is True
    assert browser.art("zustand")[-1]["zustand"] == sitzung.ZUSTAND_BEREIT


@pytest.mark.asyncio
async def test_an_exhausted_quota_ends_the_session_after_the_sentence() -> None:
    """Erst „bereit", dann Schluss — der angefangene Satz ist bezahlt."""
    lage = sitzung.Lage()
    browser = FalscherBrowser()
    oben = FalscheGegenstelle()
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {"usage": {"total_tokens": 999_999}}},
        browser, oben, lage, None, FalschesKontingent(weiter=False),
    )
    assert lage.kontingent_aus is True
    assert lage.offen is False
    assert browser.art("kontingent") == [{"art": "kontingent"}]
    assert oben.geschlossen is True


@pytest.mark.asyncio
async def test_without_a_quota_the_session_simply_runs() -> None:
    """Kein Kontingentobjekt heisst nicht „null Tokens frei"."""
    lage = sitzung.Lage()
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {"usage": {"total_tokens": 5}}},
        FalscherBrowser(), FalscheGegenstelle(), lage, None, None,
    )
    assert lage.offen is True
    assert lage.kontingent_aus is False


@pytest.mark.asyncio
async def test_an_unknown_event_does_not_break_anything() -> None:
    """Ein Anbieter, der ein Ereignis hinzufügt, darf keine Sitzung abreissen."""
    browser = FalscherBrowser()
    await sitzung._ereignis_verarbeiten(
        {"type": "response.irgendwas.neues", "delta": "x"},
        browser, FalscheGegenstelle(), sitzung.Lage(), None,
    )
    assert browser.texte == []
    assert browser.binaer == []


# ── Was gesprochen wurde, bleibt ──────────────────────────────────────────
#
# Der Modulkopf von `ai_voice_session` versprach das lange, bevor es stimmte:
# die Sitzung schrieb nichts, und nach dem Auflegen war das Gespraech weg. Diese
# Tests halten fest, dass es jetzt stimmt — und dass ein Fehlschlag beim
# Schreiben einen Satz kostet und nicht das Gespraech.


def _unterhaltung(db: Session, user: User) -> AiConversation:
    """Dieselbe Unterhaltung, in der auch der getippte Chat steht."""
    zeile = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Sprache"
    )
    db.add(zeile)
    db.commit()
    return zeile


def _nachrichten(db: Session, gespraech_id: str) -> list[tuple[str, str]]:
    db.expire_all()
    return [
        (zeile.role, zeile.content)
        for zeile in db.query(AiMessage)
        .filter(AiMessage.conversation_id == gespraech_id)
        .all()
    ]


@pytest.mark.asyncio
async def test_both_sides_of_the_conversation_end_up_in_the_history(
    db: Session, regular_user: User
) -> None:
    """Es ist derselbe Chat, nur ein anderer Eingang — also derselbe Verlauf.

    Der Mensch geht mit dem fertigen Transkript hinein, die KI mit dem, was aus
    den Deltas einer Antwort zusammengesetzt wurde. Zusammengesetzt und
    **einmal** geschrieben: ein Delta je Nachricht ergaebe einen Verlauf aus
    Silben.
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "  Wie laeuft mein Server?  "},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    for stueck in ("Er laeuft. ", "Seit drei Tagen."):
        await sitzung._ereignis_verarbeiten(
            {"type": "response.output_audio_transcript.delta", "delta": stueck},
            browser, leer, lage, None, gespraech_id=gespraech.id,
        )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {}},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )

    assert sorted(_nachrichten(db, gespraech.id)) == [
        ("assistant", "Er laeuft. Seit drei Tagen."),
        ("user", "Wie laeuft mein Server?"),
    ]
    # Und der Puffer ist leer, sonst klebte der Satz vorn an der naechsten
    # Antwort und stuende ein zweites Mal im Verlauf.
    assert lage.antworttext == ""


@pytest.mark.asyncio
async def test_the_question_stays_above_the_answer_even_when_it_arrives_late(
    db: Session, regular_user: User
) -> None:
    """Die Eingabetranskription laeuft nebenher — die Reihenfolge nicht.

    Bei OpenAI Realtime ist `conversation.item.input_audio_transcription.
    completed` **nicht** mit den `response.*`-Ereignissen synchronisiert. Bei
    einer kurzen Frage und einer schnellen Antwort trifft `response.done`
    zuerst ein; wer dann den Schreibzeitpunkt als `created_at` nimmt, bekommt
    einen Verlauf, in dem die Antwort ueber der Frage steht.

    Das faellt nicht nur im Panel auf: `build_provider_messages` sortiert nach
    derselben Spalte und reichte die verdrehte Reihenfolge im naechsten Prompt
    weiter — das Modell saehe sich antworten, bevor gefragt wurde.

    Deshalb traegt der Zug des Menschen den Stempel vom Ende seines Sprechens.
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    # Der Mensch hoert auf zu reden — hier entsteht sein Zeitstempel.
    await sitzung._ereignis_verarbeiten(
        {"type": "input_audio_buffer.speech_stopped"},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    assert lage.gesprochen_bis is not None

    # Die Antwort ist fertig, bevor das Transkript da ist.
    await sitzung._ereignis_verarbeiten(
        {"type": "response.output_audio_transcript.delta", "delta": "Er laeuft."},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {}},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    # Und erst jetzt das Transkript.
    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "Wie laeuft mein Server?"},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )

    db.expire_all()
    verlauf = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == gespraech.id)
        .order_by(AiMessage.created_at)
        .all()
    )
    assert [zeile.role for zeile in verlauf] == ["user", "assistant"]
    # Und der Stempel wird nicht ein zweites Mal vergeben: der naechste Zug
    # holt sich seinen eigenen.
    assert lage.gesprochen_bis is None


@pytest.mark.asyncio
async def test_a_second_response_does_not_repeat_the_first(
    db: Session, regular_user: User
) -> None:
    """Der Puffer wird geleert, nicht nur ausgelesen.

    Ein `response.done` ohne Text danach schreibt gar nichts — reine
    Werkzeugrunden erzeugen genau das, und eine leere Nachricht ist keine.
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    await sitzung._ereignis_verarbeiten(
        {"type": "response.output_audio_transcript.delta", "delta": "Fertig."},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    for _ in range(2):
        await sitzung._ereignis_verarbeiten(
            {"type": "response.done", "response": {}},
            browser, leer, lage, None, gespraech_id=gespraech.id,
        )

    assert _nachrichten(db, gespraech.id) == [("assistant", "Fertig.")]


@pytest.mark.asyncio
async def test_what_the_human_says_is_redacted_before_it_is_stored(
    db: Session, regular_user: User
) -> None:
    """Dieselbe Asymmetrie wie im Chat: der Mensch geschwaerzt, die KI nicht.

    Wer ein Passwort ins Mikrofon spricht, bekommt es transkribiert — und ohne
    diese Zeile stuende es danach im Klartext in der Datenbank. In der Antwort
    der KI kann eine Zuweisung dagegen eine *Anleitung* sein, und `[REDACTED]`
    an dieser Stelle waere keine Antwort mehr (`_finalize_stream` entscheidet
    genauso).
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "Trag RCON_PASSWORD=hunter2 ein"},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )

    gespeichert = dict(_nachrichten(db, gespraech.id))["user"]
    assert "hunter2" not in gespeichert
    assert "[REDACTED]" in gespeichert


@pytest.mark.asyncio
async def test_a_runaway_answer_does_not_blow_up_the_history(
    db: Session, regular_user: User
) -> None:
    """Der Deckel greift beim Sammeln und nicht erst beim Schreiben.

    Ein Modell, das nicht aufhoert zu reden, soll den Speicher der Sitzung nicht
    in dem Tempo belegen, in dem es spricht — und den Verlauf des getippten
    Chats schon gar nicht sprengen.
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    for _ in range(20):
        await sitzung._ereignis_verarbeiten(
            {"type": "response.output_audio_transcript.delta", "delta": "wort " * 200},
            browser, leer, lage, None, gespraech_id=gespraech.id,
        )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {}},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )

    (_rolle, text), = _nachrichten(db, gespraech.id)
    assert len(text) <= sitzung.MAX_NACHRICHT_ZEICHEN


@pytest.mark.asyncio
async def test_a_failed_write_costs_a_sentence_and_not_the_conversation(
    monkeypatch: pytest.MonkeyPatch, db: Session, regular_user: User
) -> None:
    """Der Mensch redet gerade. Ihm die Leitung zu kappen, weil eine Zeile nicht
    in die Datenbank ging, waere die schlechtere Antwort.

    Geprueft wird beides: dass die Ausnahme nicht durchschlaegt **und** dass der
    Browser danach weiterbedient wird — sonst waere „das Gespraech laeuft
    weiter" eine Behauptung ohne Beleg.
    """
    gespraech = _unterhaltung(db, regular_user)

    def _geht_nicht(*_args, **_kwargs) -> None:
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(sitzung, "_nachricht_ablegen", _geht_nicht)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "Wie laeuft mein Server?"},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.output_audio_transcript.delta", "delta": "Er laeuft."},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )
    await sitzung._ereignis_verarbeiten(
        {"type": "response.done", "response": {}},
        browser, leer, lage, None, gespraech_id=gespraech.id,
    )

    assert lage.offen is True
    assert browser.art("gehoert") == [{"art": "gehoert", "text": "Wie laeuft mein Server?"}]
    assert browser.art("zustand")[-1]["zustand"] == sitzung.ZUSTAND_BEREIT
    assert _nachrichten(db, gespraech.id) == []


@pytest.mark.asyncio
async def test_without_a_conversation_the_session_simply_speaks(
    db: Session, regular_user: User
) -> None:
    """Ohne Kennung wird nichts geschrieben — der Vorgabewert von `fuehren`.

    Das ist kein Ausnahmefall, sondern die Bedingung dafuer, dass die Tests der
    Sitzungsmechanik ohne Datenbank auskommen: die Reihenfolge zweier Ereignisse
    laesst sich pruefen, ohne eine Unterhaltung anzulegen.
    """
    gespraech = _unterhaltung(db, regular_user)
    browser, leer, lage = FalscherBrowser(), FalscheGegenstelle(), sitzung.Lage()

    await sitzung._ereignis_verarbeiten(
        {"type": sitzung._EINGABETEXT_FERTIG, "transcript": "Hallo?"},
        browser, leer, lage, None,
    )

    assert browser.art("gehoert") == [{"art": "gehoert", "text": "Hallo?"}]
    assert _nachrichten(db, gespraech.id) == []


# ── Der Ablauf im Ganzen ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configuration_and_history_go_out_before_the_first_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reihenfolge ist hier eine Zusage, kein Stil.

    Ton, der vor dem ``session.update`` ankommt, trifft eine Sitzung ohne
    Anweisungen und ohne Werkzeuge. Das Modell antwortete dann als beliebiger
    Assistent — nicht als das Panel.
    """
    oben = FalscheGegenstelle()
    monkeypatch.setattr(
        sitzung, "verbinden", lambda adresse, schluessel: _sofort(oben)
    )
    browser = FalscherBrowser()
    konfig = sitzung.sitzungskonfiguration(modell="m", anweisungen="a", stimme="alloy")

    await sitzung.fuehren(
        browser,
        adresse="wss://beispiel/realtime",
        schluessel="sk-test",
        konfiguration=konfig,
        verlauf=[sitzung.verlaufseintrag("user", "Hallo")],
        hoechstdauer=0.15,
    )

    typen = [eintrag["type"] for eintrag in oben.gesendet]
    assert typen[0] == "session.update"
    assert typen[1] == "conversation.item.create"
    assert oben.geschlossen, "Die Verbindung nach oben blieb offen."


@pytest.mark.asyncio
async def test_the_session_ends_by_itself_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Höchstdauer ist der Ablauf des Access-Tokens, nicht Vorsicht.

    Ein WebSocket prüft die Anmeldung nur beim Handshake. Eine Verbindung über
    Stunden umginge damit sowohl den Ablauf als auch die jti-Sperrliste — wer
    abgemeldet wird, spräche weiter. Der Browser erfährt den Grund, damit er
    neu verbindet statt eine Störung zu melden.
    """
    oben = FalscheGegenstelle()
    monkeypatch.setattr(sitzung, "verbinden", lambda adresse, schluessel: _sofort(oben))
    browser = FalscherBrowser()

    lage = await sitzung.fuehren(
        browser,
        adresse="wss://beispiel/realtime",
        schluessel="sk-test",
        konfiguration=sitzung.sitzungskonfiguration(
            modell="m", anweisungen="a", stimme="alloy"
        ),
        verlauf=[],
        hoechstdauer=0.15,
    )

    assert browser.art("abgelaufen"), "Der Browser erfuhr nicht, warum Schluss war."
    assert lage.offen is False


async def _sofort(wert):
    """Ein fertiges Ergebnis als Coroutine — für `monkeypatch` auf `verbinden`."""
    return wert


# ── Die Probe für den Einstellungsdialog ──────────────────────────────────
#
# Der Chattest schickt ein „ping" an `/chat/completions`. Bei einem
# Realtime-Modell antwortet OpenAI darauf woertlich *„This is not a chat model
# and thus not supported in the v1/chat/completions endpoint"* — eine
# Fehlermeldung fuer einen voellig richtig eingerichteten Zugang. Deshalb hat
# der Sprachweg seine eigene Probe.


class ProbeGegenstelle(FalscheGegenstelle):
    """Eine Gegenstelle, die auf `recv()` ein Ereignis liefert."""

    def __init__(self, ereignis: dict | None = None) -> None:
        super().__init__()
        self._ereignis = ereignis or {"type": "session.created"}

    async def recv(self) -> str:
        return json.dumps(self._ereignis)


@pytest.mark.asyncio
async def test_the_probe_opens_and_hangs_up_again(monkeypatch) -> None:
    """Sie kostet nichts: eine Sitzung, die niemand bespricht, hat keine Tokens."""
    oben = ProbeGegenstelle()
    monkeypatch.setattr(sitzung, "verbinden", lambda *a, **k: _sofort(oben))

    await sitzung.pruefen("wss://beispiel/realtime?model=m", "sk-test")

    assert oben.geschlossen is True


@pytest.mark.asyncio
async def test_a_silent_provider_does_not_hang_the_probe(monkeypatch) -> None:
    """Wer nach dem Handschlag schweigt, blockiert den Dialog nicht."""
    class Stumm(FalscheGegenstelle):
        async def recv(self) -> str:
            await asyncio.sleep(3600)
            return ""

    oben = Stumm()
    monkeypatch.setattr(sitzung, "verbinden", lambda *a, **k: _sofort(oben))
    monkeypatch.setattr(sitzung, "PROBE_TIMEOUT", 0.05)

    await sitzung.pruefen("wss://beispiel/realtime?model=m", "sk-test")

    assert oben.geschlossen is True


class _Antwort:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Abgelehnt(Exception):
    def __init__(self, status_code: int) -> None:
        self.response = _Antwort(status_code)
        super().__init__(f"HTTP {status_code}")


@pytest.mark.parametrize(
    "fehler, erwartet",
    [
        (_Abgelehnt(401), "AI_PROVIDER_AUTH_FAILED"),
        (_Abgelehnt(403), "AI_PROVIDER_AUTH_FAILED"),
        # Bei OpenAI heisst das fast immer: die Modellkennung stimmt nicht. Die
        # Adresse baut MSM selbst, sie kann nicht danebenliegen.
        (_Abgelehnt(400), "AI_PROVIDER_REQUEST_REJECTED"),
        (_Abgelehnt(404), "AI_PROVIDER_REQUEST_REJECTED"),
        (_Abgelehnt(429), "AI_PROVIDER_RATE_LIMITED"),
        (_Abgelehnt(500), "AI_PROVIDER_UNAVAILABLE"),
        (asyncio.TimeoutError(), "AI_PROVIDER_STREAM_TIMEOUT"),
        (OSError("kein Netz"), "AI_PROVIDER_UNAVAILABLE"),
    ],
)
def test_the_probe_speaks_in_codes_the_panel_already_knows(fehler, erwartet) -> None:
    """Keine eigenen Codes fuer den Sprachweg — es sind dieselben drei Faelle.

    Falscher Schluessel, falsches Modell, nicht erreichbar. Ein zweiter Satz
    Codes waere eine zweite Wortwahl fuer dasselbe, und die Haelfte davon
    stuende in keiner Sprachdatei.
    """
    assert sitzung.probe_fehlercode(fehler) == erwartet


def test_the_probe_never_forwards_the_providers_wording() -> None:
    """Der Wortlaut kann Kontingentstaende und Kontonamen tragen."""
    fehler = _Abgelehnt(401)
    fehler.args = ("Incorrect API key provided: sk-abc***. Org org-geheim",)

    code = sitzung.probe_fehlercode(fehler)

    assert code == "AI_PROVIDER_AUTH_FAILED"
    assert "sk-abc" not in code and "org-geheim" not in code
