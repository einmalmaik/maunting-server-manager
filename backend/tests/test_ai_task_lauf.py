"""Der Lauf, den die Uhr ausloest — und alles, was daran anders ist.

Die uebrigen Aufgabendateien pruefen Bausteine: rechnet `naechste_faelligkeit`
richtig, weist `anlegen` das Fehlende ab. Hier geht es um den einen Fall, den
kein Baustein allein zeigt: **es sitzt niemand davor.**

Drei Dinge muessen dann gelten, und jedes davon war beim Guardian einmal ein
Befund, bevor es eine Zusage war:

* Der Rahmen ueberlebt jede Runde. Er wird nicht in einer Variablen gehalten,
  sondern bei jeder Runde neu aus dem Zustand gelesen — sonst gaelte er in der
  ersten Runde und in der dritten nicht mehr.
* Die Werkzeugmenge ist enger als im Chat, und sie wird im Code durchgesetzt,
  nicht im Prompt. Die Eingabe eines Aufgabenlaufs enthaelt Serverlogs, also
  Text, den ein Spieler geschrieben haben kann.
* `ask_user` gibt es nicht. Ein Lauf, der darauf wartet, steht auf
  'waiting_user' — und weil `aktiver_lauf` wartende Laeufe mitzaehlt, blockiert
  er von da an **jede** weitere Aufgabe dieses Benutzers.

Gefaelscht wird genau das, was in einem Test nicht echt sein kann: der Anbieter
und die Laufzeit der Anwendung. Alles dazwischen ist Produktivcode.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import (
    AiAutonomyGrant,
    AiConversation,
    AiProvider,
    AiRun,
    AiTask,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_run_broker, ai_run_service, ai_stream_service, ai_task_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_tool_registry import (
    AUFGABEN_HANDELN,
    AUFGABEN_LESEN,
    MEMORY_TOOLS,
    SKILL_TOOLS,
    aufgaben_tools,
)
from services.auth_service import AuthService
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.permission_catalog import SERVER_KEYS
from services.role_service import set_user_roles


#: Der gefaelschte Anbieter fasst ihn nie an — er muss nur nicht ``None`` sein,
#: weil `aufgabenlauf_starten` daran erkennt, ob ueberhaupt eine Anwendung laeuft.
_KEIN_CLIENT = object()

KI_RECHTE = ("ai.chat.use", "ai.tasks.manage", "ai.autonomous.use")


# ── Aufbau ────────────────────────────────────────────────────────────────


def _benutzer(db: Session, name: str, *, rechte=KI_RECHTE) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.email_notifications = True
    db.commit()
    rolle = Role(name=f"aufgabe-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    # Ohne aufgehobene Limits scheitert der Lauf am Kontingent statt an dem,
    # was hier geprueft werden soll.
    set_role_limit(db, rolle.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _server(db: Session, name: str) -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{name}",
        container_name=f"msm-{name}",
        status="running",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _sichtbar(db: Session, user: User, server: Server) -> None:
    for key in sorted(SERVER_KEYS):
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _freigabe(
    db: Session, user: User, *, budget: int = 10, server: Server | None = None
) -> None:
    db.add(AiAutonomyGrant(
        user_id=user.id,
        server_id=None if server is None else server.id,
        enabled=True,
        max_actions_per_hour=budget,
    ))
    db.commit()


def _anbieter(db: Session, name: str = "Aufgaben-Anbieter") -> AiProvider:
    provider = AiProvider(
        name=name,
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _aufgabe(db: Session, user: User, **felder) -> AiTask:
    vorgabe = {
        "title": "Serverbericht",
        "instruction": "Sieh nach den Servern und fasse zusammen.",
        "kind": "report",
        "plan_kind": "daily",
        "time_of_day": "08:00",
        "timezone": "Europe/Berlin",
        "channel": "email",
    }
    vorgabe.update(felder)
    aufgabe = ai_task_service.anlegen(db, user=user, felder=vorgabe)
    db.commit()
    db.refresh(aufgabe)
    return aufgabe


# ── Die gefaelschten Aussenkanten ─────────────────────────────────────────


class Anbieter:
    """Ein skriptbarer Modellanbieter — hier bewusst als Gegner gefahren.

    `runden` ist eine Liste von Werkzeugaufrufslisten; jede Anfrage bekommt die
    naechste. Ist die Liste erschoepft, antwortet er mit Text — genau wie ein
    echtes Modell, das fertig ist.
    """

    def __init__(self, runden, *, text: str = "Alle Server laufen."):
        self.runden = list(runden)
        self.text = text
        self.gesehen: list[list[dict]] = []
        self.werkzeugsaetze: list[set[str]] = []

    def einbauen(self, monkeypatch) -> "Anbieter":
        async def fake(_client, *, provider, api_key, messages, usage: StreamUsage,
                       tools=None, reasoning=False, reasoning_effort=None,
                       cache_marke=False):
            del provider, api_key, reasoning, reasoning_effort, cache_marke
            self.gesehen.append([dict(item) for item in messages])
            if tools is not None:
                self.werkzeugsaetze.append({
                    str(eintrag.get("function", {}).get("name")) for eintrag in tools
                })
                if self.runden:
                    usage.tool_calls = list(self.runden.pop(0))
            usage.total_tokens = 10
            yield StreamChunk("content", self.text)

        monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)
        return self

    def werkzeugantworten(self) -> list[dict]:
        import json

        antworten = []
        for runde in self.gesehen:
            for nachricht in runde:
                if nachricht.get("role") != "tool":
                    continue
                try:
                    antworten.append(json.loads(nachricht.get("content") or "{}"))
                except ValueError:
                    antworten.append({"roh": nachricht.get("content")})
        return antworten


def _laufzeit_faelschen(monkeypatch):
    """`aufgabenlauf_starten` braucht Client, Denkstufe und Kontextfenster.

    Alle drei kommen im Betrieb aus der laufenden Anwendung. Hier werden sie
    gesetzt, ohne eine Anwendung hochzufahren — der Rest des Starts bleibt echt,
    inklusive Unterhaltung, Anbieterwahl und Kontingent.
    """
    from services import ai_context_window, ai_reasoning

    monkeypatch.setattr(ai_run_service, "http_client", lambda: _KEIN_CLIENT)

    async def _vorgabe(*args, **kwargs):
        return False, None

    class _Fenster:
        bekannt = False
        zeichen = None

    async def _ermitteln(*args, **kwargs):
        return _Fenster()

    monkeypatch.setattr(ai_reasoning, "vorgabe", _vorgabe)
    monkeypatch.setattr(ai_context_window, "ermitteln", _ermitteln)
    # Der Auftrag wuerde sonst auf die Ereignisschleife der Anwendung gelegt.
    # Hier fahren wir das Segment selbst — das ist der Punkt, an dem ein Test
    # ueberhaupt zusehen kann.
    monkeypatch.setattr(ai_run_service, "lauf_starten", lambda run_id: True)


async def _lauf_fahren(db: Session, run: AiRun) -> AiRun | None:
    ai_run_broker.eroeffnen(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()
    return db.get(AiRun, run.id)


# ── Die Werkzeugmenge ─────────────────────────────────────────────────────


def test_die_lesemenge_enthaelt_die_werkzeuge_des_betreibers() -> None:
    """Die Beispiele, die der Betreiber ausdruecklich genannt hat.

    "sag mir wie das Wetter ist" braucht `web_search`, "wie geht es meinen
    Servern" die Serverlesewerkzeuge, "welche Aufgaben hast du" `list_tasks`.
    Faellt eines davon aus der Menge, laeuft die Aufgabe zwar — sie kann nur
    genau das nicht mehr, wofuer sie angelegt wurde.
    """
    lesen = aufgaben_tools("report")
    for name in ("web_search", "list_my_servers", "read_server_status", "list_tasks"):
        assert name in lesen, name


def test_eine_berichtende_aufgabe_bekommt_kein_einziges_schreibwerkzeug() -> None:
    assert aufgaben_tools("report") == AUFGABEN_LESEN
    assert not (aufgaben_tools("report") & AUFGABEN_HANDELN)


def test_ask_user_steht_in_keiner_der_beiden_mengen() -> None:
    """Die Rueckfrage ist der Weg, auf dem ein Lauf haengenbleibt.

    Sie parkt ihn auf 'waiting_user' und wartet auf einen Klick. In einer
    faelligen Aufgabe kommt der nie — und ein wartender Lauf zaehlt fuer
    `aktiver_lauf` als aktiv, blockiert also jede weitere Aufgabe desselben
    Benutzers, bis jemand von Hand aufraeumt.
    """
    for art in ("report", "act"):
        assert "ask_user" not in aufgaben_tools(art)


def test_eine_aufgabe_legt_keine_aufgaben_an() -> None:
    """Sonst waere der erste Zyklus nur eine Frage der Zeit."""
    for art in ("report", "act"):
        menge = aufgaben_tools(art)
        assert "propose_task_set" not in menge
        assert "propose_task_delete" not in menge
        assert "send_test_email" not in menge


def test_gedaechtnis_und_skills_bleiben_dem_beaufsichtigten_chat_vorbehalten() -> None:
    """Aus einem Lauf ohne Zuschauer wird nichts Dauerhaftes gelernt.

    Die Werkzeugergebnisse enthalten Serverlogs — Text, den ein Spieler
    geschrieben haben kann. Ein `remember` daraus stuende danach in jedem
    weiteren Lauf im Kontext, und niemand hat es je gesehen.

    Gegen die beiden Gruppen aus der Registry gefragt und nicht gegen eine
    Namensliste: hier standen `forget`, `read_memories` und `propose_skill_save`
    — drei Namen, die es in `WERKZEUGE` nie gab. Sie konnten nicht
    fehlschlagen, und die Zusage trug damit nur zu einem Viertel. Über die
    Gruppen kann sie nicht wieder veralten, wenn jemand ein Werkzeug umbenennt.
    """
    for art in ("report", "act"):
        assert aufgaben_tools(art) & (MEMORY_TOOLS | SKILL_TOOLS) == set(), art


# ── Der Rahmen ────────────────────────────────────────────────────────────


def test_der_rahmen_kommt_aus_dem_zustand_und_nicht_aus_einer_variablen() -> None:
    zustand = {"aufgabe": {
        "task_id": "abc", "kind": "act", "channel": "both", "title": "Nachtbackup",
    }}
    rahmen = ai_stream_service.aufgabe_aus_zustand(zustand)
    assert rahmen is not None
    assert (rahmen.task_id, rahmen.kind, rahmen.channel) == ("abc", "act", "both")


def test_ohne_rahmen_ist_es_ein_gewoehnlicher_chatlauf() -> None:
    assert ai_stream_service.aufgabe_aus_zustand({}) is None


@pytest.mark.parametrize("roh", ["kaputt", [], {"kind": "act"}])
def test_ein_unlesbarer_rahmen_wirft_statt_zu_verschwinden(roh) -> None:
    """Der Verlust des Rahmens ist die gefaehrliche Richtung, nicht die sichere.

    Ohne ihn faellt die Werkzeugeinengung weg, `ask_user` wird wieder moeglich,
    und offene Vorschlaege werden geparkt statt zurueckgenommen — in einem Lauf,
    in dem niemand mitliest, im Namen des Benutzers und mit dessen Rechten.
    """
    with pytest.raises(ai_stream_service.GuardianRahmenUnlesbar):
        ai_stream_service.aufgabe_aus_zustand({"aufgabe": roh})


# ── Der Start ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_der_faellige_lauf_traegt_den_rahmen_und_den_auftrag(
    db: Session, monkeypatch
) -> None:
    user = _benutzer(db, "planer")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)

    assert run is not None
    zustand = ai_run_service.zustand_lesen(run)
    rahmen = ai_stream_service.aufgabe_aus_zustand(zustand)
    assert rahmen is not None
    assert rahmen.task_id == aufgabe.id
    assert rahmen.kind == "report"
    db.refresh(aufgabe)
    assert aufgabe.last_run_id == run.id


@pytest.mark.asyncio
async def test_der_faellige_lauf_sieht_kein_skill_verzeichnis(
    db: Session, monkeypatch
) -> None:
    """Kein Verzeichnis zu Werkzeugen, die dieser Lauf nicht bekommt.

    Der Block im Systemprompt fordert ausdrücklich dazu auf, den passenden
    Skill mit `read_skill` zu lesen — und `AUFGABEN_LESEN` bietet das nicht an.
    Der Versuch kostete eine Runde, das Verzeichnis kostete in jeder Runde
    Tokens; beides Nacht für Nacht, bei jedem fälligen Auftrag.

    Aufgelöst auf der Prompt-Seite: die Werkzeugmenge eines Laufs ohne Zeugen
    zu erweitern, ist eine Entscheidung des Betreibers, keine Aufräumarbeit.
    """
    from services.ai_context_service import build_provider_messages

    # Ausdrücklich **mit** dem Skillrecht: ohne es fällt der Block ohnehin
    # weg, und der Test würde nichts zeigen.
    user = _benutzer(db, "planer", rechte=(*KI_RECHTE, "ai.skills.use"))
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)

    assert run is not None
    systemnachricht = ai_run_service.zustand_lesen(run)["provider_messages"][0]
    assert systemnachricht["role"] == "system"
    assert "Skill-Verzeichnis" not in systemnachricht["content"]
    assert "read_skill" not in systemnachricht["content"]

    # Gegenprobe an derselben Unterhaltung: im Chat bleibt das Verzeichnis.
    conversation = (
        db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    )
    assert conversation is not None
    assert "Skill-Verzeichnis" in build_provider_messages(db, conversation)[0]["content"]


@pytest.mark.asyncio
async def test_der_auftragstext_nennt_den_plan_und_dass_niemand_davorsitzt(
    db: Session, monkeypatch
) -> None:
    """Er ist die Stelle mit dem meisten Gewicht, die es in einem Lauf gibt."""
    user = _benutzer(db, "auftrag")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user, instruction="Sag mir wie das Wetter wird.")

    text = ai_task_service._auftragstext(aufgabe)

    assert "Sag mir wie das Wetter wird." in text
    assert "08:00" in text and "Europe/Berlin" in text
    assert "Niemand sitzt gerade davor" in text
    # Der Kanal steht im Text, weil das Modell wissen muss, wie sein
    # Abschlusstext gelesen wird — als Chatblase oder als Brief.
    assert "E-Mail" in text


@pytest.mark.asyncio
async def test_ein_berichtender_auftrag_sagt_ausdruecklich_dass_nichts_veraendert_wird(
    db: Session, monkeypatch
) -> None:
    user = _benutzer(db, "nurlesen")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    bericht = _aufgabe(db, user)
    _freigabe(db, user)
    handeln = _aufgabe(db, user, title="Nachtbackup", kind="act")

    assert "veraendere nichts" in ai_task_service._auftragstext(bericht)
    assert "veraendere nichts" not in ai_task_service._auftragstext(handeln)


@pytest.mark.asyncio
async def test_ohne_laufzeit_entsteht_gar_nichts(db: Session, monkeypatch) -> None:
    """Ein Lauf, der nie loslaeuft, blockiert ueber `aktiver_lauf` alle weiteren."""
    user = _benutzer(db, "ohnelaufzeit")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    monkeypatch.setattr(ai_run_service, "http_client", lambda: None)

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    assert db.query(AiRun).count() == 0
    db.refresh(aufgabe)
    assert aufgabe.enabled is True


@pytest.mark.asyncio
async def test_ein_aktiver_lauf_vertagt_statt_abzuloesen(db: Session, monkeypatch) -> None:
    """Der Mensch chattet gerade — ihm mitten im Satz die Antwort abzuschneiden
    waere der teuerste denkbare Weg, ein Backup anzustossen."""
    user = _benutzer(db, "chattet")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    monkeypatch.setattr(
        ai_run_service, "aktiver_lauf", lambda db, *, user_id: AiRun(id="laeuft-schon")
    )

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    assert db.query(AiRun).count() == 0
    db.refresh(aufgabe)
    # **Vertagt, nicht abgeschaltet.** Der naechste Takt versucht es erneut.
    assert aufgabe.enabled is True


@pytest.mark.asyncio
async def test_die_entzogene_freigabe_legt_eine_handelnde_aufgabe_still(
    db: Session, monkeypatch
) -> None:
    """Der ausdrueckliche Wunsch des Betreibers, zu Ende gedacht.

    Beim Anlegen wurde die Freigabe geprueft. Wird sie danach zurueckgezogen,
    darf die Aufgabe nicht heimlich zu einem Vorschlagsgenerator werden, auf
    dessen Bestaetigung niemand wartet — sie wird abgeschaltet und bleibt
    sichtbar.
    """
    user = _benutzer(db, "entzogen")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    _freigabe(db, user)
    aufgabe = _aufgabe(db, user, kind="act", title="Nachtbackup")

    grant = db.query(AiAutonomyGrant).filter(AiAutonomyGrant.user_id == user.id).one()
    grant.enabled = False
    db.commit()

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    db.refresh(aufgabe)
    assert aufgabe.enabled is False
    assert aufgabe.next_run_at is None


@pytest.mark.asyncio
async def test_ein_gesperrter_benutzer_legt_seine_aufgaben_still(
    db: Session, monkeypatch
) -> None:
    user = _benutzer(db, "gesperrt")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    user.is_active = False
    db.commit()

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    db.refresh(aufgabe)
    assert aufgabe.enabled is False


@pytest.mark.asyncio
async def test_das_entzogene_recht_legt_die_aufgabe_still(
    db: Session, monkeypatch
) -> None:
    """Rechte aendern sich im Panel, Aufgaben laufen monatelang."""
    user = _benutzer(db, "rechtweg")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    set_user_roles(db, user, [])
    db.refresh(user)

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    db.refresh(aufgabe)
    assert aufgabe.enabled is False


@pytest.mark.asyncio
async def test_ohne_anbieter_wird_nicht_geraten(db: Session, monkeypatch) -> None:
    """Ein zufaellig gewaehltes Modell koennte teurer sein, als er wollte."""
    user = _benutzer(db, "zweianbieter")
    _anbieter(db, "Erster")
    _anbieter(db, "Zweiter")
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe) is None
    assert db.query(AiRun).count() == 0
    db.refresh(aufgabe)
    assert aufgabe.enabled is True


# ── Der Lauf selbst ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dem_modell_werden_nur_die_erlaubten_werkzeuge_angeboten(
    db: Session, monkeypatch
) -> None:
    user = _benutzer(db, "katalog")
    server = _server(db, "alpha")
    _sichtbar(db, user, server)
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    anbieter = Anbieter([]).einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    await _lauf_fahren(db, run)

    assert anbieter.werkzeugsaetze
    angeboten = anbieter.werkzeugsaetze[0]
    assert "ask_user" not in angeboten
    assert "propose_task_set" not in angeboten
    assert not (angeboten & AUFGABEN_HANDELN)


@pytest.mark.asyncio
async def test_ein_verbotenes_werkzeug_wird_im_code_abgewiesen(
    db: Session, monkeypatch
) -> None:
    """Das Modell ruft auf, was es nicht darf — die Schranke steht nicht im Prompt.

    Die Eingabe eines Aufgabenlaufs enthaelt Serverlogs, also Text, den ein
    Spieler geschrieben haben kann. Eine Regel, die das Modell befolgen *soll*,
    ist dagegen keine Schranke.

    Abgewiesen wird als **Werkzeugergebnis**, nicht als Abbruch. Früher verließ
    hier eine Ausnahme die Funktion, wurde im Fehlerbehandler des Segments zu
    `AI_TOOL_REJECTED` und beendete den ganzen Lauf mit 'failed' — ein einziger
    Aufruf des falschen Werkzeugs kostete damit den nächtlichen Bericht. Die
    Menge bleibt unverändert scharf: ausgeführt wird nichts, und das Modell
    bekommt eine Antwort, mit der es weiterarbeiten kann.
    """
    user = _benutzer(db, "ungehorsam")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    zustand = ai_run_service.zustand_lesen(run)
    rahmen = ai_stream_service.aufgabe_aus_zustand(zustand)

    nachrichten, benutzt, _ = await ai_stream_service._tool_followup_messages(
        user_id=user.id,
        conversation_id=run.conversation_id,
        tool_calls=[ProviderToolCall(
            id="m1", name="search_memory", arguments={"query": "Zeitzone"},
        )],
        aufgabe=rahmen,
    )

    # Gelaufen ist nichts — kein Protokolleintrag, kein Ergebnis.
    assert benutzt == []
    antworten = [n for n in nachrichten if n.get("role") == "tool"]
    assert len(antworten) == 1
    assert antworten[0]["tool_call_id"] == "m1"
    inhalt = json.loads(antworten[0]["content"])
    assert inhalt["executed"] is False
    assert "geplanten Aufgabe" in inhalt["reason"]


@pytest.mark.asyncio
async def test_eine_aufgabe_bindet_sich_an_keinen_server(
    db: Session, monkeypatch
) -> None:
    """Der Unterschied zur Heilung, an genau einer Stelle.

    Eine Heilung gehoert einem Server und weist jeden Aufruf ab, der einen
    anderen nennt. Ein stehender Auftrag gehoert keinem: "sieh nach meinen
    Servern" meint alle, die dieser Benutzer sehen darf — und welche das sind,
    entscheidet die Rechtepruefung bei jedem Aufruf ohnehin einzeln.
    """
    user = _benutzer(db, "mehrere")
    erster = _server(db, "eins")
    zweiter = _server(db, "zwei")
    _sichtbar(db, user, erster)
    _sichtbar(db, user, zweiter)
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    rahmen = ai_stream_service.aufgabe_aus_zustand(ai_run_service.zustand_lesen(run))

    nachrichten, benutzt, _ = await ai_stream_service._tool_followup_messages(
        user_id=user.id,
        conversation_id=run.conversation_id,
        tool_calls=[
            ProviderToolCall(id="s1", name="read_server_status",
                             arguments={"server_id": erster.id}),
            ProviderToolCall(id="s2", name="read_server_status",
                             arguments={"server_id": zweiter.id}),
        ],
        aufgabe=rahmen,
    )

    antworten = [n for n in nachrichten if n.get("role") == "tool"]
    assert len(antworten) == 2
    assert {eintrag["tool_name"] for eintrag in benutzt} == {"read_server_status"}


@pytest.mark.asyncio
async def test_der_rahmen_gilt_auch_in_der_dritten_runde(
    db: Session, monkeypatch
) -> None:
    """Er wird bei **jeder** Runde neu aus dem Zustand gelesen.

    Haette ihn der Lauf einmal in einer Variablen ermittelt, gaelte die
    Einengung fuer den ersten Zug und faele danach still weg — genau in dem
    Moment, in dem das Modell schon mit Werkzeugergebnissen gearbeitet hat.
    """
    user = _benutzer(db, "drei")
    server = _server(db, "gamma")
    _sichtbar(db, user, server)
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    anbieter = Anbieter([
        [ProviderToolCall(id="a", name="list_my_servers", arguments={})],
        [ProviderToolCall(id="b", name="read_server_status",
                          arguments={"server_id": server.id})],
        [ProviderToolCall(id="c", name="ask_user", arguments={
            "question": "Welchen Server meinst du?", "options": ["a", "b"],
        })],
    ]).einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    ergebnis = await _lauf_fahren(db, run)

    # Die Rueckfrage in der dritten Runde wird abgewiesen — und der Lauf endet,
    # statt auf einen Klick zu warten, den niemand tut.
    assert ergebnis.status != "waiting_user"
    assert len(anbieter.werkzeugsaetze) >= 3
    for angeboten in anbieter.werkzeugsaetze:
        assert "ask_user" not in angeboten


@pytest.mark.asyncio
async def test_ein_verbotenes_werkzeug_kostet_eine_runde_und_nicht_den_lauf(
    db: Session, monkeypatch
) -> None:
    """Der Lauf überlebt, was er nicht darf.

    `read_skill` liegt in den Lesewerkzeugen und kommt deshalb an der
    Sequenzprüfung vorbei; angeboten wird es in einer Aufgabe nicht, der
    Systemprompt bewirbt es aber wörtlich. Früher endete der Lauf an dieser
    Stelle mit 'failed' und `AI_TOOL_REJECTED` — der nächtliche Bericht fiel
    inhaltlich aus, obwohl die Aufgabe noch zu erledigen gewesen wäre.

    Jetzt kostet der Widerspruch eine Runde: der Aufruf läuft nicht, das Modell
    bekommt die Begründung als Werkzeugergebnis und antwortet.
    """
    user = _benutzer(db, "liest-skill")
    server = _server(db, "delta")
    _sichtbar(db, user, server)
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    anbieter = Anbieter([
        [ProviderToolCall(id="s1", name="read_skill",
                          arguments={"skill_key": "portkonflikt"})],
    ]).einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    ergebnis = await _lauf_fahren(db, run)

    assert ergebnis.status == "completed"
    assert ergebnis.stop_reason != "AI_TOOL_REJECTED"
    abgesagt = [
        antwort for antwort in anbieter.werkzeugantworten()
        if antwort.get("executed") is False
    ]
    assert len(abgesagt) == 1
    assert "geplanten Aufgabe" in abgesagt[0]["reason"]


@pytest.mark.asyncio
async def test_ein_wartender_lauf_entsteht_nicht(db: Session, monkeypatch) -> None:
    """Die Zusage hinter der geteilten Kennzeichnung `unbeaufsichtigt`.

    Wartet ein Aufgabenlauf auf einen Menschen, blockiert er ueber
    `aktiver_lauf` **jede** weitere Aufgabe desselben Benutzers — dauerhaft, bis
    jemand von Hand aufraeumt. Deshalb endet er lieber mit einer ehrlichen
    Fehlanzeige.
    """
    user = _benutzer(db, "wartet")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    Anbieter([
        [ProviderToolCall(id="f", name="ask_user", arguments={
            "question": "Welche Zeitzone?", "options": ["Berlin", "London"],
        })],
    ]).einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    ergebnis = await _lauf_fahren(db, run)

    assert ergebnis.status in ("completed", "failed")
    assert ai_run_service.aktiver_lauf(db, user_id=user.id) is None


@pytest.mark.asyncio
async def test_ein_bestaetigungspflichtiger_vorschlag_wird_zurueckgenommen(
    db: Session, monkeypatch
) -> None:
    """Geparkt statt zurueckgenommen waere dasselbe Leck wie oben.

    Der Lauf stuende auf 'waiting_user' und wartete auf eine Karte, die niemand
    anklickt — mit dem Unterschied, dass hier zusaetzlich ein Vorschlag in der
    Unterhaltung stehenbliebe, den der Betreiber Wochen spaeter bestaetigt.
    """
    from models import AiActionProposal

    user = _benutzer(db, "bestaetigung")
    server = _server(db, "delta")
    _sichtbar(db, user, server)
    anderer = _server(db, "fremd")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    # **Die Freigabe bleibt bestehen** — sonst legte `aufgabenlauf_starten` die
    # Aufgabe zu Recht still, und der Lauf, um den es hier geht, faende gar
    # nicht statt. Sie gilt nur fuer einen anderen Server; fuer diesen hier
    # verlangt `autonomy_allows` deshalb eine Bestaetigung, und genau die kann
    # niemand geben.
    _freigabe(db, user, server=anderer)
    Anbieter([
        [ProviderToolCall(id="w", name="propose_server_lifecycle", arguments={
            "server_id": server.id,
            "operation": "restart",
            "reason": "Der stehende Auftrag verlangt einen Neustart.",
            "expected_effect": "Der Server laeuft danach frisch.",
        })],
    ]).einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user, kind="act", title="Neustart")

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    ergebnis = await _lauf_fahren(db, run)

    assert ergebnis.status != "waiting_user"
    offen = (
        db.query(AiActionProposal)
        .filter(AiActionProposal.status == "pending")
        .count()
    )
    assert offen == 0


@pytest.mark.asyncio
async def test_zwei_faellige_aufgaben_desselben_benutzers_laufen_nacheinander(
    db: Session, monkeypatch
) -> None:
    """Nicht durch eine Sperre, sondern weil `aktiver_lauf` schon gefragt wird."""
    user = _benutzer(db, "zweiaufgaben")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    Anbieter([]).einbauen(monkeypatch)
    erste = _aufgabe(db, user, title="Erste")
    zweite = _aufgabe(db, user, title="Zweite")

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=erste)
    assert run is not None
    # Der erste Lauf steht auf 'running', das Segment ist noch nicht gefahren.
    assert await ai_task_service.aufgabenlauf_starten(db, aufgabe=zweite) is None

    await _lauf_fahren(db, run)
    db.refresh(zweite)
    zweiter_lauf = await ai_task_service.aufgabenlauf_starten(db, aufgabe=zweite)
    assert zweiter_lauf is not None
    assert zweiter_lauf.id != run.id


@pytest.mark.asyncio
async def test_der_lauf_haengt_an_der_einen_unterhaltung_des_benutzers(
    db: Session, monkeypatch
) -> None:
    """Deshalb traegt `ai_tasks` keine `conversation_id`.

    Der Verlauf soll dort stehen, wo der Betreiber ohnehin liest — und eine
    zweite Wahrheit ueber "wohin gehoert das" waere eine Spalte, die still
    veraltet, sobald jemand die Unterhaltung loescht.
    """
    from services import ai_chat_service

    user = _benutzer(db, "eineunterhaltung")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)
    erwartet = ai_chat_service.get_or_create_primary_conversation(db, user)

    assert run.conversation_id == erwartet.id


@pytest.mark.asyncio
async def test_der_auftrag_steht_als_nachricht_im_chat(db: Session, monkeypatch) -> None:
    """Der Betreiber wollte den Verlauf im Chat sehen — nicht nur das Ergebnis."""
    from models import AiMessage

    user = _benutzer(db, "sichtbar")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user, title="Wetterbericht")

    run = await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)

    nachrichten = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == run.conversation_id,
                AiMessage.role == "user")
        .all()
    )
    assert any("Wetterbericht" in (n.content or "") for n in nachrichten)


@pytest.mark.asyncio
async def test_der_guardian_bericht_wird_dem_auftrag_nicht_angehaengt(
    db: Session, monkeypatch
) -> None:
    """Sonst gilt eine Stoerung als besprochen, die kein Mensch gesehen hat.

    `lauf_beginnen` haengt einer neuen Nachricht sonst die offenen Vorfaelle an
    und vermerkt sie als gebrieft. In einem faelligen Lauf hiesse das: der
    Betreiber erfaehrt von der Stoerung nie — sie wurde ihm ja schon "gemeldet".
    """
    from models import AiGuardianNotice, Incident

    user = _benutzer(db, "briefing")
    server = _server(db, "epsilon")
    _sichtbar(db, user, server)
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    db.add(Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status="open",
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=1,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ))
    db.commit()
    aufgabe = _aufgabe(db, user)

    await ai_task_service.aufgabenlauf_starten(db, aufgabe=aufgabe)

    assert db.query(AiGuardianNotice).count() == 0
