"""Der Lauf als eigenstaendiges Ding — die drei Beschwerden aus dem Betrieb.

Alle drei hatten dieselbe Ursache: ein Zug der KI war ein Generator an einem
HTTP-Request und existierte nur, solange der Browser die Verbindung hielt.

1. *"Wenn ich den Chat verlasse oder den Browser schliesse, bricht die Anfrage
   ab."* — Der Generator bekam ein ``GeneratorExit``.
2. *"Die KI arbeitet nach dem Bestaetigen nicht weiter, man muss eine neue
   Nachricht schreiben."* — Die Bestaetigung laeuft ueber einen eigenen
   Endpunkt; es gab niemanden mehr, den sie aufwecken konnte.
3. *"Die KI soll Aufgaben von Anfang bis Ende durchfuehren."* — Unmoeglich,
   solange jede Unterbrechung das Ende ist.

Geprueft wird deshalb nicht "der Code laeuft durch", sondern genau das, was der
Betreiber gesehen hat.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiMessage,
    AiProvider,
    AiRun,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_run_broker, ai_run_service, ai_stream_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.role_service import set_user_roles


# Der gefaelschte Anbieter unten fasst ihn nie an.
_KEIN_CLIENT = object()


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Lauf",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _server(db: Session, name: str) -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{name}",
        status="running",
        container_name=f"msm-{name}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _grant(db: Session, user: User, *, server: Server, server_keys: tuple[str, ...],
           global_keys: tuple[str, ...] = ()) -> None:
    role = Role(name=f"lauf-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    for key in global_keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Lauf"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _fake_stream(monkeypatch: pytest.MonkeyPatch, runden: list[list[ProviderToolCall]],
                 *, text: str = "ok"):
    gesehen: list[list[dict]] = []
    zaehler = {"runde": 0}

    async def fake(_client, *, provider, api_key, messages, usage: StreamUsage,
                   tools=None, reasoning=False):
        del provider, api_key, reasoning
        gesehen.append([dict(item) for item in messages])
        if tools is None:
            usage.total_tokens = 10
            yield StreamChunk("content", text)
            return
        index = zaehler["runde"]
        zaehler["runde"] += 1
        if index < len(runden):
            usage.tool_calls = list(runden[index])
        usage.total_tokens = 10
        yield StreamChunk("content", text)

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)
    return gesehen


async def _lauf(db: Session, user: User, conversation: AiConversation,
                provider: AiProvider, *, content: str = "Mach was") -> AiRun:
    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=user, conversation=conversation, provider=provider,
        request_id=uuid4(), content=content, reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()
    return db.get(AiRun, run.id)


def _backup_aufruf(server: Server) -> ProviderToolCall:
    return ProviderToolCall(id="a", name="propose_backup", arguments={
        "server_id": server.id,
        "reason": "Vor der Aenderung absichern.",
        "expected_effect": "Ein Sicherungsstand liegt vor.",
    })


# ── 1. Der Lauf haengt an nichts ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_finishes_even_when_nobody_is_watching(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Fall "Browser zu": es sieht niemand zu, und der Lauf arbeitet trotzdem.

    Frueher war das Zusehen die Arbeit — der Generator *war* der Lauf. Wer den
    Tab schloss, toetete ihn mitten im Satz, und die halbe Antwort wurde als
    `failed` abgerechnet.
    """
    server = _server(db, "unbeobachtet")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [], text="Fertig.")

    # Kein einziger Abonnent — niemand sieht zu.
    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "completed"
    nachricht = db.get(AiMessage, run.message_id) if run.message_id else None
    if nachricht is None:
        nachricht = (
            db.query(AiMessage)
            .filter(AiMessage.conversation_id == conversation.id,
                    AiMessage.role == "assistant")
            .order_by(AiMessage.created_at.desc()).first()
        )
    assert nachricht is not None and nachricht.status == "complete"
    assert nachricht.content == "Fertig."


@pytest.mark.asyncio
async def test_a_late_watcher_still_sees_the_whole_answer(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer sich spaeter anhaengt, bekommt den vollstaendigen Stand — nicht den Rest.

    Genau der Fall "ich war auf einer anderen Seite und komme zurueck". Ohne
    Abzug saehe der Benutzer eine Antwort, die mittendrin beginnt.
    """
    server = _server(db, "spaet")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [], text="Der ganze Satz.")

    run = await _lauf(db, regular_user, conversation, provider)

    # Erst **jetzt** schaut jemand hin.
    ereignisse = [
        stueck async for stueck in ai_stream_service.lauf_verfolgen(run.id)
    ]
    verbunden = "".join(ereignisse)
    assert "event: snapshot" in verbunden
    assert "Der ganze Satz." in verbunden


# ── 2. Nach dem Bestaetigen laeuft es weiter ──────────────────────────────


@pytest.mark.asyncio
async def test_an_open_proposal_parks_the_run_instead_of_ending_it(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein bestaetigungspflichtiger Vorschlag beendet den Lauf nicht — er parkt ihn.

    Der Unterschied ist die ganze Beschwerde: ein beendeter Lauf kann nicht
    fortgesetzt werden, ein geparkter schon.
    """
    server = _server(db, "geparkt")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "waiting_confirmation"
    assert run.stop_reason == "awaiting_confirmation"
    vorschlag = db.query(AiActionProposal).one()
    assert vorschlag.status == "proposed"
    # Der Rueckweg ist geknuepft: der Bestaetigungsknopf weiss, wen er weckt.
    assert vorschlag.run_id == run.id
    # Und das Modell durfte den Vorgang vorher noch erklaeren — sonst stuende
    # die Karte im Chat und darueber nichts.
    nachricht = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id,
                AiMessage.role == "assistant")
        .order_by(AiMessage.created_at.desc()).first()
    )
    assert nachricht is not None and nachricht.content.strip()
    assert nachricht.status == "complete"


@pytest.mark.asyncio
async def test_the_run_continues_after_the_human_confirms(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Die Beschwerde selbst.** Nach dem Bestaetigen arbeitet die KI weiter.

    Vorher: Vorschlag, Klick, Aktion laeuft — und dann Stille. Der Benutzer
    musste eine neue Nachricht schreiben, damit die KI ueberhaupt erfuhr, wie
    ihr eigener Vorschlag ausgegangen ist.
    """
    from services import ai_proposal_service

    server = _server(db, "fortsetzung")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr(
        "services.backup_orchestrator.create_server_backup",
        lambda server_id, _db, name=None: type("B", (), {"id": 99})(),
    )
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])

    run = await _lauf(db, regular_user, conversation, provider)
    assert run.status == "waiting_confirmation"

    # Der Mensch bestaetigt — genau der Weg, den die Oberflaeche geht.
    vorschlag = db.query(AiActionProposal).one()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=vorschlag.id, user=regular_user
    )
    db.commit()
    ausgefuehrt, _ = ai_proposal_service.execute_proposal(
        db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token
    )
    db.commit()
    assert ausgefuehrt.status == "succeeded"

    # Ab hier ist der Lauf wieder dran. In der Anwendung weckt ihn
    # `execute_action`; hier wird derselbe Uebergang direkt gefahren.
    db.expire_all()
    assert ai_run_service.darf_fortsetzen(db, db.get(AiRun, run.id)) is True
    run = db.get(AiRun, run.id)
    run.status = "running"
    db.commit()
    gesehen = _fake_stream(monkeypatch, [], text="Backup liegt vor, Server laeuft weiter.")
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()

    run = db.get(AiRun, run.id)
    assert run.status == "completed"

    # Das Modell hat erfahren, wie es ausgegangen ist — sonst koennte es die
    # Aufgabe nicht zu Ende bringen.
    gemeldet = "\n".join(
        str(eintrag.get("content")) for runde in gesehen for eintrag in runde
    )
    assert "succeeded" in gemeldet
    assert "Meldung des Panels" in gemeldet

    # Und es hat danach tatsaechlich noch etwas gesagt — eine **zweite**
    # Assistenten-Nachricht, nicht ein nachtraeglich veraenderter alter Text.
    nachrichten = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id,
                AiMessage.role == "assistant")
        .order_by(AiMessage.created_at.asc()).all()
    )
    assert len(nachrichten) == 2
    assert nachrichten[-1].content == "Backup liegt vor, Server laeuft weiter."


@pytest.mark.asyncio
async def test_a_run_is_not_woken_while_a_second_proposal_is_still_open(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Karten, eine bestaetigt: der Lauf wartet auf die zweite.

    Liefe er nach der ersten los, meldete er eine halbe Arbeit als fertig —
    waehrend die zweite Karte noch offen im Chat steht.
    """
    from services import ai_proposal_service

    server = _server(db, "zweikarten")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create", "server.stop"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[
        _backup_aufruf(server),
        ProviderToolCall(id="b", name="propose_server_lifecycle", arguments={
            "server_id": server.id, "operation": "stop",
            "reason": "Danach anhalten.", "expected_effect": "Der Server steht.",
        }),
    ]])

    run = await _lauf(db, regular_user, conversation, provider)
    assert run.status == "waiting_confirmation"
    assert db.query(AiActionProposal).count() == 2

    monkeypatch.setattr(
        "services.backup_orchestrator.create_server_backup",
        lambda server_id, _db, name=None: type("B", (), {"id": 99})(),
    )
    erster = db.query(AiActionProposal).filter(
        AiActionProposal.tool_name == "propose_backup"
    ).one()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=erster.id, user=regular_user
    )
    db.commit()
    ai_proposal_service.execute_proposal(
        db, proposal_id=erster.id, user=regular_user, confirmation_token=token
    )
    db.commit()
    db.expire_all()

    assert ai_run_service.darf_fortsetzen(db, db.get(AiRun, run.id)) is False


@pytest.mark.asyncio
async def test_an_autonomous_action_does_not_park_the_run(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wo niemand bestaetigen muss, wird auch nicht gewartet.

    Der autonome Modus soll durchlaufen; geparkt wird nur, wo ein Mensch
    tatsaechlich gefragt ist.
    """
    from services import ai_autonomy_service

    server = _server(db, "autonom")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"),
           global_keys=("ai.autonomous.use",))
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=5, granted_by=regular_user.id,
    )
    db.commit()
    monkeypatch.setattr(
        "services.backup_orchestrator.create_server_backup",
        lambda server_id, _db, name=None: type("B", (), {"id": 99})(),
    )
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "completed"
    assert db.query(AiActionProposal).one().status == "succeeded"


@pytest.mark.asyncio
async def test_deleting_always_asks_even_with_autonomy(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*"Bis auf das Loeschen von Dingen."*

    Recht, Freigabe und Stundenbudget sind alle da — und trotzdem parkt der Lauf
    und wartet auf einen Menschen. Das ist die Sperre, die der autonome Modus
    nicht aufheben kann.
    """
    from services import ai_autonomy_service

    server = _server(db, "loeschen")
    _grant(db, regular_user, server=server, server_keys=("server.view",),
           global_keys=("ai.autonomous.use", "servers.delete"))
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=50, granted_by=regular_user.id,
    )
    db.commit()
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id="a", name="propose_server_delete", arguments={
            "server_id": server.id,
            "reason": "Wird nicht mehr gebraucht.",
            "expected_effect": "Der Server ist weg.",
        }),
    ]])

    run = await _lauf(db, regular_user, conversation, provider, content="Loesch den Server")

    assert run.status == "waiting_confirmation"
    vorschlag = db.query(AiActionProposal).one()
    assert vorschlag.status == "proposed"
    assert vorschlag.autonomous is False
    assert vorschlag.requires_confirmation is True
    # Und der Server steht noch.
    assert db.get(Server, server.id) is not None


# ── 3. Aufraeumen und Wiederanlauf ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_message_supersedes_a_parked_run(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer statt zu bestaetigen etwas Neues schreibt, hat die Richtung gewechselt.

    Ein alter Lauf, der Minuten spaeter durch einen nachtraeglichen Klick
    aufwacht und in denselben Chat weiterschreibt, waere ein Geist. Der
    **Vorschlag** bleibt ausfuehrbar — er weckt nur niemanden mehr.
    """
    server = _server(db, "ueberholt")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])
    alt = await _lauf(db, regular_user, conversation, provider)
    assert alt.status == "waiting_confirmation"

    _fake_stream(monkeypatch, [])
    await _lauf(db, regular_user, conversation, provider, content="Nein, lass doch")

    db.expire_all()
    assert db.get(AiRun, alt.id).status == "cancelled"
    assert db.get(AiRun, alt.id).stop_reason == "superseded"
    # Der Vorschlag selbst ist unberuehrt.
    assert db.query(AiActionProposal).one().status == "proposed"


def test_a_restart_closes_running_runs_honestly(db: Session, regular_user: User) -> None:
    """Ein Lauf im Zustand ``running`` hat den Neustart nicht ueberlebt.

    Er wird als fehlgeschlagen gefuehrt und **nicht** blind fortgesetzt: sein
    Arbeitsgedaechtnis endet mitten in einer Anbieterantwort, und ob ein
    Werkzeug schon lief, ist nicht mehr feststellbar. Ein halber Werkzeugaufruf,
    blind wiederholt, waere schlimmer als ein ehrlicher Abbruch.

    Geparkte Laeufe bleiben unangetastet — die warten auf einen Menschen.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    laufend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    wartend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    wartend.status = "waiting_confirmation"
    db.commit()

    assert ai_run_service.unterbrochene_laeufe_abgleichen(db) == 1

    db.expire_all()
    assert db.get(AiRun, laufend.id).status == "failed"
    assert db.get(AiRun, laufend.id).stop_reason == "process_restart"
    assert db.get(AiRun, wartend.id).status == "waiting_confirmation"


def test_scheduling_without_an_application_says_so(db: Session) -> None:
    """Ohne laufende Anwendung wird nichts geplant — und das wird gemeldet.

    Eine stille Nichtausfuehrung waere die schlechtere Antwort: der Lauf stuende
    dauerhaft auf ``running``, und beim naechsten Start wuerde er als abgebrochen
    gefuehrt, obwohl nie jemand angefangen hat.
    """
    ai_run_service.laufzeit_setzen(None, None)
    assert ai_run_service.lauf_starten(str(uuid4())) is False


@pytest.mark.asyncio
async def test_the_snapshot_is_a_still_picture_not_the_living_state(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Abzug beim Abonnieren darf nicht weiterwachsen.

    Gaebe der Vermittler das lebende Objekt heraus, saehe der Client denselben
    Text zweimal: einmal im Abzug und einmal als Ereignis danach.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen("lauf-1")
    abzug, _warteschlange = ai_run_broker.abonnieren("lauf-1")
    ai_run_broker.veroeffentlichen("lauf-1", "delta", {"content": "danach"})

    assert abzug.inhalt == ""


@pytest.mark.asyncio
async def test_a_failed_action_still_lets_the_run_speak(
    db: Session,
    regular_user: User,
    client,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein gescheiterter Vorgang ist genau der Moment, in dem eine Aussage zaehlt.

    Gefunden beim lokalen Durchlauf gegen ein echtes uvicorn, nicht hier: der
    Serverstart scheiterte (kein Docker), ``execute`` antwortete mit 409 — und
    der Lauf blieb fuer immer geparkt. Die Karte im Chat wurde rot, und die KI
    sagte kein Wort dazu. Der Benutzer haette raten muessen, ob noch etwas
    kommt.

    Der Grund war eine Reihenfolge: der Lauf wurde nur im Erfolgspfad geweckt.
    Jetzt weckt ihn **jede** Entscheidung — die Ausfuehrung hat stattgefunden,
    ihr Ausgang ist ein Ergebnis, und Ergebnisse gehoeren zurueck ins Gespraech.

    Geprueft wird ueber den HTTP-Endpunkt, weil genau dort der Fehler sass.
    """
    from services import ai_proposal_service

    server = _server(db, "fehlschlag")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])

    run = await _lauf(db, regular_user, conversation, provider)
    assert run.status == "waiting_confirmation"

    vorschlag = db.query(AiActionProposal).one()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=vorschlag.id, user=regular_user
    )
    db.commit()

    # Das Backup scheitert — so wie der Serverstart ohne Docker scheiterte.
    def platzt(*_args, **_kwargs):
        raise RuntimeError("Docker ist nicht erreichbar")

    monkeypatch.setattr("services.backup_orchestrator.create_server_backup", platzt)

    antwort = client.post(
        f"/api/ai/actions/{vorschlag.id}/execute",
        json={"confirmation_token": token},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert antwort.status_code == 409, antwort.text

    db.expire_all()
    assert db.query(AiActionProposal).one().status == "failed"
    # **Der Kern:** der Lauf steht nicht mehr auf "wartet auf einen Menschen".
    # Er wurde geweckt und darf dem Benutzer sagen, dass es schiefging.
    assert db.get(AiRun, run.id).status != "waiting_confirmation"
