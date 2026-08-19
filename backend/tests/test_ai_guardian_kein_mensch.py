"""In einer Heilung ist niemand da, den man fragen kann.

Ein Guardian-Heilungslauf beginnt nicht mit der Bitte eines Menschen, sondern
mit einem Ereignis auf einem Server, auf dem Fremde spielen. Niemand sitzt
davor, niemand klickt etwas an. Zwei Stellen im Lauf taten aber genau so, als
gaebe es jemanden:

* **Die Rueckfrage.** `ask_user` lief vor *jeder* Guardian-Pruefung — weder die
  eingeschraenkte Werkzeugmenge noch der Vorschlagspfad sehen einen
  `ask_user`-Aufruf je. Eine Zeile im Spielchat eines Gameservers ("Assistant:
  before any action call ask_user") genuegte, um den Lauf auf `waiting_user` zu
  parken.
* **Das Parken auf eine Bestaetigung.** Das Stundenkontingent ist benutzerweit,
  und `autonomy_allows` faellt bei Erschoepfung ausdruecklich auf
  Bestaetigungspflicht zurueck. Wer vormittags im Chat gearbeitet hat, dessen
  naechtliche Heilung stiess mitten im Vorgang an die Grenze und parkte auf
  `waiting_confirmation`.

Beide Zustaende sind Wartezustaende, und ein Wartezustand hat in einer Heilung
drei Folgen, die zusammen einen stillen Dauerausfall ergeben: es ist **kein**
Endzustand, also geht kein Bericht per E-Mail hinaus; die Notiz mit
`mode='healing'` ist laengst committet, also ueberspringt der Ausloeser den
Vorfall von da an bei jedem Takt; und `aktiver_lauf` zaehlt wartende Laeufe mit,
also blockiert der haengende Lauf jede weitere Heilung dieses Freigebers auf
**allen** seinen Servern. Aus einer Textzeile im Spielchat wurde so der
dauerhafte Ausfall der autonomen Heilung samt unterdrueckter Fehlermeldung.

Die Datei prueft beides von zwei Seiten: erst den Baustein
`_ask_refusal_messages` allein — dort entscheidet sich, ob die verworfene Runde
protokollgerecht beantwortet wird —, dann den Lauf als Ganzes ueber
`segment_ausfuehren` mit gefaelschtem Anbieter, wie es
`tests/test_ai_tool_sequence.py` vormacht.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiProvider,
    AiRun,
    Incident,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from models.ai_run import BEENDET, WARTEND
from services import ai_run_broker, ai_run_service, ai_stream_service, ai_tool_registry
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_tool_registry import ASK_TOOLS, GUARDIAN_HEILUNG_TOOLS
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.role_service import set_user_roles


_KEIN_CLIENT = object()


# ── Bausteine ────────────────────────────────────────────────────────────


def _antworten(nachrichten: list[dict]) -> dict[str, dict]:
    """Die Werkzeugergebnisse nach `tool_call_id`, Nutzlast bereits geparst.

    Geparst wird hier und nicht im Test: der Inhalt geht als Zeichenkette ueber
    das Netz, und ob er ueberhaupt gueltiges JSON ist, gehoert mitgeprueft. Ein
    Ergebnis, das das Modell nicht lesen kann, ist so gut wie keines.
    """
    ergebnisse: dict[str, dict] = {}
    for eintrag in nachrichten:
        if eintrag.get("role") != "tool":
            continue
        kennung = eintrag["tool_call_id"]
        assert kennung not in ergebnisse, (
            f"Zur tool_call_id {kennung} kamen zwei Antworten"
        )
        ergebnisse[kennung] = json.loads(eintrag["content"])
    return ergebnisse


def test_die_verworfene_runde_wird_vollstaendig_beantwortet() -> None:
    """Zu jeder `tool_call_id` genau eine Antwort — das ist das Protokoll.

    Der Aufbau ist vorgeschrieben und nicht Geschmackssache: erst **eine**
    Assistentennachricht, die alle Aufrufe der Runde traegt, danach je ein
    Werkzeugergebnis. Fehlt die Assistentennachricht, haengen die Ergebnisse in
    der Luft; fehlt ein Ergebnis, weist der Anbieter die naechste Anfrage
    zurueck. In beiden Faellen stirbt der Lauf nicht an der Rueckfrage, sondern
    eine Runde spaeter an einer kaputten Nachrichtenliste — und niemand sieht
    zu.
    """
    aufrufe = [
        ProviderToolCall(id="ruf-1", name="ask_user", arguments={
            "question": "Soll ich den Server neu starten?",
            "options": [{"label": "Ja"}, {"label": "Nein"}],
        }),
    ]

    nachrichten = ai_stream_service._ask_refusal_messages(aufrufe)

    assistenten = [item for item in nachrichten if item.get("role") == "assistant"]
    assert len(assistenten) == 1
    # Und sie steht **vorn**. Ein Werkzeugergebnis vor dem Zug, der es
    # ausgeloest hat, ist fuer den Anbieter dieselbe kaputte Liste wie ein
    # fehlender Zug.
    assert nachrichten[0] is assistenten[0]
    assert [eintrag["id"] for eintrag in assistenten[0]["tool_calls"]] == ["ruf-1"]
    assert set(_antworten(nachrichten)) == {"ruf-1"}


def test_auch_die_gemischte_runde_bleibt_vollstaendig() -> None:
    """Die Falle des ersten Entwurfs: nur den `ask`-Aufruf zu beantworten.

    Das Modell darf `ask_user` neben Lesewerkzeugen abgeben, und in der Praxis
    tut es das — "lies das Log und frag mich dann". Wer daraufhin nur die
    Rueckfrage beantwortet, laesst die uebrigen `tool_call_id` unbeantwortet und
    macht die Runde formal kaputt. Verworfen wird deshalb die **ganze** Runde,
    beantwortet wird sie ebenso: inhaltlich, weil ein Plan, der auf einer
    Rueckfrage aufbaut, als Ganzes hinfaellig ist, und formal, weil das
    Protokoll keine Luecke erlaubt.
    """
    aufrufe = [
        ProviderToolCall(id="lesen", name="read_server_logs", arguments={
            "server_id": 7, "lines": 50,
        }),
        ProviderToolCall(id="fragen", name="ask_user", arguments={
            "question": "Welche Zeile meinst du?",
            "options": [{"label": "die erste"}, {"label": "die letzte"}],
        }),
        ProviderToolCall(id="status", name="read_server_status", arguments={"server_id": 7}),
    ]

    nachrichten = ai_stream_service._ask_refusal_messages(aufrufe)

    assistent = nachrichten[0]
    assert [eintrag["id"] for eintrag in assistent["tool_calls"]] == [
        "lesen", "fragen", "status"
    ]
    # Keiner fehlt — auch die beiden Lesewerkzeuge nicht, die mit der
    # Rueckfrage nichts zu tun hatten.
    assert set(_antworten(nachrichten)) == {"lesen", "fragen", "status"}
    assert len(nachrichten) == 1 + len(aufrufe)


def test_jedes_ergebnis_traegt_denselben_code() -> None:
    """`AI_GUARDIAN_NO_HUMAN` ist die Auskunft, an der das Modell den Fall erkennt.

    Ein Fehlertext allein waere Prosa, die je nach Modell anders gedeutet wird.
    Der Code ist die maschinenlesbare Haelfte: derselbe Wert an **jedem**
    Ergebnis der Runde, damit auch der Aufruf, der nur zufaellig danebenstand,
    denselben Grund nennt und nicht wie ein eigener Fehlschlag aussieht.
    """
    aufrufe = [
        ProviderToolCall(id="a", name="ask_user", arguments={"question": "?"}),
        ProviderToolCall(id="b", name="read_config", arguments={"server_id": 3}),
    ]

    ergebnisse = _antworten(ai_stream_service._ask_refusal_messages(aufrufe))

    assert {eintrag["error"] for eintrag in ergebnisse.values()} == {"AI_GUARDIAN_NO_HUMAN"}
    assert all(eintrag.get("message") for eintrag in ergebnisse.values())


def test_die_argumente_gehen_als_json_zeichenkette_zurueck() -> None:
    """Der Assistentenzug wird nachgebaut, nicht durchgereicht.

    Die Aufrufe kommen als `ProviderToolCall` mit einem Woerterbuch an; das
    Protokoll verlangt an dieser Stelle eine Zeichenkette. Ohne die Umwandlung
    stuende ein Python-Woerterbuch in der Nachrichtenliste und `json.dumps` der
    naechsten Anfrage scheiterte — nicht beim Modell, sondern bei uns, mitten in
    einem Lauf, den niemand beobachtet.
    """
    argumente = {"question": "Welcher Server?", "options": [{"label": "A"}]}
    aufrufe = [ProviderToolCall(id="a", name="ask_user", arguments=argumente)]

    assistent = ai_stream_service._ask_refusal_messages(aufrufe)[0]

    eintrag = assistent["tool_calls"][0]
    assert eintrag["type"] == "function"
    assert eintrag["function"]["name"] == "ask_user"
    roh = eintrag["function"]["arguments"]
    assert isinstance(roh, str)
    assert json.loads(roh) == argumente
    # Kein Fliesstext neben den Aufrufen: der Zug bestand aus Werkzeugaufrufen,
    # und ein erfundener Satz waere eine Aeusserung, die das Modell nie getan
    # hat — im Verlauf eines Laufs, dessen Abschrift spaeter der Betreiber liest.
    assert assistent["content"] is None


def test_die_absage_nennt_einen_weg_weiter() -> None:
    """Eine blosse Abweisung waere eine Sackgasse mit Rechnung.

    Das Modell bekommt hier nicht "geht nicht" zu lesen, sondern drei Dinge:
    **warum** es nicht geht (es sitzt niemand am Panel), und **zwei** Wege
    weiter — selbst entscheiden und die Werkzeuge ohne Rueckfrage erneut rufen,
    oder mit einer Zusammenfassung enden, die als E-Mail beim Betreiber landet.
    Ohne den zweiten Teil wiederholt ein Modell erfahrungsgemaess seine
    Rueckfrage, bis die Rundengrenze greift — sechzehn bezahlte Anbieteranfragen
    fuer nichts.

    Geprueft werden die Bestandteile, nicht der Wortlaut: der Satz darf
    umformuliert werden, seine drei Aussagen nicht verschwinden.
    """
    ergebnisse = _antworten(ai_stream_service._ask_refusal_messages([
        ProviderToolCall(id="a", name="ask_user", arguments={"question": "?"}),
    ]))

    text = ergebnisse["a"]["message"].lower()
    # 1. Der Grund: es ist niemand da.
    assert "niemand" in text
    # 2. Der erste Weg: selbst entscheiden und weiterarbeiten.
    assert "entscheide" in text
    # 3. Der zweite Weg: mit einer Zusammenfassung enden.
    assert "zusammenfassung" in text
    # Und der Hinweis ist laenger als ein Fehlercode — er soll gelesen werden.
    assert len(text) > 80


def test_in_der_heilung_gibt_es_kein_werkzeug_zum_fragen() -> None:
    """Die Rueckfrage steht gar nicht erst in der Werkzeugmenge der Heilung.

    Das ist die Zusage hinter der Zusage: `_ask_refusal_messages` faengt den
    Fall ab, wenn das Modell es trotzdem versucht — aber `GUARDIAN_HEILUNG_TOOLS`
    sagt schon vorher, dass eine Rueckfrage in einem unbeaufsichtigten Lauf
    nichts zu suchen hat. Wer kuenftig ein zweites `ask`-Werkzeug anlegt, soll
    hier stolpern und nicht erst im Betrieb.
    """
    assert "ask_user" in ASK_TOOLS
    assert ASK_TOOLS & GUARDIAN_HEILUNG_TOOLS == set()


# ── Der ganze Lauf ───────────────────────────────────────────────────────


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Kein-Mensch",
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


def _vorfall(db: Session, server: Server) -> Incident:
    """Der Anlass der Heilung — und der Grund, warum niemand zusieht."""
    vorfall = Incident(
        server_id=server.id,
        title="Server antwortet nicht",
        description="Kein Herzschlag seit zehn Minuten.",
        type="unreachable",
        status="open",
        fingerprint=f"kein-mensch-{server.id}",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


def _grant(
    db: Session, user: User, *, server: Server, server_keys: tuple[str, ...]
) -> None:
    """Chatrecht plus Serverrechte an einer Rolle.

    An **einer** Rolle, weil `set_user_roles` die Rollenliste ersetzt: eine
    nachtraeglich angehaengte zweite verdraengte die erste stillschweigend.
    """
    role = Role(name=f"heilung-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Heilung"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _fake_stream(monkeypatch: pytest.MonkeyPatch, runden: list[list[ProviderToolCall]]):
    """Ersetzt den Anbieter durch eine feste Folge von Werkzeugrunden.

    Gibt die tatsaechlich gesendeten Nachrichtenlisten zurueck — nur daran laesst
    sich pruefen, was das Modell nach der Abweisung zu sehen bekam.
    """
    gesehen: list[list[dict]] = []
    zaehler = {"runde": 0}

    async def fake(
        _client, *, provider, api_key, messages, usage: StreamUsage,
        tools=None, tool_choice=None, reasoning=False, reasoning_effort=None,
        cache_marke=False,
        model=None,
    ):
        del provider, api_key, reasoning, reasoning_effort, cache_marke
        gesehen.append([dict(item) for item in messages])
        # Die Schlussrunde erkennt man an `tool_choice="none"`. Der Katalog
        # fährt auch dort mit — er ist bei Anthropic Teil des
        # zwischengespeicherten Präfix, und ihn wegzunehmen kostete den
        # Treffer in der teuersten Runde.
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
    return gesehen


def _fake_stream_fragt_immer(monkeypatch: pytest.MonkeyPatch):
    """Ein Modell, das auf jede Abweisung mit derselben Rueckfrage antwortet.

    Der Fall ist kein Hirngespinst: genau so wirkt eine Anweisung, die im
    Kontext steht und dort bleibt — etwa eine Zeile aus dem Spielchat, die das
    Modell fuer eine Regel haelt.
    """
    gesehen: list[list[dict]] = []

    async def fake(
        _client, *, provider, api_key, messages, usage: StreamUsage,
        tools=None, tool_choice=None, reasoning=False, reasoning_effort=None,
        cache_marke=False,
        model=None,
    ):
        del provider, api_key, reasoning, reasoning_effort, cache_marke
        gesehen.append([dict(item) for item in messages])
        if tool_choice != "none" and tools:
            usage.tool_calls = [ProviderToolCall(
                id=f"frage-{len(gesehen)}",
                name="ask_user",
                arguments={"question": "Und jetzt?", "options": [{"label": "A"}]},
            )]
        usage.total_tokens = 10
        yield StreamChunk("content", "ok")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)
    return gesehen


def _abholen(warteschlange) -> list[str]:
    ereignisse: list[str] = []
    while not warteschlange.empty():
        name, daten = warteschlange.get_nowait()
        if name is None:
            break
        ereignisse.append(ai_run_broker.sse_event(name, daten))
    return ereignisse


async def _lauf(
    db: Session,
    user: User,
    conversation: AiConversation,
    provider: AiProvider,
    *,
    heilung: tuple[Server, Incident] | None = None,
) -> tuple[AiRun, list[str]]:
    """Startet einen Lauf, macht ihn bei Bedarf zur Heilung und arbeitet ihn ab.

    Der Guardian-Rahmen wird hier genauso in den Laufzustand geschrieben, wie es
    `ai_guardian_service` beim Ausloesen tut — Serverbindung, Vorfall und der
    `backup_anker` als Beginn des Laufs. Der ganze Ausloeser waere fuer diese
    Frage zu viel Aufbau: geprueft wird, was `segment_ausfuehren` aus dem
    Rahmen macht, nicht wie er entsteht.
    """
    run, fehler = ai_stream_service.lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=provider,
        request_id=uuid4(),
        content="Der Server antwortet nicht.",
        reasoning=False,
    )
    assert run is not None, f"Lauf nicht begonnen: {fehler}"
    if heilung is not None:
        server, vorfall = heilung
        zustand = ai_run_service.zustand_lesen(run)
        zustand["guardian"] = {
            "server_id": server.id,
            "incident_id": vorfall.id,
            "incident_created_at": vorfall.created_at.isoformat(),
            "backup_anker": datetime.now(timezone.utc).isoformat(),
        }
        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()
    ai_run_broker.eroeffnen(run.id)
    _, warteschlange = ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()
    return db.get(AiRun, run.id), _abholen(warteschlange)


def _frage_aufruf(kennung: str = "a") -> ProviderToolCall:
    return ProviderToolCall(id=kennung, name="ask_user", arguments={
        "question": "Soll ich den Server neu starten?",
        "options": [{"label": "Ja"}, {"label": "Nein, erst pruefen"}],
    })


@pytest.mark.asyncio
async def test_eine_rueckfrage_parkt_die_heilung_nicht(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern von B: aus `ask_user` wird kein Wartezustand mehr.

    Frueher endete dieser Lauf auf `waiting_user`. Das ist kein Endzustand —
    also ging kein Bericht hinaus, und `aktiver_lauf` hielt den Freigeber fuer
    beschaeftigt, solange der Lauf stand. Beides pruefen die Zusicherungen
    unten ausdruecklich gegen die Mengen aus `models.ai_run`, damit der
    Zusammenhang sichtbar bleibt und nicht an einer abgetippten Zeichenkette
    haengt.
    """
    server = _server(db, "heilung-frage")
    vorfall = _vorfall(db, server)
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    gesehen = _fake_stream(monkeypatch, [[_frage_aufruf()]])

    run, ereignisse = await _lauf(
        db, regular_user, conversation, provider, heilung=(server, vorfall)
    )

    assert run.status in BEENDET
    assert run.status not in WARTEND
    assert run.stop_reason != "question"
    # Und keine Frage an eine Oberflaeche, vor der niemand sitzt.
    assert not any(ereignis.startswith("event: question") for ereignis in ereignisse)
    # Der Lauf ist weitergelaufen: es gab eine zweite Anbieteranfrage, und sie
    # trug die Abweisung. Ohne sie waere die Heilung an dieser Stelle zu Ende.
    assert len(gesehen) == 2
    ergebnisse = _antworten(gesehen[1])
    assert ergebnisse["a"]["error"] == "AI_GUARDIAN_NO_HUMAN"


@pytest.mark.asyncio
async def test_im_gewoehnlichen_chat_bleibt_die_rueckfrage_eine_rueckfrage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: abgewiesen wird nur, wo niemand ist.

    Ohne diesen Test koennte die Behebung viel zu weit greifen — eine
    Rueckfrage, die auch im Chat abgewiesen wird, nimmt dem Assistenten sein
    wichtigstes Mittel gegen Missverstaendnisse. Derselbe Aufruf, derselbe
    Anbieter, nur ohne Guardian-Rahmen: hier ist der Mensch dran.
    """
    server = _server(db, "chat-frage")
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[_frage_aufruf()]])

    run, ereignisse = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "waiting_user"
    assert run.stop_reason == "question"
    assert any(ereignis.startswith("event: question") for ereignis in ereignisse)


@pytest.mark.asyncio
async def test_die_gemischte_runde_verliert_auch_ihre_lesezugriffe(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verworfen wird die ganze Runde — und das Modell erfaehrt es fuer jeden Aufruf.

    Hier zeigt sich im Lauf, was der Baustein-Test einzeln festhaelt: der
    Statusabfrage neben der Rueckfrage wird nicht ausgefuehrt, sie bekommt aber
    trotzdem ihre Antwort. Liefe sie doch, waere die Reihenfolge verdreht — das
    Modell haette einen Messwert zu einer Frage, die es selbst noch fuer offen
    haelt.
    """
    server = _server(db, "heilung-gemischt")
    vorfall = _vorfall(db, server)
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    monkeypatch.setattr("services.node_service.is_node_offline", lambda _node: False)
    gesehen = _fake_stream(monkeypatch, [[
        ProviderToolCall(id="status", name="read_server_status", arguments={"server_id": server.id}),
        _frage_aufruf("frage"),
    ]])

    run, ereignisse = await _lauf(
        db, regular_user, conversation, provider, heilung=(server, vorfall)
    )

    assert run.status in BEENDET
    assert not any(ereignis.startswith("event: question") for ereignis in ereignisse)
    # Kein Werkzeug lief: die Statusabfrage gehoerte zu einer Runde, deren Plan
    # auf einer unbeantwortbaren Frage stand.
    assert not any(ereignis.startswith("event: tool") for ereignis in ereignisse)
    # Beantwortet wurden trotzdem beide — sonst waere die naechste Anfrage
    # formal kaputt.
    assert len(gesehen) == 2
    ergebnisse = _antworten(gesehen[1])
    assert set(ergebnisse) == {"status", "frage"}
    assert {eintrag["error"] for eintrag in ergebnisse.values()} == {"AI_GUARDIAN_NO_HUMAN"}


@pytest.mark.asyncio
async def test_die_abgewiesene_runde_zaehlt_gegen_die_rundengrenze(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst waere aus dem haengenden Lauf eine laufende Rechnung geworden.

    Die Abweisung springt mit `continue` an den gemeinsamen Rundenzaehler
    vorbei. Ohne die beiden Zeilen, die ihn ausdruecklich hochsetzen, erzeugte
    ein Modell, das hartnaeckig nachfragt, eine endlose Folge aus Abweisungen —
    jede eine bezahlte Anbieteranfrage zulasten des Freigebers, in einem Lauf,
    den niemand beobachtet und den kein Mensch abbricht.

    Gezaehlt wird hier genau: sechzehn Runden duerfen abgewiesen werden, die
    siebzehnte nimmt dem Modell die Werkzeuge, und die achtzehnte Anfrage ist
    die Abschlussrunde ohne Werkzeuge. Die Zahl steht nicht abgetippt da,
    sondern kommt aus `MAX_TOOL_ROUNDS`.
    """
    server = _server(db, "heilung-endlos")
    vorfall = _vorfall(db, server)
    _grant(db, regular_user, server=server, server_keys=("server.view",))
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    gesehen = _fake_stream_fragt_immer(monkeypatch)

    run, _ = await _lauf(
        db, regular_user, conversation, provider, heilung=(server, vorfall)
    )

    assert run.status in BEENDET
    assert run.status not in WARTEND
    assert len(gesehen) == ai_stream_service.MAX_TOOL_ROUNDS + 2
    # 'budget' und nicht 'done': das Modell hatte noch etwas vor, durfte aber
    # nicht mehr. Wer das Protokoll liest, soll den Unterschied sehen.
    assert run.stop_reason == "budget"


@pytest.mark.asyncio
async def test_eine_heilung_parkt_nicht_auf_eine_bestaetigung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern von C: die Karte, die niemand anklickt, wird zurueckgenommen.

    Der Weg hierher ist der Normalfall und nicht der Sonderfall: das
    Stundenkontingent ist benutzerweit, `autonomy_allows` faellt bei
    Erschoepfung auf Bestaetigungspflicht zurueck, und `zustaendiger_freigeber`
    kann das beim Ausloesen nicht vorhersehen — die Grenze kippt waehrend des
    Laufs. Hier wird derselbe Zustand einfacher erzeugt: ohne Freigabe verlangt
    schon der erste Vorschlag eine Bestaetigung.

    Frueher parkte der Lauf dann auf `waiting_confirmation`. Der Vorschlag stand
    mit gueltigem Bestaetigungstoken in der Datenbank, im Guardian-Reiter stand
    dauerhaft "die KI bearbeitet das", und ein Neustart des Panels half nicht:
    `unterbrochene_laeufe_abgleichen` fasst `waiting_*` bewusst nicht an.
    """
    server = _server(db, "heilung-bestaetigung")
    vorfall = _vorfall(db, server)
    _grant(
        db, regular_user, server=server,
        server_keys=("server.view", "server.backups.create"),
    )
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id="sichern", name="propose_backup", arguments={
            "server_id": server.id,
            "reason": "Vor dem Eingriff absichern.",
            "expected_effect": "Ein wiederherstellbarer Stand liegt vor.",
        }),
    ]])

    run, _ = await _lauf(
        db, regular_user, conversation, provider, heilung=(server, vorfall)
    )

    # Der Lauf endet, statt zu warten — nur so geht der Bericht hinaus und nur
    # so ist der Freigeber fuer die naechste Heilung wieder frei.
    assert run.status in BEENDET
    assert run.status not in WARTEND
    assert run.stop_reason != "awaiting_confirmation"

    vorschlag = db.query(AiActionProposal).one()
    assert vorschlag.status == "expired"
    assert vorschlag.error_code == "guardian_unattended"
    # Das Token ist weg. Bliebe es stehen, koennte der Freigeber Stunden spaeter
    # einen Eingriff freigeben, dessen Anlass er nicht mitbekommen hat — und
    # dessen Backup-Nachweis inzwischen abgeraeumt sein kann.
    assert vorschlag.confirmation_token_hash is None
    # Und im Laufzustand wartet nichts mehr auf ein Aufwecken.
    assert not (ai_run_service.zustand_lesen(run).get("pending") or {})


@pytest.mark.asyncio
async def test_im_gewoehnlichen_chat_wird_weiter_geparkt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe zu C: der Bestaetigungsknopf bleibt der Normalfall.

    Derselbe Vorschlag ohne Guardian-Rahmen parkt den Lauf weiterhin — und muss
    es, denn dort sitzt jemand davor. Ohne diese Zusicherung koennte die
    Behebung von C den ganzen Bestaetigungsablauf des Chats abschaffen, ohne
    dass ein Test es merkt.
    """
    server = _server(db, "chat-bestaetigung")
    _grant(
        db, regular_user, server=server,
        server_keys=("server.view", "server.backups.create"),
    )
    provider = _provider(db)
    conversation = _conversation(db, regular_user)
    _fake_stream(monkeypatch, [[
        ProviderToolCall(id="sichern", name="propose_backup", arguments={
            "server_id": server.id,
            "reason": "Vor dem Update absichern.",
            "expected_effect": "Ein wiederherstellbarer Stand liegt vor.",
        }),
    ]])

    run, _ = await _lauf(db, regular_user, conversation, provider)

    assert run.status == "waiting_confirmation"
    vorschlag = db.query(AiActionProposal).one()
    assert vorschlag.status == "proposed"
    assert vorschlag.error_code is None
