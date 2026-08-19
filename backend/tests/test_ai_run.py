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
import json
from types import SimpleNamespace
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
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    ProviderToolCall,
    StreamChunk,
    StreamUsage,
)
from services.role_service import set_user_roles


# Der gefaelschte Anbieter unten fasst ihn nie an.
_KEIN_CLIENT = object()


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Lauf",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
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
                 *, text: str = "ok", denken: str = ""):
    """``denken`` ist der Denktext, den **jede** Runde vorweg liefert.

    Leer voreingestellt, damit die Tests daneben unverändert bleiben: ein
    Modell ohne Nachdenken schickt keinen einzigen ``reasoning``-Brocken, und
    genau das war der Normalfall, als diese Hilfe entstand.
    """
    gesehen: list[list[dict]] = []
    zaehler = {"runde": 0}

    async def fake(_client, *, provider, api_key, messages, usage: StreamUsage,
                   tools=None, tool_choice=None, reasoning=False,
                   reasoning_effort=None, cache_marke=False, model=None):
        del provider, api_key, reasoning
        gesehen.append([dict(item) for item in messages])
        if denken:
            yield StreamChunk("reasoning", denken)
        # Die Schlussrunde erkennt man an `tool_choice="none"`: der Katalog
        # fährt auch dort mit, damit der Zwischenspeicher greift.
        if tool_choice == "none":
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


def _letzte_antwort(db: Session, conversation: AiConversation) -> AiMessage:
    """Die zuletzt geschriebene Antwort dieser Unterhaltung.

    Über die Unterhaltung und nicht über ``run.message_id``: ein beendeter
    Lauf räumt dieses Feld ab (``_lauf_abschliessen``), weil die nächste
    Fortsetzung eine neue Nachricht anlegt.
    """
    nachricht = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id,
                AiMessage.role == "assistant")
        .order_by(AiMessage.created_at.desc()).first()
    )
    assert nachricht is not None, "Der Lauf hat keine Antwort hinterlassen"
    return nachricht


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
        stueck async for stueck in ai_run_broker.lauf_verfolgen(run.id)
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
async def test_a_run_never_stays_stuck_on_running_when_the_quota_runs_out(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Lauf, der nie endet, ist schlimmer als einer, der scheitert.

    Der Weg dorthin ist kein Sonderfall: ein Lauf parkt auf
    `waiting_confirmation`, in der Zwischenzeit ist das Tageskontingent
    aufgebraucht, der Mensch bestaetigt. `_segment_beginnen` reserviert dann
    Verbrauch — und `AiQuotaExceeded` verliess frueher `_segment_vorbereiten`
    **und** `segment_ausfuehren`. Die asyncio-Aufgabe starb still:
    `_lauf_abschliessen` lief nie, kein `error` ging an den Vermittler.

    Der Lauf stand danach fuer immer auf `running`, und die Oberflaeche zeigte
    einen tippenden Assistenten, der nie wieder etwas sagt — kein Fehler, den
    man wegklicken kann, und keiner, der von selbst aufhoert.

    Geprueft wird deshalb nicht die Ausnahme, sondern der **Endzustand**.
    """
    from services.ai_usage_service import AiQuotaExceeded

    server = _server(db, "kontingent-leer")
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

    # Der Mensch bestaetigt, der Lauf wird wieder aufgenommen — und genau
    # jetzt ist das Kontingent leer.
    db.expire_all()
    run = db.get(AiRun, run.id)
    run.status = "running"
    db.commit()

    def _kein_kontingent(*args, **kwargs):
        raise AiQuotaExceeded("daily_token_limit")

    monkeypatch.setattr(
        "services.ai_stream_service.reserve_ai_usage", _kein_kontingent
    )
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    db.expire_all()
    run = db.get(AiRun, run.id)
    assert run.status == "failed", "Der Lauf haengt auf `running` fest"
    # Und der Grund steht dran, statt dass jemand raten muss.
    assert run.stop_reason == "AI_QUOTA_DAILY_TOKEN_LIMIT"


@pytest.mark.asyncio
async def test_the_provider_wording_stays_in_the_log_and_never_reaches_the_user(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf gehoert einem Benutzer, das Konto dahinter dem Betreiber.

    Es gab eine Zwischenfassung, die den Satz des Anbieters als ``detail`` in
    dieses Ereignis legte, und die Oberflaeche zeigte ihn unter der Erklaerung
    an. Der Gedanke war richtig — „Der Anbieter hat die Anfrage abgelehnt" half
    bei einer Ferndiagnose niemandem —, der Weg war es nicht: was der Anbieter
    schreibt, beschreibt **sein Konto**, nicht die Anfrage. Kontingentstand,
    Kontoname, die Namen privater Fine-Tunes und der maskierte Schluessel gingen
    damit an jeden mit `ai.chat.use`.

    Die Redaktion faengt das nicht auf, und sie kann es nicht: sie sucht
    Schluesselmuster, und ``sk-pr***…xyZ4`` ist keines mehr — genau darum steht
    hier ein maskierter Schluessel im Text. Die Erklaerung traegt seither der
    Code, zu dem `de.json` einen eigenen Satz hat.
    """
    provider = _provider(db)
    conversation = _conversation(db, regular_user)

    geheim = (
        "You exceeded your current quota for org Muster GmbH (12,80 of 15,00 USD "
        "used). Key sk-pr***************************xyZ4, model ft:muster-intern-v3."
    )

    async def fake(*_args, **_kwargs):
        raise AiProviderRequestError("AI_PROVIDER_PAYMENT_REQUIRED", geheim)
        yield  # pragma: no cover - macht die Funktion zum Generator

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Was ist los?", reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    _abzug, warteschlange = ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    fehlerereignisse = []
    while not warteschlange.empty():
        ereignis, daten = warteschlange.get_nowait()
        if ereignis == "error":
            fehlerereignisse.append(daten)

    assert len(fehlerereignisse) == 1, "Genau ein Fehlerereignis erwartet"
    meldung = fehlerereignisse[0]
    # Der Code benennt den Fall — das ist die Diagnose, die bleiben muss.
    assert meldung["code"] == "AI_PROVIDER_PAYMENT_REQUIRED"
    assert "detail" not in meldung
    # Und kein Bruchstueck des Fremdtextes auf einem anderen Feld.
    hinaus = json.dumps(meldung, ensure_ascii=False)
    for bruchstueck in ("Muster GmbH", "sk-pr", "ft:muster-intern-v3", "15,00"):
        assert bruchstueck not in hinaus, f"{bruchstueck!r} ging an den Benutzer"


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


@pytest.mark.asyncio
async def test_a_finished_run_keeps_no_working_memory(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was der Lauf zum Arbeiten brauchte, überlebt ihn nicht.

    ``provider_messages`` ist das Arbeitsgedächtnis der Schleife, und darin
    steht mehr als die Frage des Benutzers: `build_provider_messages` hängt den
    **entschlüsselten** Gedächtnisblock als eigene Nachricht an. `state_json`
    ist eine gewöhnliche Textspalte ohne Verschlüsselung, und es gab keinen Weg,
    der sie je wieder leerte — weder `forget_memory`, das die verschlüsselte
    Zeile in `ai_memory_entries` entfernt, noch das Leeren des Chatverlaufs. Ein
    Eintrag, den der Benutzer nur über Profil > Memory hinterlegt und später
    gelöscht hat, stand danach dauerhaft im Klartext daneben.

    Geprüft wird über die getippte Frage: sie ist im Klartext Teil derselben
    Liste und damit der Nachweis, ob die Liste geleert wurde.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    _fake_stream(monkeypatch, [])

    run = await _lauf(
        db, regular_user, conversation, provider,
        content="Wie viel Arbeitsspeicher hat mein Server?",
    )

    assert run.status == "completed"
    assert ai_run_service.zustand_lesen(run)["provider_messages"] == []
    assert "Arbeitsspeicher hat mein Server" not in (run.state_json or "")


def test_a_parked_run_keeps_its_working_memory(
    db: Session, regular_user: User
) -> None:
    """Die Gegenprobe — und die Grenze, an der das Leeren zum Fehler würde.

    Ein Lauf auf ``waiting_confirmation`` hat nicht aufgehört, sondern wartet
    auf einen Menschen. Seine Nachrichten sind genau das, womit er nach der
    Bestätigung weitermacht; sie wegzuwerfen hieße, ihn zu töten. Deshalb steht
    die Prüfung auf den Endzustand in `arbeitsspeicher_leeren` selbst und nicht
    bei ihren drei Aufrufern.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    run = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand(
            [{"role": "user", "content": "Merkzettel"}], request_id=str(uuid4())
        ),
    )
    run.status = "waiting_confirmation"
    db.commit()

    ai_run_service.arbeitsspeicher_leeren(run)

    assert ai_run_service.zustand_lesen(run)["provider_messages"]


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
async def test_ein_verworfener_kanal_weckt_wer_zusieht(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wer einen Kanal wegwirft, muss seine Zuhörer entlassen.

    Die Kanalgrenze hat zwei Wege. Der erste räumt beendete Kanäle ohne Zuhörer
    weg — harmlos. Der zweite greift, wenn alle Kanäle laufen oder beobachtet
    werden, und nimmt dann den ältesten; der hatte bis zuletzt **keine** Prüfung
    auf Zuhörer und rief kein ``beenden``.

    Die Folge sah der Benutzer, nicht das Protokoll: ``lauf_verfolgen`` steht in
    ``await warteschlange.get()`` ohne Frist. Ohne das Abbruchsignal kommt in
    diese Warteschlange nie wieder etwas — der weiterlaufende Lauf legt sich
    einen neuen Kanal an, und auch sein abschließendes ``beenden`` trifft nur
    den. Die SSE-Verbindung blieb offen, im Browser fiel ``setStreaming(false)``
    nie, und die Eingabe blieb gesperrt: ein ewig tippender Assistent.

    Die Grenze wird für den Test heruntergesetzt statt 256 Kanäle anzulegen —
    geprüft wird das Verhalten an der Grenze, nicht die Zahl.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    monkeypatch.setattr(ai_run_broker, "MAX_KANAELE", 2)

    ai_run_broker.eroeffnen("lauf-alt")
    _abzug, warteschlange = ai_run_broker.abonnieren("lauf-alt")

    # Zwei weitere laufende Kanäle: keiner ist beendet, also fällt die
    # Aufräumung in den zweiten Zweig und nimmt den ältesten — den mit dem
    # Zuhörer.
    ai_run_broker.eroeffnen("lauf-neu-1")
    ai_run_broker.eroeffnen("lauf-neu-2")

    assert "lauf-alt" not in ai_run_broker._KANAELE, (
        "Der älteste Kanal hätte der Grenze weichen müssen"
    )
    ereignis, daten = await asyncio.wait_for(warteschlange.get(), timeout=1.0)
    assert (ereignis, daten) == (None, None), (
        "Der Zuhörer des verworfenen Kanals wartet weiter — seine SSE-Verbindung "
        "hängt für immer"
    )


def test_text_around_a_tool_call_does_not_run_together() -> None:
    """Zwischen zwei Textabschnitten liegt eine Leerzeile, nicht nichts.

    Aus dem Betrieb, aus einer Berichtsmail: „…damit die Mail nur bestaetigte
    Informationen enthaelt.Ich pruefe jetzt den Status…“. Der Vermittler fuegte
    die Abschnitte mit ``"".join`` zusammen — richtig fuer die Token-Bruchstuecke
    *innerhalb* eines Abschnitts, falsch *zwischen* zweien. Dazwischen liegt ein
    Werkzeugaufruf, und der Prompt verlangt vor jedem einen ganzen Satz.

    Im Chat fiel es nie auf, weil der die Abschnitte einzeln zeichnet. Nur wer
    ``inhalt`` weiterverwendet — die Mail, der Anbieter, der Verlauf — sah es.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen("lauf-trenner")

    # Zwei Bruchstuecke eines Satzes: die gehoeren nahtlos aneinander.
    ai_run_broker.veroeffentlichen("lauf-trenner", "delta", {"content": "Ich sehe "})
    ai_run_broker.veroeffentlichen("lauf-trenner", "delta", {"content": "nach."})
    ai_run_broker.veroeffentlichen(
        "lauf-trenner", "tool", {"name": "server_uebersicht", "gruppe": "server"}
    )
    ai_run_broker.veroeffentlichen("lauf-trenner", "delta", {"content": "Drei laufen."})

    abzug, _ = ai_run_broker.abonnieren("lauf-trenner")
    assert abzug.inhalt == "Ich sehe nach.\n\nDrei laufen."


def test_thoughts_keep_their_place_in_the_section_list() -> None:
    """Der Denktext ist ein Abschnitt an seiner Stelle, kein Feld daneben.

    Er lag zuletzt flach neben ``abschnitte``, und die Oberfläche konnte ihn
    deshalb nur als **einen** Kasten über allem zeichnen: die Gedanken der
    dritten Runde standen über dem Text der ersten, der dort seit zwölf
    Sekunden stand.

    ``denken`` bleibt daneben bestehen — als Ableitung, wie ``inhalt`` es
    längst ist. Genau diese Zeichenkette geht in ``AiMessage.reasoning`` und
    von dort in die Berichtsmail; sie muss Zeichen für Zeichen dieselbe sein
    wie vorher.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen("lauf-denken")

    ai_run_broker.veroeffentlichen("lauf-denken", "reasoning", {"content": "Ich pruefe "})
    ai_run_broker.veroeffentlichen("lauf-denken", "reasoning", {"content": "die Ports."})
    ai_run_broker.veroeffentlichen("lauf-denken", "delta", {"content": "Ich sehe nach."})
    ai_run_broker.veroeffentlichen(
        "lauf-denken", "tool", {"name": "server_uebersicht", "gruppe": "server"}
    )
    ai_run_broker.veroeffentlichen("lauf-denken", "reasoning", {"content": "Jetzt die Logs."})

    abzug, _ = ai_run_broker.abonnieren("lauf-denken")
    assert [abschnitt["art"] for abschnitt in abzug.abschnitte] == [
        "denken", "text", "tool", "denken",
    ]
    # Die Bruchstücke einer Runde gehören nahtlos aneinander, die Runden
    # bleiben getrennt.
    assert abzug.abschnitte[0]["inhalt"] == "Ich pruefe die Ports."
    assert abzug.abschnitte[3]["inhalt"] == "Jetzt die Logs."
    # Die Ableitung: dieselbe Zeichenkette wie beim früheren flachen Feld.
    assert abzug.denken == "Ich pruefe die Ports.Jetzt die Logs."
    assert abzug.als_ereignis()["reasoning"] == "Ich pruefe die Ports.Jetzt die Logs."


def test_a_new_segment_drops_the_thoughts_with_the_sections() -> None:
    """Ein neues Segment schreibt eine neue Nachricht — auch für die Gedanken.

    Das Leeren stand früher als eigene Zeile im Vermittler. Es fällt jetzt mit
    der Abschnittsliste weg, und genau das muss zugesichert bleiben: bliebe der
    Denktext stehen, trüge die Fortsetzung nach einer Bestätigung die
    Überlegungen der vorherigen Nachricht.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen("lauf-segment")
    ai_run_broker.veroeffentlichen("lauf-segment", "reasoning", {"content": "Erste Runde."})

    ai_run_broker.neues_segment("lauf-segment")

    abzug, _ = ai_run_broker.abonnieren("lauf-segment")
    assert abzug.abschnitte == []
    assert abzug.denken == ""


def test_a_stored_thought_section_is_redacted_like_the_flat_field() -> None:
    """Die Schwärzung darf die neue Gliederung nicht verpassen.

    ``message.reasoning`` wird beim Abschluss geschwärzt und gekürzt — ein
    Modell kann in seinen Überlegungen denselben Schlüssel wiederholen wie in
    der Antwort. Wandert derselbe Text zusätzlich als Abschnitt in
    ``sections_json`` und zeichnet die Oberfläche von dort, wäre die
    Schwärzung ohne diesen Schritt stillschweigend ausgehebelt.
    """
    roh = ai_run_broker.denk_abschnitt("Der Key sk-abcdefghijklmnopqrst passt.")

    abgelegt = ai_stream_service._abschnitt_fuer_ablage(roh)

    assert "sk-abcdefghijklmnopqrst" not in abgelegt["inhalt"]
    assert "[REDACTED_TOKEN]" in abgelegt["inhalt"]
    # Text und Werkzeuge gehen unverändert durch — sie tragen keine eigene
    # Schwärzung, und hier eine zu erfinden wäre eine zweite Wahrheit.
    unveraendert = ai_run_broker.text_abschnitt("Alles in Ordnung.")
    assert ai_stream_service._abschnitt_fuer_ablage(unveraendert) == unveraendert


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


# ── Drei Luecken, die beim Durchsehen auffielen ──────────────────────────


@pytest.mark.asyncio
async def test_reading_a_server_leaves_a_trace(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Schreibseite war lueckenlos protokolliert, die Leseseite gar nicht.

    Damit war "hat die KI meine Logs gelesen?" nicht beantwortbar — obwohl
    `read_server_logs` bis zu 24.000 Zeichen aus einem fremden Server holt.

    Geprueft wird zugleich die Entdoppelung: dieselbe Abfrage neunmal in einer
    Runde ist **ein** Eintrag. Ein Protokoll, das man wegen Rauschen nicht mehr
    liest, ist keins.
    """
    from models import AuditLog

    server = _server(db, "protokoll")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id=f"c{i}", name="read_server_status",
                         arguments={"server_id": server.id})
        for i in range(9)
    ] + [
        # Ein globaler Lesezugriff daneben — der gehoert **nicht** ins Audit:
        # er bewegt sich im eigenen Bereich des Benutzers.
        ProviderToolCall(id="global", name="list_my_servers", arguments={}),
    ]])

    await _lauf(db, regular_user, conversation, provider)

    eintraege = db.query(AuditLog).filter(AuditLog.action == "ai.tool.read").all()
    assert len(eintraege) == 1, [e.details for e in eintraege]
    eintrag = eintraege[0]
    assert eintrag.user_id == regular_user.id
    assert eintrag.target_type == "server"
    assert str(server.id) in str(eintrag.target_id)
    assert "read_server_status" in str(eintrag.details)
    assert eintrag.origin == "ai"


# ── Worum ging es? Der Serverbezug des Laufs ──────────────────────────────


@pytest.mark.asyncio
async def test_a_run_remembers_which_server_it_looked_into(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf haelt fest, worum es gerade geht.

    Eine Unterhaltung kann das nicht: ihr Thema wechselt. Der Lauf ist genau
    die Spanne, in der ein Thema gilt.

    Die zweite Runde darf den Bezug nicht wieder abraeumen. `list_my_servers`
    sagt ueber das Thema nichts aus — es ist die Frage *welche gibt es*, nicht
    *um welchen geht es*. Eine Runde ohne serverbezogenes Werkzeug ist stumm,
    nicht widersprechend; genau dafuer steht der Schutz in
    `serverbezug_merken`, und ohne zwei Runden bliebe er ungeprueft.
    """
    server = _server(db, "thema")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _fake_stream(monkeypatch, [
        [ProviderToolCall(id="a", name="read_server_status",
                          arguments={"server_id": server.id})],
        [ProviderToolCall(id="b", name="list_my_servers", arguments={})],
    ])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.last_server_id == server.id


@pytest.mark.asyncio
async def test_a_server_the_model_may_not_see_never_becomes_the_topic(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine bloss genannte Nummer ist kein Serverbezug.

    Der Bezug wird spaeter Wissen aus dem Panel in den Kontext ziehen. Zaehlte
    ein **gescheiterter** Aufruf mit, waere das Feld ein Weg, sich Zugang zu
    erfinden: Nummer nennen, Fehlermeldung hinnehmen, Thema gesetzt.

    Genau bei einem fremden Server scheitert `_resolve_server` ja — nur die
    erfolgreiche Rueckkehr belegt `server.view`.
    """
    fremder = _server(db, "fremd")
    eigener = _server(db, "eigen")
    _grant(db, regular_user, server=eigener, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id="a", name="read_server_status",
                         arguments={"server_id": fremder.id}),
    ]])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.last_server_id is None


@pytest.mark.asyncio
async def test_the_topic_survives_the_next_message(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Und jetzt starte ihn neu" nennt keinen Server — gemeint ist der vorige.

    Ohne dieses Erbe endete der Bezug an jeder Nachrichtengrenze, und ein Chat
    ueber genau einen Server haette abwechselnd Bezug und keinen.

    Die zweite Nachricht fasst dabei bewusst gar kein Werkzeug an: nur so ist
    zu sehen, dass der Bezug vom Vorgaenger kommt und nicht neu entsteht.
    """
    server = _server(db, "erbe")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id="a", name="read_server_status",
                         arguments={"server_id": server.id}),
    ]])
    erster = await _lauf(db, regular_user, conversation, provider)
    assert erster.last_server_id == server.id

    _fake_stream(monkeypatch, [])
    zweiter = await _lauf(db, regular_user, conversation, provider, content="Danke!")

    assert zweiter.id != erster.id
    assert zweiter.last_server_id == server.id


@pytest.mark.asyncio
async def test_a_write_proposal_also_sets_the_topic(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch ein Schreibvorschlag belegt den Bezug.

    `create_proposal` geht durch dieselbe Rechtepruefung wie ein Lesewerkzeug.
    Ohne diese Stelle verloere ein Lauf sein Thema ausgerechnet dann, wenn er
    am meisten damit vorhat — und der geparkte Lauf wuesste nach der
    Bestaetigung nicht mehr, worum es ging.
    """
    server = _server(db, "vorschlag")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_backup_aufruf(server)]])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "waiting_confirmation"
    assert run.last_server_id == server.id


@pytest.mark.asyncio
async def test_the_machines_manual_reaches_the_provider_in_the_same_round(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Ganze am laufenden Zug, nicht nur an den Bausteinen.

    Der Benutzer fragt "warum kommt keiner rein?" und nennt keinen Server. Beim
    Anlegen des Laufs weiss deshalb niemand, um welche Anlage es geht — der
    Kontext dieser ersten Anfrage kann ihre Betriebsanleitung gar nicht
    enthalten. Erst das Lesewerkzeug klaert die Nummer.

    Geprueft wird die Reihenfolge: **nicht** in der ersten Anfrage, **schon**
    in der zweiten. Ohne den Nachtrag antwortete das Modell auf genau die
    Frage, fuer die der Satz gedacht ist, ohne ihn zu kennen.
    """
    from services import ai_memory_service

    server = _server(db, "handbuch")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.config.write"),
           global_keys=("ai.memory.use",))
    ai_memory_service.set_preference(db, regular_user, True)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server_shared", server_id=server.id,
        key="whitelist", value="Nach jedem Neustart die Whitelist neu laden.",
        origin="ai",
    )
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    gesehen = _fake_stream(monkeypatch, [[
        ProviderToolCall(id="a", name="read_server_status",
                         arguments={"server_id": server.id}),
    ]])

    await _lauf(db, regular_user, conversation, provider,
                content="Warum kommt keiner rein?")

    def enthaelt(runde: list[dict]) -> bool:
        return any("Whitelist neu laden" in str(m.get("content")) for m in runde)

    assert len(gesehen) >= 2
    assert not enthaelt(gesehen[0])
    assert enthaelt(gesehen[1])


def test_a_deleted_server_does_not_take_the_run_with_it(
    db: Session, regular_user: User
) -> None:
    """Der Lauf gehoert der Unterhaltung, nicht dem Server.

    Mit `CASCADE` haette das Loeschen eines Servers rueckwirkend jeden Chat
    ausgeduennt, in dem je jemand nach ihm gefragt hat — derselbe Fehler, den
    `20260810_06` fuer die Aktionsvorschlaege behoben hat. Die Zusage steht im
    Schema (`test_schema_constraints.py`); hier steht, was sie bedeutet.
    """
    server = _server(db, "vergaenglich")
    conversation = _conversation(db, regular_user)
    run = ai_run_service.lauf_anlegen(
        db,
        conversation_id=conversation.id,
        user_id=regular_user.id,
        provider_id=_provider(db).id,
        message_id=str(uuid4()),
        reasoning=False,
        zustand={},
        last_server_id=server.id,
    )
    db.commit()

    db.delete(server)
    db.commit()
    db.expire_all()

    ueberlebend = db.get(AiRun, run.id)
    assert ueberlebend is not None
    assert ueberlebend.last_server_id is None


@pytest.mark.asyncio
async def test_loop_detection_survives_a_question(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine Rueckfrage darf die Schleifenerkennung nicht zuruecksetzen.

    Der Laufzustand wurde bei `ask_user` geschrieben und **nie gelesen** — halb
    gebaut, und die Haelfte täuschte. Folge: das Modell liest eine Datei, fragt
    etwas, liest sie nach der Antwort erneut, und gezaehlt wird wieder bei null.
    So kommt man nie an die Grenze.

    Der Nachfolger erbt deshalb die Signaturen. Die **Rundenbudgets** erbt er
    ausdruecklich nicht: eine Klaerung ist kein Fehler des Benutzers.
    """
    server = _server(db, "frage-erbe")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    _fake_stream(monkeypatch, [
        [ProviderToolCall(id="a", name="read_server_status",
                          arguments={"server_id": server.id})],
        [ProviderToolCall(id="b", name="ask_user", arguments={
            "question": "Welchen Server meinst du?",
            "options": [{"label": "A"}, {"label": "B"}],
        })],
    ])
    erster = await _lauf(db, regular_user, conversation, provider)
    assert erster.status == "waiting_user"

    _fake_stream(monkeypatch, [])
    zweiter = await _lauf(db, regular_user, conversation, provider, content="Den ersten")

    db.expire_all()
    # Der beantwortete Lauf ist nicht "abgebrochen, ueberholt" — er wurde
    # beantwortet. Im Protokoll ist das ein Unterschied.
    alt = db.get(AiRun, erster.id)
    assert alt.status == "completed"
    assert alt.stop_reason == "answered"

    signaturen = ai_run_service.zustand_lesen(db.get(AiRun, zweiter.id))["tool_signatures"]
    assert any("read_server_status" in schluessel for schluessel in signaturen), signaturen
    # Budget dagegen frisch.
    assert ai_run_service.zustand_lesen(db.get(AiRun, zweiter.id))["rounds"] == 0


@pytest.mark.asyncio
async def test_a_run_that_ran_out_of_rounds_says_so(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Lauf am Rundenende sieht aus wie einer, der fertig war — ist er aber nicht.

    `stop_reason='budget'` stand als vorgesehener Wert im Modell und wurde
    nirgends gesetzt. Wer das Protokoll liest, soll den Unterschied sehen:
    "erledigt" gegen "durfte nicht mehr und hat aus dem geantwortet, was da war".
    """
    server = _server(db, "budgetende")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.console.read"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    monkeypatch.setattr("services.docker_service.logs", lambda *_a, **_k: "zeile")
    # Jede Runde etwas anderes, damit die Schleifenerkennung nicht vorher greift.
    _fake_stream(monkeypatch, [
        [ProviderToolCall(id=f"r{i}", name="read_server_logs",
                          arguments={"server_id": server.id, "lines": 10 + i})]
        for i in range(ai_stream_service.MAX_TOOL_ROUNDS + 3)
    ])

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "completed"
    assert run.stop_reason == "budget"


@pytest.mark.asyncio
async def test_a_normal_run_is_not_marked_as_out_of_budget(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe — sonst stuende an jedem Lauf "budget"."""
    server = _server(db, "budgetok")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [], text="Kurz und fertig.")

    run = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "completed"
    assert run.stop_reason == "done"


# ── 4. Ein Lauf gehoert immer nur einem ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_superseded_run_cannot_report_itself_as_completed(
    db: Session, regular_user: User
) -> None:
    """Ein Endzustand ist endgueltig — auch gegenueber dem eigenen Segment.

    Der Fall aus dem Betrieb: die KI streamt, der Benutzer schiebt ungeduldig
    eine zweite Nachricht nach. Der erste Lauf wird auf 'cancelled/superseded'
    gesetzt. Bringt seine Aufgabe die angefangene Runde trotzdem zu Ende,
    meldete sie danach 'completed/done' und ueberschrieb den Abbruch — im
    Protokoll stand ein Lauf als erledigt, den der Benutzer laengst ueberholt
    hatte.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    laufend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    db.commit()
    assert laufend.status == "running"

    ai_run_service.vorgaenger_abloesen(db, conversation_id=conversation.id)
    db.commit()

    # Und jetzt meldet sich die alte Aufgabe zurueck, als waere nichts gewesen.
    ai_stream_service._lauf_abschliessen(
        laufend.id, status="completed", stop_reason="done"
    )

    db.expire_all()
    ueberholt = db.get(AiRun, laufend.id)
    assert ueberholt.status == "cancelled", "Der abgeloeste Lauf hat sich wiederbelebt"
    assert ueberholt.stop_reason == "superseded"


@pytest.mark.asyncio
async def test_a_new_message_cancels_the_task_of_a_running_predecessor(
    db: Session, regular_user: User
) -> None:
    """Abloesen heisst anhalten, nicht nur umetikettieren.

    Der Status wurde gesetzt, die asyncio-Aufgabe lief weiter: sie fuehrte
    Werkzeuge aus, legte Vorschlaege in dieselbe Unterhaltung und antwortete
    **nach** der neuen Frage des Benutzers.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    laufend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    db.commit()

    laeuft_schon = asyncio.Event()

    async def _endlos() -> None:
        laeuft_schon.set()
        await asyncio.sleep(3600)

    aufgabe = asyncio.ensure_future(_endlos())
    await laeuft_schon.wait()
    ai_run_service.laufzeit_setzen(asyncio.get_running_loop(), None)
    ai_run_service._AUFGABEN[laufend.id] = aufgabe
    try:
        ai_run_service.vorgaenger_abloesen(db, conversation_id=conversation.id)
        db.commit()
        # Der Abbruch wird auf der Schleife zugestellt, nicht im Aufrufer.
        await asyncio.sleep(0.05)
        assert aufgabe.cancelled(), "Die Aufgabe des abgeloesten Laufs arbeitet weiter"
    finally:
        aufgabe.cancel()
        ai_run_service.zuruecksetzen_fuer_tests()
        ai_run_service.laufzeit_setzen(None, None)


def test_the_cancelled_predecessor_does_not_erase_why_it_was_cancelled(
    db: Session, regular_user: User
) -> None:
    """„Ueberholt" und „Panel faehrt herunter" muessen unterscheidbar bleiben.

    Der abgeloeste Lauf steht auf 'cancelled/superseded'. Seine Aufgabe bekommt
    den Abbruch erst am naechsten Haltepunkt zugestellt und meldet dann aus dem
    CancelledError-Zweig ihrerseits 'cancelled', aber mit stop_reason
    'cancelled'. Der Status ist in beiden Faellen derselbe — ein Waechter, der
    zusaetzlich auf einen *anderen* Status prueft, faellt hier durch und
    ueberschreibt 'superseded'. Danach steht im Protokoll nicht mehr, ob der
    Benutzer den Lauf ueberholt hat oder ob der Prozess herunterfuhr.

    Der Test faehrt genau den Weg, den `test_a_new_message_cancels_the_task...`
    mit seiner Ersatzaufgabe nicht faehrt.
    """
    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    laufend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    db.commit()

    ai_run_service.vorgaenger_abloesen(db, conversation_id=conversation.id)
    db.commit()

    # Das ist es, was der Vorgaenger meldet, wenn ihn der Abbruch erreicht.
    ai_stream_service._lauf_abschliessen(
        laufend.id, status="cancelled", stop_reason="cancelled"
    )

    db.expire_all()
    run = db.get(AiRun, laufend.id)
    assert run.status == "cancelled"
    assert run.stop_reason == "superseded", run.stop_reason


@pytest.mark.asyncio
async def test_a_superseded_run_performs_no_write_actions(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der teuerste Teil des Geistes: er handelt noch.

    Der Abbruch einer Aufgabe wird erst am naechsten Haltepunkt zugestellt —
    und zwischen dem Ende des Anbieterstroms und dem Anlegen der Vorschlaege
    liegt keiner. Ein ueberholter Lauf legte deshalb noch Aktionskarten in die
    Unterhaltung des Nachfolgers und fuehrte autonome Aktionen wirklich aus.
    """
    server = _server(db, "abgeloest-schreibt")
    _grant(db, regular_user, server=server,
           server_keys=("server.view", "server.backups.create"))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr(
        "services.backup_orchestrator.create_server_backup",
        lambda server_id, _db, name=None: type("B", (), {"id": 99})(),
    )

    stand = {"abgeloest": False}

    async def fake(_client, *, provider, api_key, messages, usage: StreamUsage,
                   tools=None, tool_choice=None, reasoning=False,
                   reasoning_effort=None, cache_marke=False, model=None):
        del provider, api_key, messages, reasoning, reasoning_effort
        usage.total_tokens = 10
        if tool_choice != "none" and tools and not stand["abgeloest"]:
            usage.tool_calls = [_backup_aufruf(server)]
            yield StreamChunk("content", "ich sichere gleich")
            # Genau jetzt schreibt der Benutzer etwas Neues: der Lauf ist ab
            # hier abgeloest, hat seine Werkzeugrunde aber schon in der Hand.
            ai_run_service.vorgaenger_abloesen(db, conversation_id=conversation.id)
            db.commit()
            stand["abgeloest"] = True
            return
        yield StreamChunk("content", "ok")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Sicher den Server", reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    db.expire_all()
    assert db.query(AiActionProposal).count() == 0, "Ein abgeloester Lauf hat gehandelt"
    ueberholt = db.get(AiRun, run.id)
    assert ueberholt.status == "cancelled"
    assert ueberholt.stop_reason == "superseded"


def test_two_confirmations_at_once_plan_only_one_segment(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Klicks, zwei Threads, ein Lauf — und trotzdem nur ein Segment.

    Die Sperre las ``_AUFGABEN``, eingetragen wurde die Aufgabe aber erst in der
    Koroutine ``_starten`` auf der Ereignisschleife. Beide Aufrufer sahen darum
    einen leeren Eintrag und planten je ein Segment: zwei Anbieteraufrufe auf
    demselben Zustand, zwei Abrechnungen und dieselbe Schreibaktion zweimal.

    Nachgestellt ohne Threads, weil das Fenster genau dasselbe ist: die Schleife
    laeuft zwischen den beiden Planungen kein einziges Mal.
    """
    gestartet: list[str] = []

    async def _fake_segment(run_id: str, *, client=None) -> None:
        del client
        gestartet.append(run_id)

    monkeypatch.setattr(ai_stream_service, "segment_ausfuehren", _fake_segment)
    schleife = asyncio.new_event_loop()
    run_id = str(uuid4())
    try:
        ai_run_service.laufzeit_setzen(schleife, None)
        assert ai_run_service.lauf_starten(run_id) is True
        assert ai_run_service.lauf_starten(run_id) is True
        schleife.run_until_complete(asyncio.sleep(0.05))
    finally:
        ai_run_service.laufzeit_setzen(None, None)
        ai_run_service.zuruecksetzen_fuer_tests()
        schleife.close()

    assert gestartet == [run_id], f"Ein Lauf, zwei Segmente: {gestartet}"


def test_the_waiting_states_have_exactly_one_home(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``WARTEND`` muss tragen, nicht danebenstehen.

    Die Wartezustaende standen viermal woertlich im Code: in der Konstante, im
    CheckConstraint und in zwei Abfragen. Wer einen Zustand ergaenzte, trug ihn
    in die Konstante ein — mit null Wirkung, weil niemand sie las.

    Geprueft wird deshalb die Kopplung selbst: wird die Konstante enger, muessen
    die Abfragen enger werden.
    """
    from models.ai_run import ZUSTAENDE

    conversation = _conversation(db, regular_user)
    provider = _provider(db)
    wartend = ai_run_service.lauf_anlegen(
        db, conversation_id=conversation.id, user_id=regular_user.id,
        provider_id=provider.id, message_id=None, reasoning=False,
        zustand=ai_run_service.leerer_zustand([], request_id=str(uuid4())),
    )
    wartend.status = "waiting_confirmation"
    db.commit()

    monkeypatch.setattr(ai_run_service, "WARTEND", ("waiting_user",))

    assert ai_run_service.aktiver_lauf(db, user_id=regular_user.id) is None, (
        "aktiver_lauf haelt eine eigene Kopie der Wartezustaende"
    )
    ai_run_service.vorgaenger_abloesen(db, conversation_id=conversation.id)
    db.commit()
    db.expire_all()
    assert db.get(AiRun, wartend.id).status == "waiting_confirmation", (
        "vorgaenger_abloesen haelt eine eigene Kopie der Wartezustaende"
    )

    # Hier stand einmal eine dritte Zusicherung, die den CheckConstraint der
    # Tabelle mit einer aus ZUSTAENDE gebauten Zeichenkette verglich. Sie ist
    # bewusst geloescht: seit die Einschraenkung selbst aus ZUSTAENDE erzeugt
    # wird, vergleicht sie dieselbe Liste mit sich selbst und kann per
    # Konstruktion nie umfallen. Dass die Datenbankgrenze und die Konstante
    # dieselbe Quelle haben, ist eine Wartbarkeitsaenderung ohne Testwirkung —
    # und eine Zusicherung, die immer haelt, behauptet nur Absicherung.


# ── 6. Der Denkblock überlebt das Neuladen ────────────────────────────────


@pytest.mark.asyncio
async def test_the_reasoning_survives_a_reload_without_carrying_credentials(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der aufklappbare Denkblock hängt an genau einer Zuweisung — und die war ungeprüft.

    Nach einem F5 kommt er nicht aus dem Strom, sondern aus ``AiMessage.reasoning``
    über das Feld ``reasoning`` der Antwortform. Fällt eines von beiden weg,
    verschwindet der Block still; die Testsuite bliebe grün.

    Im selben Zug die zweite Zusage derselben Zeile: ein Modell wiederholt in
    seinen Überlegungen genauso einen Schlüssel wie im Text, und gespeichert
    wird er nicht.
    """
    from routers.ai_chat import _message_response

    server = _server(db, "denkspur")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(
        monkeypatch, [], text="Fertig.",
        denken="In der Datei steht RCON_PASSWORD=hunter2, das erklärt es.",
    )

    await _lauf(db, regular_user, conversation, provider)

    nachricht = _letzte_antwort(db, conversation)
    assert nachricht.reasoning, "Der Denktext wurde gar nicht erst gespeichert"
    assert "das erklärt es" in nachricht.reasoning
    assert "hunter2" not in nachricht.reasoning
    assert "RCON_PASSWORD=[REDACTED]" in nachricht.reasoning
    # Und der Weg, den ein Neuladen wirklich nimmt.
    assert _message_response(nachricht).reasoning == nachricht.reasoning


@pytest.mark.asyncio
async def test_the_thoughts_of_two_rounds_do_not_run_together(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwischen den Gedanken zweier Runden liegt eine Leerzeile, nicht nichts.

    Der Antworttext bekam sie an der Rundennaht längst, mit derselben
    Begründung — dazwischen liegt ein Werkzeugaufruf, und ein Satzende trifft
    ohne Trenner auf einen Satzanfang: „…sehe ich mir zuerst die Logs
    an.Ich sehe mir…". Für den Denktext passierte an derselben Stelle nichts,
    und ``"".join(thoughts)`` geht genauso in ``AiMessage.reasoning`` und von
    dort in die Berichtsmail.

    Zweite Zusage derselben Naht: der Umbruch geht **live** mit hinaus. Stünde
    er nur im gespeicherten Text, läse der Benutzer während des Laufs etwas
    anderes als nach dem Neuladen.
    """
    server = _server(db, "denknaht")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    # Eine Werkzeugrunde, danach die Antwortrunde: zweimal derselbe Gedanke,
    # und genau dazwischen liegt die Naht.
    _fake_stream(
        monkeypatch,
        [[ProviderToolCall(id="a", name="read_server_status",
                           arguments={"server_id": server.id})]],
        text="Fertig.",
        denken="Ich sehe mir zuerst die Logs an.",
    )

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Mach was", reasoning=True,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    abzug, warteschlange = ai_run_broker.abonnieren(run.id)
    del abzug
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()

    live = ""
    while not warteschlange.empty():
        ereignis, daten = warteschlange.get_nowait()
        if ereignis == "reasoning":
            live += str(daten.get("content") or "")

    nachricht = _letzte_antwort(db, conversation)
    assert nachricht.reasoning == (
        "Ich sehe mir zuerst die Logs an.\n\nIch sehe mir zuerst die Logs an."
    ), "Die Gedanken zweier Runden kleben aneinander"
    assert live == nachricht.reasoning, (
        "Live stand ein anderer Denktext als nach dem Neuladen"
    )


@pytest.mark.asyncio
async def test_a_round_without_thoughts_leaves_no_empty_thought_box(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Umbruch kommt mit dem nächsten Gedanken — oder gar nicht.

    Er wird an der Naht nur bestellt und erst beim ersten Gedanken der neuen
    Runde eingelöst. Das ist kein Umweg, sondern der Unterschied zwischen einem
    Trenner und einem Abschnitt aus nichts: viele Modelle denken nur in der
    ersten Runde. Ginge der Umbruch sofort als eigenes ``reasoning``-Ereignis
    hinaus, entstünde beim Vermittler ein Denkabschnitt mit zwei Umbrüchen als
    einzigem Inhalt — und die Oberfläche zeichnete daraus einen leeren Kasten
    „Nachgedacht", der eine Überlegung behauptet, die es nicht gab. Am Ende des
    Laufs stünde derselbe Umbruch außerdem als Rest im gespeicherten Denktext.
    """
    server = _server(db, "denknaht-still")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)

    gedacht = {"einmal": False}

    async def fake(_client, *, provider, api_key, messages, usage: StreamUsage,
                   tools=None, tool_choice=None, reasoning=False,
                   reasoning_effort=None, cache_marke=False, model=None):
        del provider, api_key, messages, tool_choice, reasoning, reasoning_effort
        del cache_marke
        # Nur die erste Runde denkt — danach schweigt das Modell und arbeitet.
        if not gedacht["einmal"]:
            gedacht["einmal"] = True
            yield StreamChunk("reasoning", "Erst der Status.")
            usage.tool_calls = [ProviderToolCall(
                id="a", name="read_server_status", arguments={"server_id": server.id},
            )]
        usage.total_tokens = 10
        yield StreamChunk("content", "Fertig.")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Mach was", reasoning=True,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    abzug, _warteschlange = ai_run_broker.abonnieren(run.id)
    del abzug
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()

    nachricht = _letzte_antwort(db, conversation)
    assert nachricht.reasoning == "Erst der Status.", (
        "Der Denktext endet mit einem Trenner, hinter dem nichts mehr kommt"
    )
    abschnitte = json.loads(nachricht.sections_json or "[]")
    denkabschnitte = [
        abschnitt for abschnitt in abschnitte if abschnitt.get("art") == "denken"
    ]
    assert [abschnitt["inhalt"] for abschnitt in denkabschnitte] == ["Erst der Status."], (
        "Ein Denkabschnitt ohne Inhalt wird zu einem leeren Kasten 'Nachgedacht'"
    )


@pytest.mark.asyncio
async def test_the_reasoning_stops_at_the_same_limit_live_and_stored(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was der Benutzer live gelesen hat, findet er nach dem Neuladen wieder.

    Die Zeichengrenze zählte im Adapter je **Anfrage**, und der Zähler wurde
    nach jeder Werkzeugrunde neu angelegt. Gespeichert wurde dann auf einmal je
    **Nachricht** gekürzt: bei sechzehn Runden bis zu sechzehnmal 32.000
    Zeichen live gegen 32.000 in der Datenbank. Der Denkblock brach nach dem
    Neuladen mitten im Satz ab, ohne dass irgendetwas darauf hinwies.
    """
    server = _server(db, "denkgrenze")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    # Zwei Runden mit je gut der Hälfte der Grenze — zusammen darüber.
    _fake_stream(
        monkeypatch,
        [[ProviderToolCall(id="a", name="read_server_status",
                           arguments={"server_id": server.id})]],
        text="Fertig.",
        denken="d" * (ai_stream_service.MAX_REASONING_CHARS // 2 + 1_000),
    )

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Mach was", reasoning=True,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    # Zusehen ab der ersten Sekunde, so wie der Browser nach dem Absenden.
    abzug, warteschlange = ai_run_broker.abonnieren(run.id)
    del abzug
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()

    live = ""
    while not warteschlange.empty():
        ereignis, daten = warteschlange.get_nowait()
        if ereignis == "reasoning":
            live += str(daten.get("content") or "")

    nachricht = _letzte_antwort(db, conversation)
    assert len(live) == ai_stream_service.MAX_REASONING_CHARS, (
        f"Live gingen {len(live)} Zeichen hinaus"
    )
    assert nachricht.reasoning == live, (
        "Der gespeicherte Denktext ist nicht der, den der Benutzer gesehen hat"
    )


@pytest.mark.asyncio
async def test_the_compaction_reports_itself_before_the_run_ends(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Faltung muss sich melden, solange noch jemand zuhört.

    Der Abschluss eines Laufs schließt den Kanal, und die Anzeige steigt an
    genau diesem Ereignis aus. Stand die Faltung dahinter, entstand ``compacted``
    in einem bereits geschlossenen Kanal: der Hinweis "Ältere Nachrichten wurden
    zusammengefasst" erreichte nie einen Browser, und der Kontextring blieb auf
    dem Stand von vor der Faltung stehen.
    """
    from services import ai_compaction_service

    server = _server(db, "faltung")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [], text="Fertig.")

    async def _faltet(*, client, user_id, conversation_id, provider_id,
                      context_chars=None) -> bool:
        del client, user_id, conversation_id, provider_id, context_chars
        # Eine echte Faltung spricht mit dem Anbieter. Der Haltepunkt, den sie
        # dabei zwangsläufig hat, gehört in die Nachstellung.
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(ai_compaction_service, "compact_conversation", _faltet)

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Mach was", reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    abo = ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    ereignisse = [
        stueck async for stueck in ai_run_broker.lauf_verfolgen(run.id, abo=abo)
    ]
    verbunden = "".join(ereignisse)
    assert "event: compacted" in verbunden, (
        "Die Faltung meldet sich erst, wenn niemand mehr zuhört"
    )
    assert verbunden.index("event: compacted") < verbunden.index('"status": "completed"'), (
        "Die Anzeige ist beim Endereignis schon ausgestiegen"
    )



# ── 7. Der Rahmen des Laufs geht nie still verloren ───────────────────────


def test_ein_kaputter_laufzustand_traegt_die_marke() -> None:
    """Unlesbar ist nicht dasselbe wie leer — und muss sich unterscheiden lassen.

    Ein frischer Lauf hat noch keinen Zustand; das ist der harmlose Fall. Ein
    vorhandener, aber kaputter Zustand ist der gefaehrliche: in ihm steht
    keine Rolle, kein Guardian- und kein Aufgabenrahmen mehr. Wer beides
    gleich beantwortet, kann den einen Fall nicht abfangen.
    """
    frisch = SimpleNamespace(id="r-frisch", state_json=None)
    assert "unlesbar" not in ai_run_service.zustand_lesen(frisch)

    kaputt = SimpleNamespace(id="r-kaputt", state_json="{kaputt")
    assert ai_run_service.zustand_lesen(kaputt)["unlesbar"] is True

    kein_woerterbuch = SimpleNamespace(id="r-liste", state_json="[1, 2, 3]")
    assert ai_run_service.zustand_lesen(kein_woerterbuch)["unlesbar"] is True


@pytest.mark.asyncio
async def test_ein_unlesbarer_laufzustand_beendet_den_lauf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Rahmen faehrt niemand weiter — auch nicht als gewoehnlicher Chatlauf.

    Der Rueckfall auf den leeren Zustand war fail-open: ein Worker- oder
    Heilungslauf verlor damit Rolle, Serverbindung und Werkzeugeinengung und
    lief mit dem vollen Katalog weiter, im Namen des Freigebers und ohne
    jemanden, der mitliest. Der Verlust des Rahmens ist die gefaehrliche
    Richtung, nicht die sichere.
    """
    server = _server(db, "rahmenlos")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    gesehen = _fake_stream(monkeypatch, [], text="Das darf nie gesagt werden.")

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Sieh nach den Servern", reasoning=False,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    run.state_json = "{kaputt"
    db.commit()

    ai_run_broker.eroeffnen(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()
    run = db.get(AiRun, run.id)

    assert run.status == "failed"
    assert run.stop_reason == "laufrahmen_unlesbar"
    assert gesehen == [], "Der Anbieter wurde trotz verlorenem Rahmen gefragt"


# ── 8. Wer zusieht, geht die Schleife nichts an ───────────────────────────


def test_das_fenster_wohnt_beim_vermittler() -> None:
    """Der Modulvertrag als Zusage, nicht als Absichtserklaerung.

    ``ai_stream_service`` sagt in seinem Kopf: "``ai_run_broker`` — wer
    zusehen darf". Solange ``lauf_verfolgen`` und ``sse_event`` in der
    Schleife standen, war dieser Satz an genau der Stelle unwahr. Auch eine
    bequeme Wiederausfuhr zaehlt nicht: sie waere ein zweiter Name fuer
    dieselbe Sache, und der naechste Umzug faende sie nicht.
    """
    for name in ("sse_event", "lauf_verfolgen", "lauf_status"):
        assert hasattr(ai_run_broker, name), f"{name} fehlt beim Vermittler"
        assert not hasattr(ai_stream_service, name), (
            f"{name} gehoert in den Vermittler, nicht in die Schleife"
        )
    assert not hasattr(ai_stream_service, "_lauf_status")
