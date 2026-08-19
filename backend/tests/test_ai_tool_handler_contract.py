"""Jedes Werkzeug hat einen Handler — und ohne Handler faellt es laut auf.

`test_ai_tool_registry.py` sichert die eine Haelfte: Katalog und Tabelle decken
sich. Die andere Haelfte fehlte, und genau dort lagen drei stille Loecher. Alle
drei hatten dieselbe Form — eine Verzweigung ohne Abschluss, in der ein
unbekannter Werkzeugname nicht auffiel, sondern in den letzten Zweig fiel:

* `_execute_global_read_tool` endete im Rumpf der Kapazitaetsabfrage. Ein
  Lesewerkzeug ohne Zweig lieferte dem Modell RAM-Zahlen **unter seinem eigenen
  Namen** zurueck. Fuer ein Werkzeug, das Dokumentation liefern soll, waere das
  die Halluzination gewesen, die es verhindern sollte — mit einem Beleg davor.
* `create_proposal` schrieb `elif tool_name in GLOBAL_WRITE_TOOLS:
  _server_create_payload(...)`. Das las sich wie eine Mengenzugehoerigkeit,
  meinte aber ein einziges Werkzeug.
* Im serverbezogenen Zweig baute ein namenloses `else` jedes unbekannte
  Werkzeug als Konfigurationsaenderung.

Ein vergessener Handler ist ein Fehler beim Bauen. Er darf nicht als Auskunft
beim Benutzer ankommen.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from models import Server, User
from services import (
    ai_action_service,
    ai_proposal_service,
    ai_stream_service,
    ai_tool_registry,
)
from services.ai_action_errors import AiActionValidationError


def test_a_global_read_tool_without_a_handler_raises_instead_of_returning_nodes(
    db: Session, owner_user: User
) -> None:
    """Der wichtigste Test der Datei.

    Bewusst mit einem Benutzer, der **jedes** Recht hat: nur so laeuft die Kette
    bis ans Ende durch und die Frage ist wirklich "was passiert ohne Zweig" und
    nicht "wo greift die erste Rechtepruefung". Frueher lieferte dieser Aufruf
    `{"nodes": [...]}` zurueck — ein gueltiges Ergebnis unter einem erfundenen
    Namen.
    """
    with pytest.raises(AiActionValidationError, match="Kein Handler"):
        ai_action_service._execute_global_read_tool(
            db, user=owner_user, tool_name="read_erfundenes", arguments={}
        )


def test_every_global_read_tool_is_named_in_its_dispatch() -> None:
    """Jedes Werkzeug aus der Tabelle kommt in der Verteilung namentlich vor.

    Der Test darueber faengt den Fantasienamen; dieser hier faengt den Fall,
    der wirklich passiert: ein Werkzeug wird in die Tabelle und in den Katalog
    eingetragen, und das Verdrahten geht beim naechsten Kaffee unter.
    """
    quelle = inspect.getsource(ai_action_service._execute_global_read_tool)
    # `ask_user` ist kein Lesewerkzeug, sondern die Rueckfrage; sie wird im
    # Stream behandelt und erreicht diese Verteilung nie.
    fehlend = sorted(
        name
        for name in ai_tool_registry.GLOBAL_READ_TOOLS - ai_tool_registry.ASK_TOOLS
        if f'"{name}"' not in quelle
    )
    assert fehlend == []


def test_every_write_tool_has_its_own_payload_branch() -> None:
    """Kein Schreibwerkzeug darf die Nutzlast eines anderen bekommen.

    Ein Vorschlag, der unter dem Namen A die Nutzlast von B traegt, entsteht
    ohne Fehler, sieht in der Karte plausibel aus und tut beim Ausfuehren etwas
    anderes, als sein Name sagt.

    Seit die globalen Schreibwerkzeuge in der Tabelle `_GLOBALE_PAYLOADS`
    stehen statt in einer elif-Kette, gibt es **zwei** gueltige Orte fuer einen
    Nutzlastbau: ein Tabelleneintrag oder ein namentlicher Zweig in
    `create_proposal`. Ein Werkzeug, das an keinem von beiden vorkommt, faellt
    dort in den Waechterzweig ("Kein Payload-Bau fuer Werkzeug") — dieser Test
    sorgt dafuer, dass das beim Bauen auffaellt und nicht erst beim Benutzer.
    """
    quelle = inspect.getsource(ai_proposal_service.create_proposal)
    fehlend = sorted(
        name
        for name in ai_tool_registry.WRITE_TOOLS
        if name not in ai_proposal_service._GLOBALE_PAYLOADS
        and f'"{name}"' not in quelle
    )
    assert fehlend == []


def test_the_tool_catalogue_stays_within_a_stated_budget() -> None:
    """Der Katalog ist teurer als der ganze Systemprompt — und unsichtbar.

    `provider_tool_definitions()` geht in **jeder** Runde der Werkzeugschleife
    mit ueber die Leitung, aber `message_character_count` sieht ihn nicht: er
    steht weder im Kontextbudget noch im Fuellstandsring neben dem
    Absendeknopf.

    Die Grenze ist keine technische Schranke, sondern eine sichtbare Zahl: wer
    sie reisst, soll das bemerken, bevor es ein Modell mit kleinem Fenster
    bemerkt. Genau dafuer ist sie beim Bau der stehenden KI-Aufgaben auch
    angesprungen — und dabei kam heraus, dass die hier dokumentierte Messung
    laengst nicht mehr stimmte.

    Stand 13.08.2026, nachgemessen statt abgeschrieben:

    * ohne die Aufgaben-Werkzeuge: **40.930** Zeichen (nicht "rund 31.000",
      wie hier bis dahin stand — der Katalog war seither um ein Drittel
      gewachsen, ohne dass die Grenze je angesprungen waere),
    * die vier Aufgaben-Werkzeuge zusammen: **4.193** Zeichen,
    * Systemprompt zum Vergleich: gut 8.000.

    Die Grenze steht deshalb jetzt bei 50.000 und nicht bei 45.000. Das ist eine
    bewusste Anhebung um rund zehn Prozent fuer ein Feature, das der Betreiber
    ausdruecklich bestellt hat — und keine Gewoehnung: der naechste, der hier
    auflaeuft, findet eine Zahl vor, die zu einer nachgemessenen gehoert, und
    muss dieselbe Entscheidung erneut treffen. Wer sie wieder anhebt, misst
    vorher nach und schreibt es hin.

    Der billigste Weg unter die Grenze ist uebrigens **nicht**, Beschreibungen
    zu kuerzen: jede von ihnen hat einen Betriebsanlass. Er ist, zwei Werkzeuge
    zu einem zu machen, wenn sie dasselbe Schema tragen — so wurden aus
    `propose_task_create` und `propose_task_update` ein `propose_task_set`, und
    das Planschema steht seitdem einmal statt zweimal im Katalog.

    Stand 14.08.2026: **43.565** Zeichen ohne hinterlegten Websuchschlüssel,
    **44.331** mit ihm. Die 1.701 Zeichen weniger stammen nicht daher, dass ein
    Anlass fallengelassen wurde, sondern daher, dass er nur noch **einmal**
    dasteht: `propose_task_set`, `remember` und `learn_skill` wiederholten in
    ihren Beschreibungen, was `ai_prompt.AUFGABEN`, `GEDAECHTNIS` und `SKILLS`
    in derselben Anfrage schon sagen. Welche Stelle welche ersetzt, steht als
    Kommentar über dem jeweiligen Werkzeug; dass die Blöcke dabeibleiben
    müssen, hält `test_ai_prompt` fest.

    Stand 18.08.2026, nachgemessen: **52.404** Zeichen mit den vier
    Gehirn/Worker-Werkzeugen (`worker_start`, `worker_cancel`, `wait_until`,
    `worker_frage` — zusammen rund 3.900 Zeichen, docs/agentic-framework.md).
    Die Grenze steht deshalb bei 55.000. Wichtig fuer die Einordnung: dieser
    Volltext ist ab jetzt der **Worst Case eines Ein-Modell-Chats** — sobald
    der Laufart-Schnitt greift, sieht das Gehirn nur noch etwa sieben
    Werkzeuge (sein Katalog schrumpft um mehr als 90 Prozent, das war das
    erklaerte Latenzziel), und die Worker verlieren die Gehirn- und
    Gedaechtniswerkzeuge. Wer die Grenze erneut anhebt, misst vorher nach und
    schreibt es hin.
    """
    katalog = json.dumps(ai_action_service.provider_tool_definitions(), ensure_ascii=False)
    assert len(katalog) < 55_000, (
        f"Der Werkzeugkatalog ist auf {len(katalog)} Zeichen gewachsen. "
        "Er geht in jeder Runde mit und taucht in keiner Budgetrechnung auf."
    )


def test_a_failed_tool_call_is_marked_in_the_history() -> None:
    """Sonst sieht ein gescheiterter Aufruf aus wie ein geglueckter.

    Fuer die Doku-Werkzeuge ist das der schlimmste Fall: im Verlauf steht
    "Dokumentation gelesen", und die Antwort darunter ist geraten. Das Feld
    stand seit jeher im SSE-Payload — es fehlte im TypeScript-Typ und wurde
    nirgends gerendert.
    """
    # `_anzeigeeintrag` und nicht mehr `_tool_followup_messages`: der Bau des
    # Verlaufseintrags ist dorthin gewandert, als die Werkzeuge nebenlaeufig
    # wurden. Die Zusage ist dieselbe geblieben, nur ihr Ort hat sich geaendert.
    quelle = inspect.getsource(ai_stream_service._anzeigeeintrag)
    assert '"failed": True' in quelle
    typen = (
        Path(ai_action_service.__file__).resolve().parents[2]
        / "frontend" / "src" / "api" / "ai.ts"
    ).read_text(encoding="utf-8")
    assert "failed?: boolean" in typen


def test_the_group_reaches_the_frontend() -> None:
    """`gruppe` steuert das Symbol im Verlauf und verliess das Backend nie.

    Das Frontend riet sie an einem hartkodierten `tool_name === 'remember'` nach
    — `search_memory` und `forget_memory` tragen dieselbe Gruppe und bekamen
    trotzdem den allgemeinen Schraubenschluessel.
    """
    quelle = inspect.getsource(ai_stream_service._anzeigeeintrag)
    assert '"gruppe"' in quelle


# Serverbezogene Lesewerkzeuge, die der Rauchtest unten ausspart — je Zeile mit
# dem Grund.
#
# Beide sprechen nach draußen. Sie mitzunehmen hieße, sie zu mocken, und ein
# Mock ersetzt genau den Teil, den dieser Test prüfen soll. Ehrlicher ist eine
# kurze, benannte Ausnahmeliste — zusammen mit dem Test darunter, der aufpasst,
# dass sie nicht durch eine Umbenennung still ins Leere zeigt.
OHNE_RAUCHTEST = {
    "check_server_reachability": "öffnet eine TCP-Verbindung zum Server",
    "search_workshop_mods": "fragt die Steam-Workshop-Suche",
}


# Argumente, ohne die ein Werkzeug schon an seiner eigenen Formprüfung endet.
# Ein Aufruf, der dort abbricht, hat den Handler nie erreicht und würde nichts
# beweisen.
#
# Diese Tabelle ist die einzige Antwort des Rauchtests auf die Frage "braucht
# dieses Werkzeug noch etwas?". Wer ein Werkzeug ergänzt, dessen Formprüfung
# mehr verlangt als eine `server_id`, trägt es hier ein — der Test selbst rät
# nicht.
BEISPIELARGUMENTE: dict[str, dict] = {
    "read_config": {"path": "server.cfg"},
    "search_server_files": {"query": "hostname"},
}


@pytest.fixture
def rauchtest_server(db: Session, tmp_path: Path) -> Server:
    """Ein Server, an dem die Lesewerkzeuge wirklich etwas zu lesen finden.

    Bewusst nicht die `test_server`-Fixture: die trägt einen Containernamen,
    und damit griffe `read_server_logs` nach dem Docker-Daemon. Ohne
    Containernamen endet es sauber mit `available: False` — derselbe Handler,
    ohne die Abhängigkeit nach außen.

    Das Verzeichnis ist echt und enthält eine Datei, damit `read_config`,
    `list_server_files` und `search_server_files` nicht schon am fehlenden Pfad
    scheitern.

    Und der Server hat bewusst **keine** Mods: `read_mod_updates` fragt Steam
    erst, wenn es aktive Mods gibt (`games/updater.py`, `mod_ids` leer heißt
    kein Abruf). Wer hier eine Mod-Fixture ergänzt, holt sich damit einen
    Netzabruf in den Test.
    """
    verzeichnis = tmp_path / "srv"
    verzeichnis.mkdir()
    (verzeichnis / "server.cfg").write_text("hostname=Rauchtest\n", encoding="utf-8")
    server = Server(
        name="Rauchtest",
        game_type="dayz",
        install_dir=str(verzeichnis),
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@pytest.mark.parametrize(
    "tool_name",
    sorted(ai_tool_registry.SERVER_READ_TOOLS - set(OHNE_RAUCHTEST)),
)
def test_every_server_read_tool_survives_being_called(
    db: Session, owner_user: User, rauchtest_server: Server, tool_name: str
) -> None:
    """Jedes serverbezogene Lesewerkzeug wird einmal wirklich ausgeführt.

    Die Tests weiter oben prüfen, ob ein Werkzeugname im Quelltext der
    Verteilung **vorkommt**. Das ist zu wenig: `read_guardian_incidents` kam
    vor und war trotzdem kaputt — ein `NameError` in seinem Zweig fiel erst im
    Betrieb auf, in einem Lauf, bei dem niemand zusah.

    Verlangt wird das Mindeste, aber das für jedes Werkzeug: ein Ergebnis, das
    beim Modell ankommen kann.

    **Ein `AiActionValidationError` gilt ausdrücklich nicht als bestanden.**
    Für diesen Fall stand hier ein stilles `return`, und damit war der Test
    nicht mehr zu widerlegen: ein Werkzeug, das an seiner Formprüfung *immer*
    abbricht — vertippter Argumentname, verdrehte Bedingung —, erreichte seinen
    Handler nie und bestand trotzdem. Genau das soll der Rauchtest fangen.

    Ob so ein Abbruch „dem Aufruf fehlen Argumente" oder „der Handler ist
    kaputt" heisst, kann der Test nicht wissen. Also entscheidet er es nicht,
    sondern legt es dem vor, der das Werkzeug gebaut hat: fehlende Argumente
    gehören nach `BEISPIELARGUMENTE`, alles andere ist ein Baufehler.

    Der Benutzer hat bewusst jedes Recht: sonst endete die Kette an der ersten
    Rechteprüfung, und geprüft wäre wieder nur die Prüfung.
    """
    arguments = {
        "server_id": rauchtest_server.id,
        **BEISPIELARGUMENTE.get(tool_name, {}),
    }
    try:
        ergebnis = ai_action_service.execute_read_tool(
            db, user=owner_user, tool_name=tool_name, arguments=arguments
        )
    except AiActionValidationError as fehler:
        pytest.fail(
            f"{tool_name} endet an einer Formprüfung: {fehler}. Entweder fehlen "
            "dem Aufruf Argumente — dann gehören sie nach BEISPIELARGUMENTE —, "
            "oder der Handler ist kaputt. Weggesehen wird hier nicht."
        )
    assert isinstance(ergebnis, dict) and ergebnis, (
        f"{tool_name} liefert kein befülltes Wörterbuch, sondern {ergebnis!r}"
    )
    # Derselbe Schritt, den jedes Ergebnis im Betrieb nimmt (`ai_stream_service`
    # serialisiert es mit `json.dumps(..., ensure_ascii=True)`, bevor es als
    # Werkzeugantwort zum Anbieter geht). Ein Handler, der ein `datetime`, einen
    # `Path` oder ein ORM-Objekt durchreicht, fällt im Test nicht auf und
    # reisst im Lauf die ganze Runde ab — dort fängt es niemand mehr auf.
    json.dumps(ergebnis, ensure_ascii=True)
    # Und es ist der Server, nach dem gefragt wurde. Nicht jedes Ergebnis nennt
    # einen — `read_config` nennt nur den Pfad —, aber wer einen nennt, nennt
    # den richtigen: ein Handler, der die Server-ID des Aufrufs verliert und
    # einen anderen auflöst, gibt fremde Serverdaten unter der Frage nach
    # diesem heraus.
    if "server_id" in ergebnis:
        assert ergebnis["server_id"] == rauchtest_server.id, (
            f"{tool_name} antwortet über Server {ergebnis['server_id']}, "
            f"gefragt war {rauchtest_server.id}"
        )


def test_the_smoke_test_exceptions_are_still_real_tools() -> None:
    """Eine Ausnahmeliste, die ins Leere zeigt, spart still ein Werkzeug aus.

    Wird eines der ausgesparten Werkzeuge umbenannt oder entfernt, bliebe der
    alte Name hier stehen — und ein neues Werkzeug mit ähnlichem Namen liefe
    unbemerkt nicht mit.
    """
    assert set(OHNE_RAUCHTEST) <= ai_tool_registry.SERVER_READ_TOOLS


def test_every_write_tool_has_an_execution_branch() -> None:
    """Sonst faellt der fehlende Zweig erst auf, **nachdem** ein Mensch
    bestaetigt hat.

    `execute_proposal` endet bei einem unbekannten Werkzeug in
    `AI_ACTION_TOOL_NOT_ALLOWED`. Bis dahin hat der Benutzer den Vorschlag
    gelesen, den Bestaetigungsdialog weggeklickt und einen Einmal-Token
    verbraucht — und bekommt dann eine Meldung, die nach fehlendem Recht
    aussieht statt nach fehlendem Code.

    Seit die Ausfuehrung in der Tabelle `_AUSFUEHRUNGEN` steht statt in einer
    elif-Kette, gibt es — wie beim Payload-Test oben — **zwei** gueltige Orte
    fuer die Verdrahtung: ein Tabelleneintrag oder ein namentlicher Zweig in
    `execute_proposal`. Ein Werkzeug, das an keinem von beiden vorkommt,
    faellt dort in den Waechterzweig — dieser Test sorgt dafuer, dass das
    beim Bauen auffaellt und nicht erst nach der Bestaetigung.
    """
    quelle = inspect.getsource(ai_proposal_service.execute_proposal)
    fehlend = sorted(
        name
        for name in ai_tool_registry.WRITE_TOOLS
        if name not in ai_proposal_service._AUSFUEHRUNGEN
        and f'"{name}"' not in quelle
    )
    assert fehlend == []
