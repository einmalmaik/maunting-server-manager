"""Die Ansage vor der Arbeit: das Ereignis ``tool_plan``.

Bisher erfuhr die Oberfläche einen Werkzeugnamen erst **mit** dem Ergebnis:
``tool`` geht hinaus, nachdem der Aufruf durchgelaufen ist. Genau davor liegt
die längste Stille eines Laufs — bei einem Logauszug über eine langsame Leitung
sind das mehrere Sekunden, in denen nichts zu sehen ist als ein Spinner ohne
Text.

``tool_plan`` schließt diese Lücke, und die drei Zusagen dieser Datei sind
genau das, was dabei schiefgehen kann:

* Kommt die Ansage **nach** den Ergebnissen, ist sie wertlos: sie soll sagen,
  was gleich läuft, nicht was gelaufen ist.
* Fehlt eine ``call_id``, zeigt die Oberfläche einen Aufruf zu wenig. Der Name
  taugt dafür nicht als Schlüssel — bis zu acht Werkzeuge laufen gleichzeitig,
  und dasselbe kann in einer Runde zweimal vorkommen.
* Landet die Ansage im **Abzug**, steht sie nach einem Neuladen dauerhaft im
  Verlauf. Sie ist eine Absicht, keine Tatsache; der Abzug trägt nur Tatsachen.

Dazu kommt die vierte, die aus der Reihenfolge im Code folgt: angesagt wird
erst, wenn aussortiert ist. Ein Werkzeug, das in diesem Rahmen gar nicht laufen
darf, darf auch nicht angekündigt werden — sonst behauptet die Anzeige eine
Arbeit, die nie stattfand.

Und die fünfte, weil sie zuerst fehlte: **die Ansage gilt für jedes Werkzeug,
nicht nur für die lesenden.** Achtzehn der zweiundfünfzig gepflegten
Verlaufssätze gehören Schreibwerkzeugen, und sie waren unerreichbar — der
Vorschlagspfad sagte nichts an. Ausgerechnet dort ist die Stille am längsten:
ein Backup dauert Sekunden bis Minuten, ein Neustart auch.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiAutonomyGrant,
    AiProvider,
    AiRun,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_run_broker, ai_stream_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_proposal_service import AufgabenKontext
from services.ai_stream.read_tools import _fruehstart_lesewerkzeug_erlaubt
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.role_service import set_user_roles


_KEIN_CLIENT = object()


def _lauf(db: Session, user: User) -> AiRun:
    """Ein echter Lauf mit eigenem Kanal — die Zeile in ``ai_runs`` wird gebraucht.

    Ein erfundener ``run_id`` täte es nicht: die Runde hält ihre Ergebnisse in
    ``ai_tool_results`` fest, und deren Fremdschlüssel auf den Lauf ist in der
    Testdatenbank scharf geschaltet.
    """
    provider = AiProvider(
        name="Ansage", provider_kind="openrouter", default_model="model-a",
        enabled=True, requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)

    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Ansage"
    )
    db.add(conversation)
    db.commit()

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Wie geht es dem Server?", reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    db.commit()

    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen(run.id)
    return run


def _werkzeuge_taeuschen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ersetzt die Ausführung durch ein sofortiges Ergebnis.

    Geprüft wird die **Reihenfolge der Meldungen**, nicht was ein Werkzeug
    liefert. Echte Aufrufe brauchten Server, Rechte und Nodes und würden die
    Frage dieser Datei mit ihrem eigenen Aufbau zudecken.
    """
    def _sofort(
        _user_id: int,
        call: ProviderToolCall,
        _herkunft: str = "panel",
        _familie: str | None = None,
        _prefetch_session_id: str | None = None,
    ):
        return {"tool": call.name}, None

    monkeypatch.setattr(ai_stream_service, "_werkzeug_ausfuehren", _sofort)


def _abholen(warteschlange) -> list[tuple[str, dict]]:
    ereignisse = []
    while not warteschlange.empty():
        ereignisse.append(warteschlange.get_nowait())
    return ereignisse


@pytest.mark.asyncio
async def test_die_ansage_kommt_vor_den_ergebnissen_und_nennt_jeden_aufruf(
    db: Session, regular_user: User, test_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein ``tool_plan`` mit allen ``call_id`` der Runde, vor dem ersten ``tool``.

    Die dritte Servernummer kommt als Wort statt als Zahl. Die Argumente
    stammen vom Modell — dort kann alles stehen, und für die Anzeige zählt nur
    eine echte Zahl. Alles andere heißt "kein Serverbezug" und nicht "kaputt".
    """
    _werkzeuge_taeuschen(monkeypatch)
    run = _lauf(db, regular_user)
    _abzug, warteschlange = ai_run_broker.abonnieren(run.id)

    await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=run.conversation_id,
        tool_calls=[
            ProviderToolCall(
                id="call_1", name="read_server_status",
                arguments={"server_id": test_server.id},
            ),
            ProviderToolCall(id="call_2", name="list_my_servers", arguments={}),
            ProviderToolCall(
                id="call_3", name="read_server_status",
                arguments={"server_id": "zwoelf"},
            ),
        ],
        run_id=run.id,
    )

    ereignisse = _abholen(warteschlange)
    namen = [name for name, _ in ereignisse]
    assert namen == ["tool_plan", "tool", "tool", "tool"], (
        f"Die Reihenfolge war {namen} — die Ansage muss vor den Ergebnissen "
        "stehen und genau einmal je Runde kommen"
    )

    _, plan = ereignisse[0]
    assert plan == {"aufrufe": [
        {
            "call_id": "call_1", "tool_name": "read_server_status",
            "server_id": test_server.id,
        },
        {"call_id": "call_2", "tool_name": "list_my_servers", "server_id": None},
        {"call_id": "call_3", "tool_name": "read_server_status", "server_id": None},
    ]}


@pytest.mark.asyncio
async def test_die_ansage_landet_nicht_im_abzug(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer sich nach der Runde anhängt, sieht zwei Werkzeuge — keine Ansage.

    Der Abzug ist der Stand, den ein neu geladener Browser bekommt. Schriebe
    der Vermittler die Ansage dort hinein, stünde jeder Aufruf zweimal im
    Verlauf: einmal als Absicht, einmal als Tatsache — dauerhaft, denn
    ``snapshot`` ersetzt im Client die Abschnitte.
    """
    _werkzeuge_taeuschen(monkeypatch)
    run = _lauf(db, regular_user)

    await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=run.conversation_id,
        tool_calls=[
            ProviderToolCall(id="call_1", name="list_my_servers", arguments={}),
            ProviderToolCall(id="call_2", name="list_my_servers", arguments={}),
        ],
        run_id=run.id,
    )

    abzug, _warteschlange = ai_run_broker.abonnieren(run.id)
    arten = [abschnitt.get("art") for abschnitt in abzug.abschnitte]
    assert arten == ["tool", "tool"], (
        f"Der Abzug trägt {arten} — die Ansage gehört nicht hinein"
    )
    # Die ``call_id`` ist der Beweis: sie steht nur in der Ansage, nie in einem
    # Werkzeugeintrag des Verlaufs.
    assert "call_1" not in json.dumps(abzug.als_ereignis(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_ein_aussortierter_aufruf_wird_nicht_angesagt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Angesagt wird nur, was der Rahmen zulässt.

    In einem fällig gewordenen Auftrag steht ``search_memory`` nicht zur
    Verfügung; der Aufruf wandert mit Begründung zurück an das Modell, ohne zu
    laufen. Käme die Ansage vor dieser Auswahl, behauptete die Oberfläche eine
    Arbeit, die nie stattfand.
    """
    _werkzeuge_taeuschen(monkeypatch)
    run = _lauf(db, regular_user)
    _abzug, warteschlange = ai_run_broker.abonnieren(run.id)

    await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=run.conversation_id,
        tool_calls=[
            ProviderToolCall(id="call_1", name="list_my_servers", arguments={}),
            ProviderToolCall(
                id="call_2", name="search_memory", arguments={"query": "Ports"}
            ),
        ],
        run_id=run.id,
        aufgabe=AufgabenKontext(
            task_id=str(uuid4()), kind="report", channel="email",
            title="Nächtliche Runde",
        ),
    )

    ereignisse = _abholen(warteschlange)
    assert [name for name, _ in ereignisse] == ["tool_plan", "tool"]
    _, plan = ereignisse[0]
    assert plan == {"aufrufe": [
        {"call_id": "call_1", "tool_name": "list_my_servers", "server_id": None},
    ]}


# ── Der Schreibpfad ───────────────────────────────────────────────────────────
#
# Die Tests oben rufen `_tool_followup_messages` unmittelbar. Für einen
# Schreibvorschlag geht das nicht: die Ansage steht in `segment_ausfuehren`,
# direkt vor `_persist_write_proposals`, weil diese Funktion in einem eigenen
# Thread läuft und `veroeffentlichen` auf die Ereignisschleife gehört. Geprüft
# wird deshalb der ganze Lauf mit gefälschtem Anbieter — das ist zugleich der
# einzige Weg, die Frage "wie viele Ansagen je Runde?" ehrlich zu stellen.


def _rechte_geben(db: Session, user: User, server: Server, *schluessel: str) -> None:
    """Eine Rolle mit Chatrecht und offenem Kontingent, dazu die Serverrechte.

    Ohne sie entstünde gar kein Vorschlag — `create_proposal` prüft das Recht am
    Server —, und dann gäbe es nichts, wogegen sich die Ansage vergleichen liesse.
    """
    rolle = Role(name=f"ansage-{user.id}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    set_role_limit(db, rolle.id, {feld: None for feld in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [rolle.id])
    for key in schluessel:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _anbieter_taeuschen(
    monkeypatch: pytest.MonkeyPatch, runden: list[list[ProviderToolCall]]
) -> None:
    """Der Anbieter liefert eine feste Folge von Werkzeugrunden, dann nur Text."""
    zaehler = {"runde": 0}

    async def fake(
        _client, *, provider, api_key, messages, usage: StreamUsage,
        tools=None, tool_choice=None, reasoning=False, reasoning_effort=None,
        cache_marke=False,
        model=None,
    ):
        del provider, api_key, messages, tools, reasoning, reasoning_effort, cache_marke
        # Die Schlussrunde erkennt man an `tool_choice="none"`: dort gibt es
        # keine Werkzeuge mehr, also auch keine Runde zu zählen.
        if tool_choice == "none":
            usage.total_tokens = 10
            yield StreamChunk("content", "ok")
            return
        index = zaehler["runde"]
        zaehler["runde"] += 1
        if index < len(runden):
            usage.tool_calls = list(runden[index])
        usage.total_tokens = 10
        yield StreamChunk("content", "ok")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)


@pytest.mark.asyncio
async def test_sicherer_read_startet_vor_dem_provider_streamende(
    db: Session, regular_user: User, test_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein `tool_ready` startet nur den sicheren Read noch im Providerstrom."""
    _rechte_geben(db, regular_user, test_server, "server.view")
    rolle = db.query(Role).filter(Role.name == f"ansage-{regular_user.id}").one()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.autonomous.use"))
    db.add(AiAutonomyGrant(
        user_id=regular_user.id,
        server_id=None,
        enabled=True,
        max_actions_per_hour=50,
    ))
    db.commit()
    run = _lauf(db, regular_user)
    executor_started = asyncio.Event()
    provider_darf_enden = asyncio.Event()
    provider_beendet = asyncio.Event()
    call = ProviderToolCall(
        id="call_read",
        name="read_server_status",
        arguments={"server_id": test_server.id},
    )
    runden = {"n": 0}

    def executor(*_args):
        executor_started.set()
        return {"status": "running"}, None

    async def fake_stream(_client, *, usage, **_kwargs):
        runden["n"] += 1
        if runden["n"] == 1:
            usage.tool_calls = [call]
            yield StreamChunk("tool_ready", tool_call=call)
            await provider_darf_enden.wait()
            provider_beendet.set()
            return
        yield StreamChunk("content", "Status ist da.")

    monkeypatch.setattr(ai_stream_service, "_werkzeug_ausfuehren", executor)
    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake_stream)

    task = asyncio.create_task(
        ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    )
    await asyncio.wait_for(executor_started.wait(), timeout=1)
    assert not provider_beendet.is_set()
    provider_darf_enden.set()
    await task


def test_unsichere_und_unautorisierte_calls_starten_nicht_frueh(
    db: Session, regular_user: User, test_server: Server,
) -> None:
    """Read-Katalog ist nicht gleich der engen, autonomen Vorstart-Allowlist."""
    _rechte_geben(db, regular_user, test_server, "server.view")
    run = _lauf(db, regular_user)
    erlaubte = frozenset({
        "read_server_status", "list_my_servers", "propose_backup",
    })
    basis = {
        "run_id": run.id,
        "user_id": regular_user.id,
        "conversation_id": run.conversation_id,
        "angebotene_werkzeuge": erlaubte,
    }
    assert not _fruehstart_lesewerkzeug_erlaubt(
        call=ProviderToolCall(
            id="write", name="propose_backup", arguments={"server_id": test_server.id}
        ),
        **basis,
    )
    assert not _fruehstart_lesewerkzeug_erlaubt(
        call=ProviderToolCall(id="other-read", name="list_my_servers", arguments={}),
        **basis,
    )
    assert not _fruehstart_lesewerkzeug_erlaubt(
        call=ProviderToolCall(
            id="invalid", name="read_server_status", arguments={"server_id": "1"}
        ),
        **basis,
    )
    assert not _fruehstart_lesewerkzeug_erlaubt(
        call=ProviderToolCall(
            id="no-autonomy",
            name="read_server_status",
            arguments={"server_id": test_server.id},
        ),
        **basis,
    )


def _sicherung(server: Server, call_id: str) -> ProviderToolCall:
    return ProviderToolCall(id=call_id, name="propose_backup", arguments={
        "server_id": server.id,
        "reason": "Vor dem Eingriff absichern.",
        "expected_effect": "Ein Wiederherstellungspunkt liegt vor.",
    })


@pytest.mark.asyncio
async def test_auch_ein_schreibwerkzeug_wird_angesagt(
    db: Session, regular_user: User, test_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``propose_backup`` bekommt dieselbe Ansage wie ein Lesewerkzeug.

    Und sie kommt **vor** der Vorschlagskarte. Danach wäre sie sinnlos: der
    Benutzer sieht dann längst, worum es geht — die Ansage soll die Zeit davor
    füllen, in der das Backup tatsächlich läuft.
    """
    _rechte_geben(db, regular_user, test_server, "server.view", "server.backups.create")
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _anbieter_taeuschen(monkeypatch, [[_sicherung(test_server, "call_w")]])

    run = _lauf(db, regular_user)
    _abzug, warteschlange = ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    ereignisse = _abholen(warteschlange)
    namen = [name for name, _ in ereignisse]
    assert namen.count("tool_plan") == 1, (
        f"Die Runde meldete {namen} — genau eine Ansage je Runde"
    )
    plan = ereignisse[namen.index("tool_plan")][1]
    assert plan == {"aufrufe": [{
        "call_id": "call_w",
        "tool_name": "propose_backup",
        "server_id": test_server.id,
    }]}
    karte = next(
        (i for i, name in enumerate(namen) if name in {"proposal", "action"}), None
    )
    assert karte is not None, f"Kein Vorschlag im Lauf: {namen}"
    assert namen.index("tool_plan") < karte, (
        f"Die Ansage stand nach der Karte: {namen}"
    )


@pytest.mark.asyncio
async def test_eine_gemischte_runde_sagt_genau_einmal_an(
    db: Session, regular_user: User, test_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lesen und Schreiben nebeneinander ergeben **eine** Ansage, nicht zwei.

    Die Oberfläche ersetzt ihren Zustand mit jeder Ansage, sie ergänzt ihn
    nicht: zwei Ansagen in einer Runde hiessen, die zweite vergisst die erste.

    Angesagt wird dabei nur das Lesewerkzeug — der Schreibaufruf läuft in dieser
    Runde bewusst nicht, sondern geht mit Begründung an das Modell zurück. Eine
    Ansage für ihn wäre eine Behauptung über Arbeit, die nicht stattfindet.
    """
    _rechte_geben(db, regular_user, test_server, "server.view", "server.backups.create")
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _werkzeuge_taeuschen(monkeypatch)
    _anbieter_taeuschen(monkeypatch, [[
        ProviderToolCall(
            id="call_r", name="read_server_status",
            arguments={"server_id": test_server.id},
        ),
        _sicherung(test_server, "call_w"),
    ]])

    run = _lauf(db, regular_user)
    _abzug, warteschlange = ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    ereignisse = _abholen(warteschlange)
    ansagen = [daten for name, daten in ereignisse if name == "tool_plan"]
    assert len(ansagen) == 1, (
        f"{len(ansagen)} Ansagen in einer Runde — jede weitere löscht die davor"
    )
    assert ansagen[0] == {"aufrufe": [{
        "call_id": "call_r",
        "tool_name": "read_server_status",
        "server_id": test_server.id,
    }]}


@pytest.mark.asyncio
async def test_die_ansage_des_schreibwerkzeugs_landet_nicht_im_abzug(
    db: Session, regular_user: User, test_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auch im Schreibpfad bleibt die Ansage flüchtig.

    Der Abzug ist der Stand, den ein neu geladener Browser bekommt. Stünde die
    Ansage dort, läse der Benutzer nach dem Neuladen "ich lege ein Backup an"
    über einer Karte, die längst auf seine Bestätigung wartet — dauerhaft.
    """
    _rechte_geben(db, regular_user, test_server, "server.view", "server.backups.create")
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _anbieter_taeuschen(monkeypatch, [[_sicherung(test_server, "call_w")]])

    run = _lauf(db, regular_user)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    # Erst danach anhängen: `abonnieren` gibt eine **Kopie** des Abzugs zurück,
    # ein vorher geholter Stand bliebe leer. Genau so kommt auch ein Browser
    # zurück, der zwischendurch neu geladen wurde.
    abzug, _warteschlange = ai_run_broker.abonnieren(run.id)
    # Der Vorschlag selbst gehört in den Abzug — er ist eine Tatsache.
    assert abzug.vorschlaege, "Der Vorschlag fehlt im Abzug"
    gespeichert = json.dumps(abzug.als_ereignis(), ensure_ascii=False)
    assert "call_w" not in gespeichert, (
        "Die `call_id` steht nur in der Ansage — im Abzug hat sie nichts verloren"
    )
