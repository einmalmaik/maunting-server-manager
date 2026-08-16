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

**Drei Werkzeuge gibt es nur hier**, und alle drei ersetzen etwas, das im
Sprachmodus fehlt: `bestaetige_vorschlag` den Klick auf die Karte,
`set_ai_autonomy` den Schalter in den Einstellungen, `zeige_beleg` den Blick auf
den Bildschirm. Dass der getippte Chat sie **nicht** sieht, ist keine Feinheit
der Katalogfilterung, sondern die Sicherheitszusage dahinter — sie steht weiter
unten als eigener Test.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiAutonomyGrant,
    AuditLog,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from models.ai_autonomy_grant import DEFAULT_MAX_ACTIONS_PER_HOUR
from services import ai_autonomy_service, ai_tool_registry, ai_voice_tools
from services.file_edit_service import content_revision
from services.role_service import set_user_roles


# ── Werkbank ──────────────────────────────────────────────────────────────
#
# `Bruecke` oeffnet ihre Datenbanksitzungen selbst — sie laeuft im Betrieb in
# einem Thread und bekommt deshalb keine gereicht. Die Tests darunter richten
# also den Bestand ein, committen und rufen dann so, wie die Gegenstelle es tut.


_KONFIGURATION = "port=2302\nmaxPlayers=40\n"


def _server(db: Session, tmp_path: Path) -> Server:
    verzeichnis = tmp_path / "sprachserver"
    verzeichnis.mkdir()
    zeile = Server(
        name="Sprachserver",
        game_type="dayz",
        install_dir=str(verzeichnis),
        container_name=f"msm-voice-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _rechte(
    db: Session,
    user: User,
    *,
    global_keys: tuple[str, ...] = (),
    server: Server | None = None,
    server_keys: tuple[str, ...] = (),
) -> None:
    rolle = Role(name=f"sprache-{uuid4().hex[:8]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in global_keys:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    if server is not None:
        for key in server_keys:
            db.add(ServerPermission(
                user_id=user.id, server_id=server.id, permission_key=key
            ))
        db.commit()


def _konfiguration(server: Server) -> Path:
    datei = Path(server.install_dir) / "server.cfg"
    datei.write_text(_KONFIGURATION, encoding="utf-8")
    return datei


def _patch_argumente(server: Server, datei: Path) -> dict:
    """Die Argumente einer Teilaenderung — umkehrbar, also autonomiefaehig.

    Bewusst ein Werkzeug, das wirklich etwas tut und dabei nur eine Datei
    anfasst: ein Test, der die Ausfuehrung wegmockt, wuerde die Zusage „laeuft
    sofort" nur behaupten.
    """
    return {
        "server_id": server.id,
        "path": "server.cfg",
        "expected_revision": content_revision(datei.read_bytes()),
        "edits": [{"find": "maxPlayers=40", "replace": "maxPlayers=60"}],
        "reason": "Der Benutzer will mehr Plaetze.",
        "expected_effect": "Es passen 60 Spieler auf den Server.",
    }


class FalscheRechte:
    """Ein Rechtedienst fuer die Katalogtests, der genau eine Frage beantwortet.

    `katalog` fragt seit dem Autonomieschalter die Datenbank, die
    Katalogtests fuehren aber keine mit — sie pruefen den Zuschnitt und nicht
    die Rechtevergabe. Ein Doppelgaenger statt einer Datenbank haelt sie bei
    ihrer Aussage.
    """

    def __init__(self, autonomie: bool = False) -> None:
        self.autonomie = autonomie

    def has_global_permission(self, _db, _user, key: str) -> bool:
        return self.autonomie and key == "ai.autonomous.use"


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
    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte())
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
    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte())
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

    namen = [w["name"] for w in ai_voice_tools.katalog(None, None)]

    assert "web_search" not in namen
    # Aus der Registry kommt genau das eine erlaubte Werkzeug. Der Rest des
    # Katalogs sind die sprachlokalen Werkzeuge, die es dort nicht gibt — sie
    # stehen namentlich weiter unten und nicht in dieser Zusage.
    assert [name for name in namen if ai_tool_registry.bekannt(name)] == [
        "read_server_status"
    ]


def test_the_autonomy_switch_is_only_offered_to_someone_who_may_use_it(
    monkeypatch,
) -> None:
    """Der Katalog ist keine Schranke — er soll trotzdem nicht luegen.

    Ein angebotenes `set_ai_autonomy` ohne `ai.autonomous.use` kostet eine
    Gespraechsrunde und endet in einer Absage, die der Sprechende sich anhoeren
    muss. Die Schranke selbst steht im Werkzeug und bleibt dort; hier geht es
    darum, sie nicht erst provozieren zu muessen.
    """
    monkeypatch.setattr(
        ai_voice_tools, "provider_tool_definitions",
        lambda: [
            {"type": "function", "function": {"name": "read_server_status", "parameters": {}}},
            {"type": "function", "function": {"name": "propose_backup", "parameters": {}}},
        ],
    )
    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({"read_server_status", "propose_backup"}),
    )

    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte(False))
    ohne = [w["name"] for w in ai_voice_tools.katalog(None, None)]
    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte(True))
    mit = [w["name"] for w in ai_voice_tools.katalog(None, None)]

    assert ai_voice_tools.AUTONOMIE not in ohne
    assert ai_voice_tools.AUTONOMIE in mit


def test_showing_evidence_needs_something_that_reads(monkeypatch) -> None:
    """Ohne ein einziges Lesewerkzeug kann die Echtheitsschranke nie halten.

    `zeige_beleg` darf nur zeigen, was ein Werkzeug in dieser Sitzung wirklich
    zurueckgab. Wer nichts lesen darf, bekaeme also einen Knopf, der nie angeht —
    und ein Modell, das ihn trotzdem versucht, haette eine Runde verbraucht.
    """
    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte())
    monkeypatch.setattr(
        ai_voice_tools, "provider_tool_definitions",
        lambda: [
            {"type": "function", "function": {"name": "read_server_status", "parameters": {}}},
            {"type": "function", "function": {"name": "propose_backup", "parameters": {}}},
        ],
    )

    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge", lambda db, user: frozenset({"propose_backup"})
    )
    ohne_lesen = [w["name"] for w in ai_voice_tools.katalog(None, None)]
    monkeypatch.setattr(
        ai_voice_tools, "angebotene_werkzeuge",
        lambda db, user: frozenset({"read_server_status"}),
    )
    mit_lesen = [w["name"] for w in ai_voice_tools.katalog(None, None)]

    assert ai_voice_tools.BELEG not in ohne_lesen
    assert ai_voice_tools.BELEG in mit_lesen


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


# ── „Ich schaue kurz nach" — und dann kam nichts mehr ─────────────────────
#
# Der auffaelligste Fehler des Sprachmodus im Betrieb, und der einzige, den ein
# Benutzer als „kaputt" beschreibt statt als „unschoen": die KI kuendigte etwas
# an, rief ein Werkzeug, und danach war Stille, bis der Mensch von sich aus
# nachfragte.
#
# Die Ursache ist die Reihenfolge der Ereignisse und nicht der Prompt:
# `response.function_call_arguments.done` kommt **waehrend** die Antwort laeuft.
# Ein sofortiges `response.create` faellt damit in eine offene Antwort und wird
# abgewiesen — danach bittet niemand mehr um eine Antwort.


@pytest.mark.asyncio
async def test_no_answer_is_asked_for_while_one_is_still_running(
    monkeypatch,
) -> None:
    """Das Ergebnis wartet auf das Ende der Antwort, statt in sie hineinzureden.

    Der Fall aus dem Betrieb, Schritt fuer Schritt: Das Modell beginnt eine
    Antwort („schaue ich kurz nach") und stellt im selben Zug einen
    Werkzeugaufruf. Das Werkzeug ist schneller fertig als der gesprochene Satz.
    Wer jetzt um eine Antwort bittet, bittet zu frueh.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: {"status": "laeuft"},
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    bruecke.antwort_begonnen()
    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c1", "name": "read_server_status", "arguments": "{}",
    }, oben, browser)

    arten = [n.get("type") for n in oben.gesendet]
    assert "conversation.item.create" in arten, "Das Ergebnis ging gar nicht hinaus"
    assert "response.create" not in arten, "Es wurde in die laufende Antwort hineingebeten"

    # Die Antwort ist zu Ende — **jetzt** darf geredet werden.
    await bruecke.antwort_beendet(oben)

    assert [n.get("type") for n in oben.gesendet][-1] == "response.create"


@pytest.mark.asyncio
async def test_several_tools_in_one_turn_ask_for_one_answer(monkeypatch) -> None:
    """Drei Ergebnisse gehoeren in eine Antwort, nicht in drei angefangene."""
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: {"ok": True},
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    bruecke.antwort_begonnen()
    for n in range(3):
        await bruecke.ereignis({
            "type": "response.function_call_arguments.done",
            "call_id": f"c{n}", "name": "read_server_status", "arguments": "{}",
        }, oben, browser)
    await bruecke.antwort_beendet(oben)

    arten = [n.get("type") for n in oben.gesendet]
    assert arten.count("conversation.item.create") == 3
    assert arten.count("response.create") == 1


@pytest.mark.asyncio
async def test_an_interruption_drops_the_pending_answer(monkeypatch) -> None:
    """Wer dazwischenredet, bekommt nicht die Antwort auf seine vorige Frage.

    Ein Abbruch heisst hier fast immer: der Mensch hat angefangen zu reden. Die
    wartende Bitte dann nachzuholen waere genau das, wogegen das Unterbrechen
    gebaut ist. Das Ergebnis ist nicht verloren — es steht im Verlauf und liegt
    der naechsten Antwort ohnehin vor.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: {"ok": True},
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    bruecke.antwort_begonnen()
    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c1", "name": "read_server_status", "arguments": "{}",
    }, oben, browser)
    await bruecke.antwort_beendet(oben, abgebrochen=True)

    assert "response.create" not in [n.get("type") for n in oben.gesendet]
    # Und die Marke ist weg: die naechste Antwort holt sie nicht doch noch nach.
    await bruecke.antwort_beendet(oben)
    assert "response.create" not in [n.get("type") for n in oben.gesendet]


@pytest.mark.asyncio
async def test_a_tool_call_outside_a_running_answer_asks_at_once(monkeypatch) -> None:
    """Laeuft keine Antwort, wird sofort gebeten — sonst waere es eine Pause."""
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    monkeypatch.setattr(
        ai_voice_tools.Bruecke, "ausfuehren",
        lambda self, name, argumente: {"ok": True},
    )
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c1", "name": "read_server_status", "arguments": "{}",
    }, oben, browser)

    assert [n.get("type") for n in oben.gesendet][-1] == "response.create"


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


def test_no_voice_only_tool_is_reachable_from_the_typed_chat() -> None:
    """Die Sicherheitszusage hinter „nur hier" — ausdruecklich, fuer alle drei.

    Bei `bestaetige_vorschlag` geht es um die Karte. Bei `set_ai_autonomy` geht
    es um etwas anderes und Schwereres: der getippte Chat liest
    Werkzeugergebnisse — Logzeilen, Konfigurationen, Suchtreffer —, und darin
    steht Text, den irgendjemand geschrieben hat. Ein Werkzeug, das die
    Rueckfragepflicht abschaltet, darf nicht in Reichweite eines solchen Textes
    liegen. Das ist eine ausdrueckliche Entscheidung des Betreibers gegen
    Prompt-Injection und keine Luecke im Katalog.

    Bei `zeige_beleg` ist es schlicht ueberfluessig: dort steht der Text ohnehin
    schon auf dem Schirm.

    Gehalten wird die Zusage nicht durch einen Filter — den vergisst jemand —,
    sondern dadurch, dass es die Werkzeuge dort **nicht gibt**. Genau das prueft
    dieser Test, und zwar an beiden Orten, an denen ein Werkzeug entstehen kann.
    """
    from services.ai_action_service import provider_tool_definitions as echte

    namen = {d["function"]["name"] for d in echte()}
    for werkzeug in (
        ai_voice_tools.BESTAETIGEN, ai_voice_tools.AUTONOMIE, ai_voice_tools.BELEG
    ):
        assert werkzeug not in ai_tool_registry.WERKZEUGE, (
            f"{werkzeug} steht in der Registry und ist damit im Chat sichtbar"
        )
        assert not ai_tool_registry.bekannt(werkzeug)
        assert werkzeug not in namen, (
            f"{werkzeug} wird dem getippten Chat als Werkzeug angeboten"
        )


def test_the_confirmation_tool_appears_only_when_writing_is_offered(monkeypatch) -> None:
    """Wer nichts aendern darf, braucht nichts zu bestaetigen."""
    monkeypatch.setattr(ai_voice_tools, "permission_service", FalscheRechte())
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


def test_a_second_proposal_is_refused_while_one_is_waiting(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Ein Ja gilt genau einem Vorschlag.

    Damit gibt es die ganze Klasse „das Ja landete auf dem falschen Vorschlag"
    nicht — weder durch ein Missverstaendnis des Modells noch dadurch, dass
    jemand zwei Saetze hintereinander sagt.

    Die Schranke greift seit der Autonomie **nach** `create_proposal` und nicht
    davor: ob ein Vorschlag ein Ja braucht, weiss erst dieser Aufruf. Der Test
    braucht deshalb einen echten Benutzer — und prueft die Kehrseite gleich mit,
    naemlich dass die kurz entstandene Zeile samt ihrem Auditeintrag wieder
    verschwindet. Bliebe sie stehen, sammelte jeder abgewiesene Versuch einen
    Vorschlag an, den niemand angelegt hat.
    """
    server = _server(db, tmp_path)
    datei = _konfiguration(server)
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use",),
        server=server, server_keys=("server.view", "server.files.read", "server.files.write"),
    )
    bruecke = ai_voice_tools.Bruecke(user_id=regular_user.id)
    bruecke.offener_vorschlag = "vorhandener-vorschlag"

    ergebnis = bruecke._vorschlagen(
        "propose_config_patch", _patch_argumente(server, datei)
    )

    assert "error" in ergebnis
    assert "noch ein Vorschlag" in ergebnis["error"]
    assert bruecke.offener_vorschlag == "vorhandener-vorschlag"
    db.expire_all()
    assert db.query(AiActionProposal).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "ai.action.proposed").count() == 0
    assert datei.read_text(encoding="utf-8") == _KONFIGURATION


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


# ── Autonomie im Gespraech ────────────────────────────────────────────────
#
# Wer den autonomen Modus eingeschaltet hat, hat genau den Schritt abbestellt,
# an dem ein Mensch zustimmt. Ihn im Sprachmodus durch eine Ruecklesung wieder
# einzufuehren hiesse, den Schalter zu ignorieren.


def test_an_autonomous_proposal_runs_at_once_and_leaves_nothing_waiting(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Bei erteilter Freigabe wird nicht vorgelesen, sondern getan.

    Geprueft wird an der Datei und nicht am Rueckgabewert allein: „ausgefuehrt"
    zu melden, ohne etwas auszufuehren, waere genau die Sorte Fehler, die unter
    dem richtigen Namen durchlaeuft.

    Und `offener_vorschlag` bleibt leer. Das ist keine Kosmetik: stuende dort
    eine Kennung, blockierte ein laengst erledigter Vorgang den naechsten, und
    das Modell wartete auf ein Ja zu etwas, das schon passiert ist.
    """
    server = _server(db, tmp_path)
    datei = _konfiguration(server)
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server=server, server_keys=("server.view", "server.files.read", "server.files.write"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    bruecke = ai_voice_tools.Bruecke(user_id=regular_user.id)
    ergebnis = bruecke._vorschlagen(
        "propose_config_patch", _patch_argumente(server, datei)
    )

    assert ergebnis["ausgefuehrt"] is True
    assert ergebnis["autonom"] is True
    # Kein Satz zum Vorlesen — es gibt nichts mehr zu fragen.
    assert "vorlesen" not in ergebnis
    assert bruecke.offener_vorschlag is None
    assert "maxPlayers=60" in datei.read_text(encoding="utf-8")


def test_an_autonomous_proposal_runs_even_while_another_one_waits(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Die Schranke „hoechstens ein offener Vorschlag" meint das gesprochene Ja.

    Sie schuetzt es davor, auf dem falschen Vorschlag zu landen. Wo kein Ja
    gebraucht wird, gibt es nichts zu verwechseln — sonst legte ein einziger
    unbeantworteter Vorschlag den autonomen Modus fuer den Rest des Gespraechs
    lahm.
    """
    server = _server(db, tmp_path)
    datei = _konfiguration(server)
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server=server, server_keys=("server.view", "server.files.read", "server.files.write"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    bruecke = ai_voice_tools.Bruecke(user_id=regular_user.id)
    bruecke.offener_vorschlag = "wartet-noch"
    ergebnis = bruecke._vorschlagen(
        "propose_config_patch", _patch_argumente(server, datei)
    )

    assert ergebnis["ausgefuehrt"] is True
    # Und der wartende Vorschlag ist unberuehrt: sein Ja gehoert weiterhin ihm.
    assert bruecke.offener_vorschlag == "wartet-noch"


def test_without_a_grant_the_proposal_is_read_out_and_waits(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Standardweg bleibt unveraendert — die Gegenprobe zur Autonomie.

    Ohne sie bliebe der Autonomietest auch dann gruen, wenn **jeder** Vorschlag
    sofort liefe. Hier passiert bis zum gesprochenen Ja nichts, und der Satz zum
    Vorlesen kommt von MSM und nicht vom Modell.
    """
    server = _server(db, tmp_path)
    datei = _konfiguration(server)
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server=server, server_keys=("server.view", "server.files.read", "server.files.write"),
    )

    bruecke = ai_voice_tools.Bruecke(user_id=regular_user.id)
    ergebnis = bruecke._vorschlagen(
        "propose_config_patch", _patch_argumente(server, datei)
    )

    assert "ausgefuehrt" not in ergebnis
    assert ergebnis["vorlesen"] == (
        "Eine Stelle in der Datei server.cfg auf Sprachserver ändern"
    )
    assert bruecke.offener_vorschlag == ergebnis["proposal_id"]
    assert datei.read_text(encoding="utf-8") == _KONFIGURATION
    # Ein Ja zu einer anderen Kennung loest ihn nicht ein.
    assert "error" in bruecke._bestaetigen("eine-andere-kennung")
    assert datei.read_text(encoding="utf-8") == _KONFIGURATION


def test_an_irreversible_tool_stays_confirmable_even_with_every_grant(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Eine gesprochene Zustimmung ist schwaecher als ein Klick.

    Sie kann missverstanden werden, im Hintergrund kann jemand anders „ja"
    sagen, und der Beweis im Audit ist ein Transkript statt einer Betaetigung.
    Fuer alles, wovon es keinen Weg zurueck gibt, ist das zu wenig — und eine
    erteilte Autonomiefreigabe aendert daran nichts.

    Geprueft wird am Ergebnis und nicht an einer Mengenzugehoerigkeit: der
    Benutzer haelt hier alles, was Autonomie sonst ausloest, und bekommt
    trotzdem fuer **jedes** Werkzeug aus `ALWAYS_CONFIRM_TOOLS` eine Absage —
    ohne dass eine Zeile entsteht.
    """
    server = _server(db, tmp_path)
    _rechte(
        db, regular_user,
        global_keys=(
            "ai.chat.use", "ai.autonomous.use", "servers.delete", "blueprints.manage",
        ),
        server=server,
        server_keys=(
            "server.view", "server.files.read", "server.files.write",
            "server.backups.restore",
        ),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    bruecke = ai_voice_tools.Bruecke(user_id=regular_user.id)
    for werkzeug in sorted(ai_tool_registry.ALWAYS_CONFIRM_TOOLS):
        ergebnis = bruecke._vorschlagen(werkzeug, {
            "server_id": server.id,
            "reason": "Der Benutzer hat darum gebeten.",
            "expected_effect": "Der Vorgang laeuft.",
        })
        assert isinstance(ergebnis, dict) and "error" in ergebnis, (
            f"{werkzeug} kam per Stimme durch"
        )
        assert "ausgefuehrt" not in ergebnis, f"{werkzeug} lief autonom"
        if ai_tool_registry.bekannt(werkzeug):
            # Vier Namen der Sperrliste sind noch Vorhaben und haetten hier auch
            # als "unbekanntes Werkzeug" abgewiesen werden koennen — das waere
            # ein gruener Test aus dem falschen Grund. Fuer die gebauten
            # Werkzeuge wird deshalb der richtige Grund verlangt: es ist die
            # Karte im Panel, die fehlt, und das soll die KI so sagen.
            assert "Panel" in ergebnis["error"], (
                f"{werkzeug} wurde abgewiesen, aber nicht wegen der Karte: "
                f"{ergebnis['error']}"
            )

    assert bruecke.offener_vorschlag is None
    db.expire_all()
    assert db.query(AiActionProposal).count() == 0
    # Und der Server, um den es in der Haelfte der Faelle ging, steht noch.
    assert db.query(Server).filter(Server.id == server.id).first() is not None


# ── Der Schalter fuer den autonomen Modus ─────────────────────────────────


def test_the_switch_refuses_without_the_permission(
    db: Session, regular_user: User
) -> None:
    """`katalog` bietet es nur mit dem Recht an — das ist keine Zusage.

    Zwischen dem Aufbau des Katalogs und diesem Aufruf liegen Minuten, in denen
    ein Admin eine Rolle aendern kann. Und ein Katalog ist ohnehin eine Bitte:
    ein Modell, das sich den Namen ausdenkt, muss hier abprallen.
    """
    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True}
    )

    assert "error" in ergebnis
    db.expire_all()
    assert db.query(AiAutonomyGrant).count() == 0


def test_the_switch_sets_the_grant_and_leaves_a_trace(
    db: Session, regular_user: User
) -> None:
    """Kein zweiter Freigabeweg: derselbe Dienst wie der Schalter im Panel.

    Und derselbe Auditeintrag, nur mit `channel: voice` — das ist der
    Unterschied, den ein Betreiber spaeter sucht. Der Schalter im Panel ist eine
    Betaetigung, dieser hier ein gesprochener Satz.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "ai.autonomous.use"))

    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True}
    )

    assert ergebnis["autonom"] is True
    db.expire_all()
    freigabe = db.query(AiAutonomyGrant).one()
    assert freigabe.server_id is None, "panelweit, weil keine `server_id` kam"
    assert freigabe.enabled is True
    assert freigabe.granted_by == regular_user.id
    assert freigabe.max_actions_per_hour == DEFAULT_MAX_ACTIONS_PER_HOUR
    eintrag = db.query(AuditLog).filter(AuditLog.action == "ai.autonomy.updated").one()
    assert "voice" in (eintrag.details or "")


def test_the_switch_never_raises_an_existing_hourly_budget(
    db: Session, regular_user: User
) -> None:
    """Das Budget ist die Schranke gegen ein Modell in der Schleife.

    Sie per Zuruf zu weiten waere genau die Handlung, gegen die sie steht — ein
    Modell, das sich in `propose_backup` verrannt hat, brauchte dann nur einmal
    „mehr davon" zu rufen. Wer mehr will, sagt es dem Panel.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "ai.autonomous.use"))
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=False,
        max_actions_per_hour=2, granted_by=regular_user.id,
    )
    db.commit()

    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True}
    )

    assert ergebnis["aktionen_pro_stunde"] == 2
    db.expire_all()
    freigabe = db.query(AiAutonomyGrant).one()
    assert freigabe.enabled is True, "Einschalten muss wirken"
    assert freigabe.max_actions_per_hour == 2


def test_a_server_switch_keeps_the_rate_that_was_already_in_force(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Ein Zuruf verschiebt den Geltungsbereich, nicht das Tempo.

    Hier stand einmal das Gegenteil: eine neue Serverzeile bekam den
    Spaltendefault, weil „das Budget des Nachbarn nicht unseres" sei. Das
    klingt sauber und war eine stille Erhoehung — `resolve_grant` zieht die
    spezifischere Zeile vor, also hob „schalt Autonomie fuer Server 7 ein" die
    wirksame Grenze dort von drei auf zehn. Eine Zahl, die niemand genannt
    hatte, ausgeloest durch einen Satz ueber den Bereich.

    Die panelweite Zeile bleibt daneben unberuehrt: geschrieben wird in genau
    einen Bereich, uebernommen wird nur die Zahl.
    """
    server = _server(db, tmp_path)
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server=server, server_keys=("server.view",),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=3, granted_by=regular_user.id,
    )
    db.commit()
    assert DEFAULT_MAX_ACTIONS_PER_HOUR != 3, "Sonst prueft der Test nichts"

    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True, "server_id": server.id}
    )

    assert ergebnis["aktionen_pro_stunde"] == 3
    db.expire_all()
    panelweit = db.query(AiAutonomyGrant).filter(
        AiAutonomyGrant.server_id.is_(None)
    ).one()
    assert panelweit.max_actions_per_hour == 3, "Das Budget des Nachbarn blieb stehen"
    fuer_server = db.query(AiAutonomyGrant).filter(
        AiAutonomyGrant.server_id == server.id
    ).one()
    assert fuer_server.max_actions_per_hour == 3


def test_an_enabled_switch_says_so_when_the_budget_is_zero(
    db: Session, regular_user: User
) -> None:
    """Freigabe erteilt, Wirkung null — das gehoert gesagt, nicht behoben.

    `autonomy_allows` weist ein Stundenbudget von null ab. Ein „ist
    eingeschaltet" waere hier eine Auskunft, die der naechste Vorschlag
    widerlegt; die Zahl ungefragt anzuheben waere die Erhoehung, gegen die das
    Werkzeug steht.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "ai.autonomous.use"))
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=False,
        max_actions_per_hour=0, granted_by=regular_user.id,
    )
    db.commit()

    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True}
    )

    assert ergebnis["autonom"] is True
    assert ergebnis["aktionen_pro_stunde"] == 0
    assert "null" in ergebnis["hinweis"]


def test_a_switch_for_an_invisible_server_says_nothing_about_it(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Spiegelt `routers/ai_autonomy._require_server_access`, samt Wortwahl.

    Eine Freigabe fuer einen Server, den man nicht sehen darf, wuerde ohnehin
    nichts bewirken — sie wuerde nur dessen Existenz verraten.
    """
    server = _server(db, tmp_path)
    _rechte(db, regular_user, global_keys=("ai.chat.use", "ai.autonomous.use"))

    ergebnis = ai_voice_tools.Bruecke(user_id=regular_user.id)._autonomie_setzen(
        {"enabled": True, "server_id": server.id}
    )

    assert ergebnis == {"error": "Server nicht gefunden."}
    db.expire_all()
    assert db.query(AiAutonomyGrant).count() == 0


@pytest.mark.parametrize("roh", ["true", "false", "ja", "nein"])
def test_a_boolean_as_a_string_is_read_and_not_guessed(roh: str) -> None:
    """`bool("false")` ist wahr — und das waere hier die Rueckfragepflicht.

    Realtime-Modelle schicken Wahrheitswerte gelegentlich als Zeichenkette. Ein
    naiv gelesenes „false" haette den autonomen Modus **eingeschaltet**, waehrend
    der Mensch gerade gesagt hat, dass er ihn ausgeschaltet haben will.
    """
    erwartet = roh in ("true", "ja")
    assert ai_voice_tools._wahrheitswert(roh) is erwartet


@pytest.mark.parametrize("roh", ["", "vielleicht", None, 1, [], {}])
def test_what_is_not_a_clear_yes_or_no_is_refused(roh: object) -> None:
    """Unlesbar heisst zurueckfragen, nicht raten."""
    assert ai_voice_tools._wahrheitswert(roh) is None


# ── Der Beleg auf dem Bildschirm ──────────────────────────────────────────
#
# Zweck: Logzeilen werden nicht vorgelesen, sondern gezeigt und muendlich
# erklaert. Die Echtheitsschranke ist dabei nicht Beiwerk, sondern der Sinn der
# Sache — ohne sie waere der Bildschirm die glaubwuerdigste Oberflaeche fuer die
# unsicherste Aussage im ganzen Panel.


_ECHTE_ZEILE = '2026-08-16 12:00:03 ERROR [main] Konnte "server.cfg" nicht lesen'


def _log_lesen(monkeypatch, bruecke, zeilen: list[str] | None = None) -> None:
    """Ein Lesewerkzeug laufen lassen, das dem Modell diese Zeilen zurueckgibt."""
    from services import ai_stream_service

    ergebnis = {"zeilen": zeilen if zeilen is not None else [_ECHTE_ZEILE]}
    monkeypatch.setattr(
        ai_stream_service, "_werkzeug_ausfuehren",
        lambda user_id, call: (ergebnis, None),
    )
    bruecke.ausfuehren("read_server_logs", {"server_id": 1})


def test_only_a_line_that_really_came_back_may_go_on_the_screen(monkeypatch) -> None:
    """Ein Modell, das eine Zeile nacherzaehlt, prallt hier ab — und erfaehrt warum.

    Die echte Zeile traegt Anfuehrungszeichen, und das ist Absicht: im Puffer
    stehen **serialisierte** Ergebnisse. Ohne den Umweg ueber die escapte Form
    fiele ausgerechnet die Sorte Zeile durch, die man am haeufigsten zeigen will
    — Stacktraces und Konfigurationswerte stecken voller Anfuehrungszeichen.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke)

    erfunden = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log von srv-1",
        "zeilen": ["2026-08-16 12:00:03 FATAL Die Datenbank ist zerstoert"],
    })

    assert "error" in erfunden
    assert bruecke.offener_beleg is None, "Erfundenes stand kurz auf dem Schirm"

    echt = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE],
    })

    assert echt["angezeigt"] is True
    assert echt["zeilen"] == 1
    # Der Hinweis gehoert zur Zusage: gezeigt statt vorgelesen ist der ganze
    # Zweck, und das Modell erfaehrt es im Ergebnis und nicht nur im Prompt.
    assert "vor" in echt["hinweis"]
    assert bruecke.offener_beleg == {"quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE]}


def test_a_long_log_can_still_be_shown(monkeypatch) -> None:
    """Ein langes Log ist der Normalfall — und war genau der Fall, der brach.

    Der Puffer bekam frueher das Ergebnis **nach** `_gekuerzt`. Bei einem langen
    Log legt die Kuerzung den serialisierten Text als Zeichenkette in ein Feld
    `anfang`, und eine zweite Serialisierung escapt darin jedes
    Anfuehrungszeichen ein zweites Mal. `_woertlich` sucht die einfach escapte
    Form und fand nichts mehr.

    Die Wirkung war die Umkehrung des Zwecks: bei kurzen Ergebnissen ging das
    Zeigen, bei langen prallte das Modell ab und wich auf das aus, was das
    Werkzeug verhindern soll — es las die Zeile vor. Ausgerechnet bei einem
    Sechs-Kilobyte-Log, also immer.
    """
    fueller = [f"2026-08-16 12:00:{n:02d} INFO Start Schritt {n} von 80" for n in range(80)]
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke, [_ECHTE_ZEILE, *fueller])

    # Vorbedingung: das Ergebnis war wirklich lang genug fuer die Kuerzung.
    assert bruecke._ergebnisse_zeichen >= ai_voice_tools.MAX_ERGEBNIS_ZEICHEN

    gezeigt = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE],
    })

    assert gezeigt.get("angezeigt") is True, gezeigt
    assert bruecke.offener_beleg == {
        "quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE],
    }
    # Und die Schranke steht weiterhin: was hinter dem Schnitt lag, hat das
    # Modell nie gesehen und darf es auch nicht zeigen.
    assert bruecke._woertlich(fueller[-1]) is False


def test_the_evidence_is_the_passage_and_not_the_whole_log(monkeypatch) -> None:
    """Wer zwanzig Zeilen zeigt, zeigt schon mehr, als er erklaeren kann.

    Der Deckel greift, obwohl jede einzelne Zeile echt ist — es geht hier nicht
    um Echtheit, sondern darum, dass ein Dump mit gesprochener Untermalung nicht
    der Zweck des Werkzeugs ist.
    """
    zeilen = [f"2026-08-16 12:00:{n:02d} INFO Zeile {n}" for n in range(25)]
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke, zeilen)

    zuviel = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log", "zeilen": zeilen[: ai_voice_tools.MAX_BELEG_ZEILEN + 1],
    })
    gerade_noch = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log", "zeilen": zeilen[: ai_voice_tools.MAX_BELEG_ZEILEN],
    })

    assert "error" in zuviel
    assert str(ai_voice_tools.MAX_BELEG_ZEILEN) in zuviel["error"]
    assert gerade_noch["zeilen"] == ai_voice_tools.MAX_BELEG_ZEILEN


def test_a_single_line_may_not_be_a_wall_of_text(monkeypatch) -> None:
    """Eine „Zeile" von zweitausend Zeichen ist keine Stelle, sondern ein Dump.

    Auch hier ist die Zeile echt — die Laenge allein reicht als Grund.
    """
    lang = "x" * (ai_voice_tools.MAX_BELEG_ZEILENLAENGE + 1)
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke, [lang])

    ergebnis = bruecke.ausfuehren(ai_voice_tools.BELEG, {"quelle": "Log", "zeilen": [lang]})

    assert "error" in ergebnis
    assert str(ai_voice_tools.MAX_BELEG_ZEILENLAENGE) in ergebnis["error"]
    assert bruecke.offener_beleg is None


def test_the_three_local_tools_do_not_feed_the_evidence_buffer(monkeypatch) -> None:
    """Sonst waere jede eigene Fehlermeldung ein Beleg.

    Der Puffer fuellt sich ausschliesslich aus dem Registry-Zweig. Die Meldungen
    der drei sprachlokalen Werkzeuge tragen Text, den das Modell selbst
    hineingereicht hat — er duerfte sonst als „Beleg" auf den Schirm, und zwar
    genau der Text, den die Schranke eben abgewiesen hat.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke)
    erfunden = "Der Server ist abgestuerzt"

    abgewiesen = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log", "zeilen": [erfunden],
    })
    zweiter_versuch = bruecke.ausfuehren(ai_voice_tools.BELEG, {
        "quelle": "Log ", "zeilen": [erfunden],
    })

    assert "error" in abgewiesen
    assert "error" in zweiter_versuch, (
        "Die eigene Absage hat den erfundenen Text belegbar gemacht"
    )
    assert bruecke.offener_beleg is None


@pytest.mark.parametrize(
    "argumente",
    [
        {"zeilen": ["x"]},
        {"quelle": "  ", "zeilen": ["x"]},
        {"quelle": "Log"},
        {"quelle": "Log", "zeilen": []},
        {"quelle": "Log", "zeilen": "keine Liste"},
        {"quelle": "Log", "zeilen": [42]},
    ],
)
def test_a_malformed_evidence_call_answers_instead_of_showing(argumente: dict) -> None:
    """Nachsichtig lesen, streng zeigen — ein Formfehler kostet eine Runde."""
    bruecke = ai_voice_tools.Bruecke(user_id=1)

    ergebnis = bruecke.ausfuehren(ai_voice_tools.BELEG, argumente)

    assert "error" in ergebnis
    assert bruecke.offener_beleg is None


@pytest.mark.asyncio
async def test_the_evidence_goes_to_the_browser_before_the_result_goes_up(
    monkeypatch,
) -> None:
    """`ausfuehren` laeuft in einem Thread und darf von dort nichts senden.

    Es legt die Stelle in `offener_beleg` ab; `_aufruf_beantworten` leert das
    Feld und schickt sie — von der Ereignisschleife aus, auf der sie hingehoert.
    Und zwar **vor** dem Ergebnis nach oben, damit die Stelle schon auf dem
    Schirm steht, wenn das Modell anfaengt, sie zu erklaeren.
    """
    bruecke = ai_voice_tools.Bruecke(user_id=1)
    _log_lesen(monkeypatch, bruecke)
    oben, browser = FalscheGegenstelle(), FalscherBrowser()

    await bruecke.ereignis({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "i9", "name": ai_voice_tools.BELEG},
    }, oben, browser)
    await bruecke.ereignis({
        "type": "response.function_call_arguments.done",
        "call_id": "c9", "item_id": "i9",
        "arguments": json.dumps({"quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE]}),
    }, oben, browser)

    assert browser.texte == [
        {"art": "werkzeug", "name": ai_voice_tools.BELEG},
        {"art": "beleg", "quelle": "Log von srv-1", "zeilen": [_ECHTE_ZEILE]},
    ]
    # Das Feld ist geleert — sonst haenge dieselbe Stelle am naechsten Aufruf
    # eines beliebigen anderen Werkzeugs.
    assert bruecke.offener_beleg is None
    assert oben.gesendet[0]["type"] == "conversation.item.create"
