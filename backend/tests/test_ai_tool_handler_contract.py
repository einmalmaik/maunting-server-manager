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

from models import User
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
    """
    quelle = inspect.getsource(ai_proposal_service.create_proposal)
    fehlend = sorted(
        name for name in ai_tool_registry.WRITE_TOOLS if f'"{name}"' not in quelle
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
    """
    katalog = json.dumps(ai_action_service.provider_tool_definitions(), ensure_ascii=False)
    assert len(katalog) < 50_000, (
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
    quelle = inspect.getsource(ai_stream_service._tool_followup_messages)
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
    quelle = inspect.getsource(ai_stream_service._tool_followup_messages)
    assert '"gruppe"' in quelle


def test_every_write_tool_has_an_execution_branch() -> None:
    """Sonst faellt der fehlende Zweig erst auf, **nachdem** ein Mensch
    bestaetigt hat.

    `execute_proposal` endet bei einem unbekannten Werkzeug in
    `AI_ACTION_TOOL_NOT_ALLOWED`. Bis dahin hat der Benutzer den Vorschlag
    gelesen, den Bestaetigungsdialog weggeklickt und einen Einmal-Token
    verbraucht — und bekommt dann eine Meldung, die nach fehlendem Recht
    aussieht statt nach fehlendem Code.
    """
    quelle = inspect.getsource(ai_proposal_service.execute_proposal)
    fehlend = sorted(
        name for name in ai_tool_registry.WRITE_TOOLS if f'"{name}"' not in quelle
    )
    assert fehlend == []
