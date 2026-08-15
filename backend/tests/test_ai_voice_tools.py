"""Dieselben Werkzeuge, andere Huelle — und die Schranken, die sonst fehlten.

Der wichtigste Test dieser Datei ist der letzte Block. Im Chat fuehrt
`ai_stream_service.segment_ausfuehren` den Zug und zaehlt dabei mit: Runden,
gleiche Aufrufe, Gesamtzahl. In einer Realtime-Sitzung fuehrt das **Modell** den
Zug — und alles, was dort an der Schleife hing, haengt hier an nichts, ausser es
steht in `ai_voice_tools`.

Ein festgefahrenes Modell, das `read_server_logs` zweihundertmal ruft, ist im
Chat ein abgebrochener Lauf. Hier waere es ein Gespraech, das nicht mehr
aufhoert, auf einem Konto, das Audio mit 64 USD je Million Ausgabetokens
abrechnet.
"""

from __future__ import annotations

import json

import pytest

from services import ai_tool_registry, ai_voice_tools


# ── Die Huelle ────────────────────────────────────────────────────────────


def test_the_nested_chat_shape_becomes_the_flat_realtime_shape() -> None:
    """Die Schemata wandern unveraendert; nur die Huelle wird flach."""
    schema = {
        "type": "object",
        "properties": {"server_id": {"type": "integer"}},
        "required": ["server_id"],
        "additionalProperties": False,
    }
    flach = ai_voice_tools.fuer_realtime({
        "type": "function",
        "function": {
            "name": "read_server_status",
            "description": "Liest den Status.",
            "parameters": schema,
        },
    })

    assert flach == {
        "type": "function",
        "name": "read_server_status",
        "description": "Liest den Status.",
        "parameters": schema,
    }
    # Und zwar wirklich dasselbe Objekt, nicht eine umgebaute Kopie: ein
    # Schema, das unterwegs bearbeitet wird, ist ein zweiter Pflegeort.
    assert flach["parameters"] is schema


@pytest.mark.parametrize(
    "kaputt",
    [
        {},
        {"type": "function"},
        {"type": "function", "function": {}},
        {"type": "function", "function": {"name": ""}},
        {"type": "function", "function": "kein dict"},
    ],
)
def test_a_broken_definition_is_skipped_not_fatal(kaputt: dict) -> None:
    """Ein kaputter Eintrag kostet den Eintrag, nicht den Katalog."""
    assert ai_voice_tools.fuer_realtime(kaputt) is None


# ── Der Zuschnitt ─────────────────────────────────────────────────────────


def test_the_voice_set_is_written_out_and_contains_nothing_that_writes() -> None:
    """`SPRACHE_LESEN` ist eine Aufzaehlung, keine Ableitung.

    Das ist dieselbe Entscheidung wie bei `GUARDIAN_HEILUNG_TOOLS` und
    `AUFGABEN_LESEN`: ein kuenftiges Werkzeug soll sich nicht stillschweigend
    im Sprachweg wiederfinden. Wer eines aufnehmen will, schreibt es hin — und
    faellt dabei ueber diesen Test, wenn es ein schreibendes ist.
    """
    for name in ai_tool_registry.SPRACHE_LESEN:
        assert ai_tool_registry.bekannt(name), f"{name} fehlt in WERKZEUGE"
        art = ai_tool_registry.WERKZEUGE[name].art
        assert art not in ("server_write", "global_write"), (
            f"{name} schreibt und gehoert nicht in SPRACHE_LESEN — "
            "Schreibaktionen brauchen die gesprochene Bestaetigung."
        )


def test_ask_user_has_no_place_in_a_conversation() -> None:
    """`ask_user` stellt eine Karte mit Knoepfen hin. Niemand hoert eine Karte.

    Im Sprachmodus fragt das Modell, indem es fragt. Dasselbe Argument wie bei
    `AUFGABEN_LESEN`, nur aus dem anderen Grund: dort sitzt niemand davor, hier
    sitzt jemand davor und sieht nicht hin.
    """
    assert "ask_user" not in ai_tool_registry.SPRACHE_LESEN


def test_learning_a_skill_is_not_something_you_say_in_passing() -> None:
    """Gelesen ja, gelernt nein.

    Ein Skill ist Prosa, die kuenftige Laeufe anleitet. Was jemand nebenbei ins
    Mikrofon sagt, soll nicht dauerhaft die Arbeitsweise der KI aendern.
    """
    assert "read_skill" in ai_tool_registry.SPRACHE_LESEN
    assert "learn_skill" not in ai_tool_registry.SPRACHE_LESEN
    assert "forget_skill" not in ai_tool_registry.SPRACHE_LESEN


def test_the_catalog_is_cut_by_rights_and_by_the_voice_set(monkeypatch) -> None:
    """Zwei Schnitte, beide nehmen weg — Rechte und Tauglichkeit."""
    monkeypatch.setattr(
        ai_voice_tools, "provider_tool_definitions",
        lambda: [
            {"type": "function", "function": {"name": "read_server_status", "parameters": {}}},
            {"type": "function", "function": {"name": "ask_user", "parameters": {}}},
            {"type": "function", "function": {"name": "propose_backup", "parameters": {}}},
            {"type": "function", "function": {"name": "propose_server_delete", "parameters": {}}},
        ],
    )
    # Der Benutzer duerfte alle vier — die Auswahl kommt hier allein aus
    # `SPRACHE_LESEN` und `SPRACHE_HANDELN`.
    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({
            "read_server_status", "ask_user", "propose_backup", "propose_server_delete",
        }),
    )

    namen = [w["name"] for w in ai_voice_tools.katalog(None, None)]

    assert "read_server_status" in namen
    # Aendern geht, mit gesprochener Bestaetigung.
    assert "propose_backup" in namen
    assert ai_voice_tools.BESTAETIGEN in namen
    # `ask_user` stellt eine Karte hin, die niemand hoert.
    assert "ask_user" not in namen
    # Und Loeschen ist per Stimme nicht bestaetigbar — es steht deshalb gar
    # nicht erst im Katalog, statt spaeter abgewiesen zu werden.
    assert "propose_server_delete" not in namen


def test_a_missing_right_removes_the_tool_even_if_it_is_a_voice_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_voice_tools, "provider_tool_definitions",
        lambda: [
            {"type": "function", "function": {"name": "read_server_status", "parameters": {}}},
            {"type": "function", "function": {"name": "web_search", "parameters": {}}},
        ],
    )
    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({"read_server_status"}),
    )

    assert [w["name"] for w in ai_voice_tools.katalog(None, None)] == ["read_server_status"]


# ── Die Argumente ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ('{"server_id": 4}', {"server_id": 4}),
        ({"server_id": 4}, {"server_id": 4}),
        ("", {}),
        ("   ", {}),
        ("kein json", {}),
        ("[1,2,3]", {}),
        ("null", {}),
        (None, {}),
        (42, {}),
    ],
)
def test_arguments_are_read_leniently(roh, erwartet) -> None:
    """Nachsichtig lesen, streng verwenden.

    Ein Formfehler kostet eine Runde — das Werkzeug weist die fehlenden
    Pflichtfelder selbst ab, mit einer Meldung, die das Modell beantworten kann.
    Ein Abbruch kostete das Gespraech.
    """
    assert ai_voice_tools._argumente_lesen(roh) == erwartet


# ── Die Schranken ─────────────────────────────────────────────────────────


def test_the_same_call_three_times_is_enough() -> None:
    """Beim vierten Mal gibt es eine Antwort statt einer Ausfuehrung."""
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    argumente = {"server_id": 4}

    for _ in range(ai_voice_tools.MAX_GLEICHE_AUFRUFE):
        assert bruecke.darf("read_server_logs", argumente) is None
        bruecke.vermerken("read_server_logs", argumente)

    grund = bruecke.darf("read_server_logs", argumente)
    assert grund is not None
    assert "read_server_logs" in grund


def test_different_arguments_are_a_different_call() -> None:
    """Die Schranke trifft die Schleife, nicht die Arbeit.

    Denselben Server dreimal abzufragen ist eine Schleife. Drei verschiedene
    Server abzufragen ist eine Antwort auf „wie laufen meine Server?".
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    for nummer in (1, 2, 3, 4, 5):
        assert bruecke.darf("read_server_status", {"server_id": nummer}) is None
        bruecke.vermerken("read_server_status", {"server_id": nummer})


def test_a_session_has_an_overall_ceiling() -> None:
    """Wer nach 32 Aufrufen nicht fertig ist, ist in einer Schleife.

    Im Chat begrenzt dieselbe Zahl **einen Zug**, und zwischen zwei Zuegen tippt
    ein Mensch. Hier liegt zwischen zwei Zuegen nur eine Sprechpause — deshalb
    gilt sie fuer die ganze Sitzung.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    for nummer in range(ai_voice_tools.MAX_AUFRUFE_JE_SITZUNG):
        assert bruecke.darf("read_server_status", {"server_id": nummer}) is None
        bruecke.vermerken("read_server_status", {"server_id": nummer})

    grund = bruecke.darf("read_server_status", {"server_id": 999})
    assert grund is not None
    assert "Sprachsitzung" in grund


def test_a_blocked_call_answers_instead_of_breaking_off() -> None:
    """„Nicht ausgefuehrt, aber beantwortet" — die etablierte Form.

    Ein Abbruch mitten im Gespraech waere fuer den Sprechenden ein Aussetzer
    ohne Erklaerung. Das Modell bekommt stattdessen einen Satz, den es vorlesen
    oder beherzigen kann.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    bruecke._gesamt = ai_voice_tools.MAX_AUFRUFE_JE_SITZUNG

    ergebnis = bruecke.ausfuehren("read_server_status", {"server_id": 1})

    assert isinstance(ergebnis, dict)
    assert "error" in ergebnis


# ── Die Laenge des Ergebnisses ────────────────────────────────────────────


def test_a_short_result_passes_through_untouched() -> None:
    assert ai_voice_tools._gekuerzt({"status": "laeuft"}) == {"status": "laeuft"}


def test_a_long_result_is_cut_because_it_gets_read_aloud() -> None:
    """Was hier zurueckkommt, wird vorgelesen.

    Ein Modell, das 16.000 Zeichen Log bekommt, fasst zusammen — und je mehr es
    zusammenfassen muss, desto mehr erfindet es dabei. Die Kuerzung sagt
    ausserdem, **dass** gekuerzt wurde: ein stillschweigend halbes Ergebnis
    waere eine falsche Auskunft unter dem richtigen Namen.
    """
    lang = {"zeilen": ["x" * 200 for _ in range(100)]}

    gekuerzt = ai_voice_tools._gekuerzt(lang)

    assert gekuerzt["gekuerzt"] is True
    assert "hinweis" in gekuerzt
    assert len(gekuerzt["anfang"]) == ai_voice_tools.MAX_ERGEBNIS_ZEICHEN


# ── Der Weg ueber die Leitung ─────────────────────────────────────────────


class FalscheGegenstelle:
    def __init__(self) -> None:
        self.gesendet: list[dict] = []

    async def send(self, rohtext: str) -> None:
        self.gesendet.append(json.loads(rohtext))


class FalscherBrowser:
    def __init__(self) -> None:
        self.texte: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.texte.append(json.loads(text))


@pytest.mark.asyncio
async def test_the_tool_name_is_remembered_from_the_item_and_used_later(
    monkeypatch,
) -> None:
    """Die Gegenstelle nennt den Namen **einmal**, beim Anlegen des Elements.

    Danach schiebt sie nur noch Argumente nach. Wer sich den Namen dort nicht
    merkt, bekommt am Ende Argumente ohne Werkzeug — und der Aufruf verschwindet
    lautlos.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: {"gerufen": name, "mit": argumente},
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "item_7", "name": "list_my_servers"},
    }, oben, browser)
    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "call_7",
        "item_id": "item_7",
        "arguments": "{}",
    }, oben, browser)

    ergebnisse = [e for e in oben.gesendet if e["type"] == "conversation.item.create"]
    assert len(ergebnisse) == 1
    element = ergebnisse[0]["item"]
    assert element["type"] == "function_call_output"
    assert element["call_id"] == "call_7"
    assert json.loads(element["output"])["gerufen"] == "list_my_servers"

    # Ohne das zweite `response.create` bleibt das Ergebnis liegen und das
    # Modell schweigt.
    assert oben.gesendet[-1]["type"] == "response.create"


@pytest.mark.asyncio
async def test_the_browser_sees_the_name_and_never_the_arguments(monkeypatch) -> None:
    """Argumente tragen Serverkennungen und Pfade.

    Der Sprechende soll sehen, woran gearbeitet wird — nicht woran genau. Eine
    Anzeige, die nebenbei mitlaeuft, ist der falsche Ort fuer Betriebsdaten.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren", lambda self, name, argumente: {"ok": True}
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "i1", "name": "read_config"},
    }, oben, browser)
    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c1", "item_id": "i1",
        "arguments": json.dumps({"server_id": 42, "path": "/srv/geheim/server.properties"}),
    }, oben, browser)

    assert browser.texte == [{"art": "werkzeug", "name": "read_config"}]
    angezeigt = json.dumps(browser.texte)
    assert "42" not in angezeigt
    assert "geheim" not in angezeigt


@pytest.mark.asyncio
async def test_an_unnamed_call_is_dropped_and_not_guessed(monkeypatch) -> None:
    """Ohne Namen wird nicht geraten.

    Ein geratener Werkzeugname waere die schlimmste Sorte Fehler: er liefe
    durch, tut etwas anderes als gemeint, und meldet Erfolg.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    gerufen: list[str] = []
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: gerufen.append(name),
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c_unbekannt", "item_id": "i_unbekannt", "arguments": "{}",
    }, oben, browser)

    assert gerufen == []
    assert oben.gesendet == []


@pytest.mark.asyncio
async def test_unrelated_events_pass_through_without_effect() -> None:
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({"type": "response.output_audio.delta"}, oben, browser)
    await bruecke.ereignis({"type": "irgendwas.neues"}, oben, browser)

    assert oben.gesendet == []
    assert browser.texte == []


# ── Die gesprochene Bestaetigung ──────────────────────────────────────────


def test_nothing_irreversible_can_be_confirmed_by_voice() -> None:
    """Die eine Zusage, die diese ganze Funktion traegt.

    Eine gesprochene Zustimmung kann missverstanden werden, im Hintergrund kann
    jemand anders „ja" sagen, und der Beweis im Audit ist ein Transkript statt
    einer Betaetigung. Fuer alles, wovon es keinen Weg zurueck gibt, ist das zu
    wenig — dort bleibt die Karte Pflicht.
    """
    ueberschneidung = ai_tool_registry.SPRACHE_HANDELN & ai_tool_registry.ALWAYS_CONFIRM_TOOLS
    assert not ueberschneidung, (
        f"Per Stimme bestaetigbar, obwohl unumkehrbar: {sorted(ueberschneidung)}"
    )
    # Namentlich, damit ein Umbau an `ALWAYS_CONFIRM_TOOLS` diesen Test nicht
    # still entwertet.
    for name in ("propose_server_delete", "propose_backup_restore",
                 "propose_hoster_integration", "propose_ai_tarif_role"):
        assert name not in ai_tool_registry.SPRACHE_HANDELN


def test_every_voice_write_tool_has_a_sentence_to_read_out() -> None:
    """Ohne Satz keine Ruecklesung — und ohne Ruecklesung keine Bestaetigung.

    Ein Schreibwerkzeug ohne Zeile in `_VORLESEN` fiele auf den allgemeinen
    Rueckfall zurueck („Eine Aenderung ausfuehren"), und der Mensch stimmte
    etwas zu, das er nicht gehoert hat.
    """
    ohne = ai_tool_registry.SPRACHE_HANDELN - set(ai_voice_tools._VORLESEN)
    assert not ohne, f"Kein Vorlesetext fuer: {sorted(ohne)}"


@pytest.mark.parametrize(
    "werkzeug, vorschau, erwartet",
    [
        ("propose_server_lifecycle", {"operation": "restart"}, "srv neu starten"),
        ("propose_server_lifecycle", {"operation": "stop"}, "srv stoppen"),
        ("propose_backup", {}, "Ein Backup von srv anlegen"),
        ("propose_config_patch", {"path": "a.txt", "edits": 1},
         "Eine Stelle in der Datei a.txt auf srv ändern"),
        ("propose_config_patch", {"path": "a.txt", "edits": 3},
         "3 Stellen in der Datei a.txt auf srv ändern"),
        ("propose_bind_ip_update", {"bind_ip": "10.0.0.5"},
         "Die Bind-IP von srv auf 10.0.0.5 ändern"),
    ],
)
def test_the_sentence_says_what_actually_happens(werkzeug, vorschau, erwartet) -> None:
    """Der Satz kommt von MSM, nicht vom Modell.

    Das ist der ganze Punkt der Ruecklesung: wuerde das Modell formulieren, was
    es gleich tut, waere die Zustimmung eine zu seiner Erzaehlung und nicht zu
    seiner Handlung.
    """
    assert ai_voice_tools.vorlesetext(werkzeug, vorschau, "srv") == erwartet


def test_a_missing_server_name_becomes_an_honest_placeholder() -> None:
    """Ein Satz mit leerem Namen waere irrefuehrend, „diesem Server" nur unschoen."""
    assert "diesem Server" in ai_voice_tools.vorlesetext("propose_backup", {}, None)


def test_the_confirmation_tool_does_not_exist_in_the_typed_chat() -> None:
    """Es steht nicht in der Registry und nicht in `provider_tool_definitions`.

    Damit kann der getippte Chat es nicht sehen — nicht, weil es dort
    weggefiltert wird, sondern weil es dort nicht existiert. Ein Filter waere
    eine Stelle, die jemand vergisst; das hier ist keine.
    """
    from services.ai_action_service import provider_tool_definitions as echte

    assert not ai_tool_registry.bekannt(ai_voice_tools.BESTAETIGEN)
    namen = {d["function"]["name"] for d in echte()}
    assert ai_voice_tools.BESTAETIGEN not in namen


def test_the_confirmation_tool_appears_only_when_writing_is_offered(monkeypatch) -> None:
    """Wer nichts aendern darf, braucht nichts zu bestaetigen."""
    monkeypatch.setattr(
        ai_voice_tools, "provider_tool_definitions",
        lambda: [
            {"type": "function", "function": {"name": "read_server_status", "parameters": {}}},
            {"type": "function", "function": {"name": "propose_backup", "parameters": {}}},
        ],
    )

    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({"read_server_status"}),
    )
    nur_lesen = [w["name"] for w in ai_voice_tools.katalog(None, None)]
    assert ai_voice_tools.BESTAETIGEN not in nur_lesen

    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({"read_server_status", "propose_backup"}),
    )
    mit_handeln = [w["name"] for w in ai_voice_tools.katalog(None, None)]
    assert ai_voice_tools.BESTAETIGEN in mit_handeln
    assert "propose_backup" in mit_handeln


def test_a_second_proposal_is_refused_while_one_is_waiting() -> None:
    """Ein Ja gilt genau einem Vorschlag.

    Damit gibt es die ganze Klasse „das Ja landete auf dem falschen Vorschlag"
    nicht — weder durch ein Missverstaendnis des Modells noch dadurch, dass
    jemand zwei Saetze hintereinander sagt.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    bruecke.offener_vorschlag = "vorhandener-vorschlag"

    ergebnis = bruecke._vorschlagen("propose_backup", {"server_id": 1})

    assert "error" in ergebnis
    assert "noch ein Vorschlag" in ergebnis["error"]


def test_a_yes_for_a_different_proposal_is_refused() -> None:
    """Nicht der von vorhin, und keiner aus dem Verlauf abgeschrieben."""
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    bruecke.offener_vorschlag = "der-richtige"

    assert "error" in bruecke._bestaetigen("ein-anderer")
    assert "error" in bruecke._bestaetigen("")

    # Und der offene Vorschlag ist dadurch nicht verlorengegangen.
    assert bruecke.offener_vorschlag == "der-richtige"


def test_confirming_without_an_open_proposal_is_refused() -> None:
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    assert "error" in bruecke._bestaetigen("irgendeine-kennung")
