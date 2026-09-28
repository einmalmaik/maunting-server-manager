"""Die Ethik-Engine berät, im Chat und in der Stimme, und hält nichts auf.

Befund vom 23.09.2026: die Engine hat seit ihrem Einbau am 25.08.2026 kein
einziges Mal geurteilt. Die Schreibrunde rief sie mit einem `client`, den es
dort nicht gab, und der NameError verschwand im `except`. Die Sprachwege
kannten sie gar nicht, und selbst ein Urteil hätte nur im Log gestanden.

Geprüft wird:

* `hinweis_fuers_modell`: nur ein echtes Urteil mit Bedenken erreicht das Modell.
* `beraten`: Trigger, Schwärzung, Rückfall, Fehler, kein Gedächtnis.
* Fristen: die Runde und die Stimme warten begrenzt, die Hauptschleife gar nicht.
* Chat: das Urteil steht im Werkzeugergebnis der Schreib- und der Leserunde.
* Stimme: dasselbe am Wert fürs Modell, nie an der Anzeige im Panel.

Gefälscht ist nur das Ethikmodell selbst (`stream_chat_completion`) und, für
die Fristen, der Schlüsselabruf, nicht die Entscheidung, ob und wem es zugeht.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiProvider,
    AiRun,
    AuditLog,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import (
    ai_autonomy_service,
    ai_ethics_service,
    ai_memory_service,
    ai_provider_service,
    ai_run_broker,
    ai_run_service,
    ai_stream_service,
    ai_worker_service,
)
from services.ai_ethics_service import AiEthicsEvaluation, hinweis_fuers_modell
from services.ai_ethics_trigger import should_trigger_ethics
from services.ai_stream.read_tools import voice_werkzeug_ausfuehren
from services.ai_voice.gemini_live_session import GEMINI_TOOL_TIMEOUT_SECONDS
from services.ai_voice.realtime_session import REALTIME_TOOL_TIMEOUT_SECONDS
from services.ai_voice.voice_dispatcher import dispatch_voice_action
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.role_service import set_user_roles


#: Was das Ethikmodell zu einem Neustart sagen könnte.
BEDENKEN = {
    "assessment": "review",
    "confidence": 0.8,
    "concerns": ["Auf dem Server spielen gerade Leute."],
    "affected_interests": ["Spieler"],
    "possible_harm": ["Alle Verbindungen brechen ab."],
    "alternative": "Erst im Spiel ankündigen, dann neu starten.",
    "recommendation": "Vorher die Spieler warnen.",
    "reason": "Ein Neustart trennt alle Verbindungen.",
}

#: Der Lauf prüft nur, **dass** ein Client da ist; gestreamt wird gefälscht.
_KEIN_CLIENT = object()


def _ethikanbieter(db: Session, *, modus: str = "auto", **felder) -> AiProvider:
    """Ein Anbieter mit eingerichteter Ethik-Engine, ohne Schlüsselpflicht."""
    anbieter = AiProvider(
        name=f"Ethik-{uuid4().hex[:6]}",
        provider_kind="openrouter",
        default_model="chat-modell",
        enabled=True,
        requires_api_key=False,
        ethics_enabled=True,
        ethics_model="ethik-modell",
        ethics_mode=modus,
    )
    for name, wert in felder.items():
        setattr(anbieter, name, wert)
    db.add(anbieter)
    db.commit()
    db.refresh(anbieter)
    return anbieter


def _ethikmodell(
    monkeypatch: pytest.MonkeyPatch,
    antwort: dict = BEDENKEN,
    *,
    gesehen: list | None = None,
    fehler: Exception | None = None,
    dauer: float = 0.0,
) -> None:
    """Das Ethikmodell, gefälscht an seinem Aufruf und sonst nirgends."""

    async def fake(_client, *, provider, api_key, messages, usage, model=None, **_):
        del provider, api_key, usage
        if gesehen is not None:
            gesehen.append({"model": model, "messages": messages})
        # Ein echter Strom wartet mindestens einmal auf das Netz; erst dort
        # kann ein Abbruch ihn erreichen.
        await asyncio.sleep(dauer)
        if fehler is not None:
            raise fehler
        yield StreamChunk("content", json.dumps(antwort))

    monkeypatch.setattr(ai_ethics_service, "stream_chat_completion", fake)


def _schluesselabruf_haengt(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """`resolve_api_key` hängt, bis der Test ihn freigibt.

    So sieht ein träger DIS-Sidecar aus: der Abruf ist synchron und dauert bis
    zu fünfzehn Sekunden, und keine Zeitgrenze einer Schleife unterbricht ihn.
    """
    freigabe = threading.Event()

    def haengt(*_args, **_kwargs):
        freigabe.wait(10)
        return None

    monkeypatch.setattr(ai_provider_service, "resolve_api_key", haengt)
    return freigabe


def _beratungen_mitschneiden(monkeypatch: pytest.MonkeyPatch) -> list:
    """Die Zukünfte der Beratungsthreads, damit der Test auf sie warten kann.

    Eine Beratung über ihre Frist hinaus läuft im Hintergrund zu Ende. Endete
    der Test vorher, räumte das Aufräumen der Testdatenbank unter ihr weg.
    """
    zukuenfte: list = []
    echt = ai_ethics_service._beratung_starten

    def mitschneiden(**kwargs):
        beratung = echt(**kwargs)
        zukuenfte.append(beratung.zukunft)
        return beratung

    monkeypatch.setattr(ai_ethics_service, "_beratung_starten", mitschneiden)
    return zukuenfte


def _benutzer(
    db: Session, user: User, *, autonom: bool, rechte: tuple[str, ...] = ()
) -> AiConversation:
    """Chatrechte, auf Wunsch die Autonomie-Freigabe dazu, und eine Unterhaltung."""
    alle = ["ai.chat.use", *rechte]
    if autonom:
        alle.append("ai.autonomous.use")
    rolle = Role(name=f"ethik-{uuid4().hex[:8]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for recht in alle:
        db.add(RolePermission(role_id=rolle.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    if autonom:
        ai_autonomy_service.set_grant(
            db, user=user, server_id=None, enabled=True,
            max_actions_per_hour=50, granted_by=user.id,
        )
    unterhaltung = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Ethik"
    )
    db.add(unterhaltung)
    db.commit()
    return unterhaltung


def _neustart(server_id: int = 1, *, kennung: str = "w1", **extra) -> ProviderToolCall:
    return ProviderToolCall(
        id=kennung,
        name="propose_server_lifecycle",
        arguments={
            "server_id": server_id,
            "operation": "restart",
            "reason": "Der Benutzer hat den Neustart verlangt.",
            "expected_effect": "Der Server startet neu.",
            **extra,
        },
    )


def _suche(kennung: str = "s1") -> ProviderToolCall:
    return ProviderToolCall(id=kennung, name="search_docs", arguments={"query": "backup"})


# ── Was das Modell erfährt ─────────────────────────────────────────────────


def test_nur_ein_urteil_mit_bedenken_erreicht_das_modell() -> None:
    """Ein `low` ändert nichts an der Handlung, und der Rückfall ist kein Urteil.

    Der Rückfall für eine Löschung heißt „critical", aber nur, weil die
    Tabelle das so ableitet. Ginge er ans Modell, stünde vor jeder Löschung
    eine Warnung, die niemand geschrieben hat.
    """
    kontext = should_trigger_ethics("propose_file_delete", {}).decision_context
    rueckfall = ai_ethics_service.fallback_evaluation(kontext)
    assert rueckfall.assessment == "critical"
    assert hinweis_fuers_modell(rueckfall) is None

    assert hinweis_fuers_modell(AiEthicsEvaluation(**{**BEDENKEN, "assessment": "low"})) is None

    hinweis = hinweis_fuers_modell(AiEthicsEvaluation(**BEDENKEN))
    assert hinweis == {
        "untrusted": True,
        "quelle": "ethik_engine",
        "einschaetzung": "review",
        "empfehlung": "Vorher die Spieler warnen.",
        "begruendung": "Ein Neustart trennt alle Verbindungen.",
        "bedenken": ["Auf dem Server spielen gerade Leute."],
        "alternative": "Erst im Spiel ankündigen, dann neu starten.",
    }


def test_eine_antwort_kann_sich_nicht_zum_rueckfall_erklaeren() -> None:
    """Das Rückfallzeichen ist privat.

    Das Ethikmodell hat die Werkzeugargumente gelesen, und die können aus
    einer Logzeile stammen. Könnte seine Antwort das Zeichen setzen, ließe
    sich jedes Urteil stummschalten.
    """
    bewertung = AiEthicsEvaluation(**{**BEDENKEN, "_ersatz": True})
    assert bewertung._ersatz is False
    assert hinweis_fuers_modell(bewertung) is not None


def test_der_hinweis_ist_geschwaerzt() -> None:
    token = "Bearer " + "abc123" * 6
    bewertung = AiEthicsEvaluation(**{**BEDENKEN, "recommendation": f"Nutze {token} dafür."})

    hinweis = hinweis_fuers_modell(bewertung)

    assert token not in json.dumps(hinweis)


# ── Wem das Urteil zugeht ──────────────────────────────────────────────────


def test_die_engine_des_laufs_geht_vor(db: Session) -> None:
    """Bevorzugt der Anbieter des Laufs, sonst der erste mit Engine und Schlüssel."""
    ohne_schluessel = _ethikanbieter(db, requires_api_key=True)
    erster = _ethikanbieter(db)
    zweiter = _ethikanbieter(db)
    ohne_engine = _ethikanbieter(db, ethics_enabled=False)

    assert ai_ethics_service.ethik_anbieter(db, zweiter.id).id == zweiter.id
    # Der Rückfall überspringt den Anbieter, dem der Schlüssel fehlt.
    assert ai_ethics_service.ethik_anbieter(db, ohne_engine.id).id == erster.id
    assert ai_ethics_service.ethik_anbieter(db, None).id == erster.id
    assert ohne_schluessel.id < erster.id


def test_ohne_eingerichtete_engine_keine_beratung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ethikanbieter(db, ethics_enabled=False)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)

    hinweise = asyncio.run(ai_ethics_service.beraten(
        user_id=regular_user.id, aufrufe=[_neustart()]
    ))

    assert hinweise == {}
    assert gesehen == []


# ── Die Beratung selbst ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_der_trigger_entscheidet_worueber_beraten_wird(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Im Modus `auto` ist Lesen kein Anlass, ein Neustart schon."""
    anbieter = _ethikanbieter(db)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)

    hinweise = await ai_ethics_service.beraten(
        user_id=regular_user.id,
        aufrufe=[_suche("lesen"), _neustart(kennung="schreiben")],
        bevorzugt_id=anbieter.id,
    )

    assert list(hinweise) == ["schreiben"]
    assert hinweise["schreiben"]["einschaetzung"] == "review"
    assert [aufruf["model"] for aufruf in gesehen] == ["ethik-modell"]
    # Ein Urteil mit Bedenken steht im Audit, wie seit dem Einbau vorgesehen.
    db.expire_all()
    eintrag = db.query(AuditLog).filter(AuditLog.action == "ai.ethics.evaluated").one()
    assert eintrag.user_id == regular_user.id


@pytest.mark.asyncio
async def test_die_argumente_gehen_geschwaerzt_hinaus(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Ethikmodell kann bei einem anderen Anbieter liegen als das Chatmodell."""
    _ethikanbieter(db)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)
    geheim = "hun" + "ter2"

    await ai_ethics_service.beraten(
        user_id=regular_user.id,
        aufrufe=[_neustart(umgebung={"RCON_PASSWORD": geheim})],
    )

    assert len(gesehen) == 1
    gesendet = json.dumps(gesehen[0]["messages"], ensure_ascii=False)
    assert geheim not in gesendet
    assert "RCON_PASSWORD" in gesendet


@pytest.mark.asyncio
async def test_das_gedaechtnis_geht_nicht_an_das_ethikmodell(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Für die Abwägung genügen Werkzeug und Argumente.

    Der tote Aufruf vor dem 23.09.2026 schickte `provider_memory_context` mit:
    an einen Anbieter, der ein anderer sein kann als der des Chats, auch aus
    Workerläufen, die kein persönliches Gedächtnis sehen (Review vom
    23.09.2026).
    """
    _benutzer(db, regular_user, autonom=False, rechte=("ai.memory.use",))
    ai_memory_service.set_preference(db, regular_user, True)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="urlaub", value="Bis Freitag in Lissabon",
    )
    db.commit()
    # Der Gegenstand des Tests: ein Gedächtnisblock hätte diesen Eintrag.
    assert "Lissabon" in (
        ai_memory_service.provider_memory_context(
            db, regular_user, query="propose_server_lifecycle"
        ) or ""
    )
    _ethikanbieter(db)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)

    hinweise = await ai_ethics_service.beraten(
        user_id=regular_user.id, aufrufe=[_neustart()]
    )

    assert hinweise["w1"]["einschaetzung"] == "review"
    assert len(gesehen) == 1
    assert "Lissabon" not in json.dumps(gesehen[0]["messages"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_ein_rueckfall_bringt_keinen_hinweis(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ethikanbieter(db)
    _ethikmodell(monkeypatch, fehler=RuntimeError("Anbieter weg"))

    hinweise = await ai_ethics_service.beraten(
        user_id=regular_user.id, aufrufe=[_neustart()]
    )

    assert hinweise == {}


@pytest.mark.asyncio
async def test_ein_langsames_ethikmodell_kostet_hoechstens_die_frist(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ethikanbieter(db)
    _ethikmodell(monkeypatch, dauer=30.0)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)
    monkeypatch.setattr(ai_ethics_service, "BERATUNG_ZEITGRENZE_SEKUNDEN", 0.05)

    beginn = time.monotonic()
    hinweise = await ai_ethics_service.beraten(
        user_id=regular_user.id, aufrufe=[_neustart()]
    )
    dauer = time.monotonic() - beginn
    for zukunft in zukuenfte:
        zukunft.result(timeout=10)

    assert hinweise == {}
    assert dauer < 5


@pytest.mark.asyncio
async def test_ein_haengender_schluesselabruf_haelt_die_runde_nicht_auf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synchroner Code lässt sich von keiner Zeitgrenze der Schleife unterbrechen.

    Deshalb wartet die Runde auf den Beratungsthread und nicht in ihm. Vorher
    hätte ein hängender Abruf die Frist einfach überdauert.
    """
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)
    freigabe = _schluesselabruf_haengt(monkeypatch)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)
    monkeypatch.setattr(ai_ethics_service, "BERATUNG_ZEITGRENZE_SEKUNDEN", 0.2)

    beginn = time.monotonic()
    try:
        hinweise = await ai_ethics_service.beraten(
            user_id=regular_user.id, aufrufe=[_neustart()]
        )
        dauer = time.monotonic() - beginn
    finally:
        freigabe.set()
        for zukunft in zukuenfte:
            zukunft.result(timeout=10)

    assert hinweise == {}
    assert dauer < 2


@pytest.mark.asyncio
async def test_die_hauptschleife_laeuft_waehrend_der_beratung_weiter(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Schlüsselabruf blockiert den Beratungsthread, nicht das Panel.

    Lief die Beurteilung auf der Hauptschleife, beantwortete der Prozess
    während eines trägen DIS-Abrufs keine einzige andere Anfrage.
    """
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)
    freigabe = _schluesselabruf_haengt(monkeypatch)

    async def taktgeber() -> None:
        for _ in range(20):
            await asyncio.sleep(0.01)
        freigabe.set()

    beginn = time.monotonic()
    uhr = asyncio.create_task(taktgeber())
    try:
        hinweise = await ai_ethics_service.beraten(
            user_id=regular_user.id, aufrufe=[_neustart()]
        )
        dauer = time.monotonic() - beginn
    finally:
        freigabe.set()
        await uhr

    # Den Abruf gibt nur der Taktgeber auf der Hauptschleife frei. Stünde sie,
    # hinge er bis zu seinem eigenen Ablauf nach zehn Sekunden.
    assert dauer < 5
    assert hinweise["w1"]["einschaetzung"] == "review"


def test_die_stimme_wartet_nur_ihre_frist(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Werkzeuge der Stimme haben zwölf Sekunden, die Beratung davon fünf.

    Mit den fünfzehn Sekunden des Chats lief ein Werkzeug hinter einem trägen
    Ethikmodell in seine Zeitgrenze, meldete „nicht abgewartet" und führte im
    Hintergrund trotzdem aus. Nach der Frist wird die Beurteilung abgebrochen:
    ein Urteil über einen Aufruf, der längst weiter ist, stünde nur im Audit.
    """
    assert ai_ethics_service.BERATUNG_ZEITGRENZE_STIMME < min(
        REALTIME_TOOL_TIMEOUT_SECONDS, GEMINI_TOOL_TIMEOUT_SECONDS
    )
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)
    freigabe = _schluesselabruf_haengt(monkeypatch)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)

    beginn = time.monotonic()
    try:
        hinweise = ai_ethics_service.beraten_im_thread(
            user_id=regular_user.id, aufrufe=[_neustart()], frist=0.2
        )
        dauer = time.monotonic() - beginn
    finally:
        freigabe.set()
        for zukunft in zukuenfte:
            zukunft.result(timeout=10)

    assert hinweise == {}
    assert dauer < 2
    db.expire_all()
    assert db.query(AuditLog).filter(AuditLog.action == "ai.ethics.evaluated").count() == 0


@pytest.mark.asyncio
async def test_ein_beendeter_lauf_beendet_seine_beratung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stoppt der Benutzer den Lauf, belegt dessen Beratung keinen Thread weiter."""
    _ethikanbieter(db)
    _ethikmodell(monkeypatch, dauer=30.0)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)

    lauf = asyncio.create_task(
        ai_ethics_service.beraten(user_id=regular_user.id, aufrufe=[_neustart()])
    )
    while not zukuenfte or not zukuenfte[0].running():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    lauf.cancel()
    with pytest.raises(asyncio.CancelledError):
        await lauf

    # Ohne Abbruch schliefe das Ethikmodell hier noch dreißig Sekunden.
    zukuenfte[0].result(timeout=5)


@pytest.mark.asyncio
async def test_ein_spaeter_fehler_nimmt_fertige_hinweise_nicht_mit(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was vor einem Fehler fertig war, erreicht das Modell trotzdem."""
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)
    echt = ai_ethics_service.hinweis_fuers_modell
    gesehen: list = []

    def zweiter_scheitert(bewertung):
        gesehen.append(bewertung)
        if len(gesehen) == 2:
            raise RuntimeError("kaputt")
        return echt(bewertung)

    monkeypatch.setattr(ai_ethics_service, "hinweis_fuers_modell", zweiter_scheitert)

    hinweise = await ai_ethics_service.beraten(
        user_id=regular_user.id,
        aufrufe=[_neustart(kennung="erster"), _neustart(kennung="zweiter")],
    )

    assert list(hinweise) == ["erster"]


def test_eine_beratung_ohne_freien_thread_entfaellt_nach_der_frist(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer nach der Frist noch in der Warteschlange steht, läuft nicht mehr.

    Sonst holte eine volle Warteschlange Urteile über Aufrufe nach, die längst
    gelaufen sind: jedes ein Modellaufruf und ein Auditeintrag für nichts.
    """
    _ethikanbieter(db)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)
    freigabe = threading.Event()
    berater = ai_ethics_service._BERATER
    belegt = [berater.submit(freigabe.wait, 10) for _ in range(berater._max_workers)]

    try:
        hinweise = ai_ethics_service.beraten_im_thread(
            user_id=regular_user.id, aufrufe=[_neustart()], frist=0.2
        )
    finally:
        freigabe.set()
        for zukunft in belegt:
            zukunft.result(timeout=10)

    assert hinweise == {}
    assert len(zukuenfte) == 1 and zukuenfte[0].cancelled()
    assert gesehen == []


@pytest.mark.asyncio
async def test_ein_fehler_der_beratung_beendet_keinen_lauf(
    regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Beratung steht vor der Schreibrunde; ein Fehler dort ist nur kein Rat."""

    def kaputt(**_):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(ai_ethics_service, "_faellige", kaputt)

    assert await ai_ethics_service.beraten(
        user_id=regular_user.id, aufrufe=[_neustart()]
    ) == {}
    assert ai_ethics_service.beraten_im_thread(
        user_id=regular_user.id, aufrufe=[_neustart()]
    ) == {}


def test_im_arbeitsthread_der_stimme(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Sprachwege sind synchron und laufen in `asyncio.to_thread`."""
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)

    hinweise = ai_ethics_service.beraten_im_thread(
        user_id=regular_user.id, aufrufe=[_neustart(kennung="t1")]
    )

    assert hinweise["t1"]["einschaetzung"] == "review"


@pytest.mark.asyncio
async def test_auch_aus_einer_laufenden_schleife(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dort scheiterte `asyncio.run`; die Beratung geht in einen eigenen Thread."""
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)

    hinweise = ai_ethics_service.beraten_im_thread(
        user_id=regular_user.id, aufrufe=[_neustart(kennung="t2")]
    )

    assert hinweise["t2"]["einschaetzung"] == "review"


# ── Chat: Schreibrunde und Leserunde ───────────────────────────────────────


def _auftraggeber(db: Session, name: str) -> User:
    user = User(
        username=name, email_encrypted="x", email_hash=name,
        password_hash="x", is_active=True,
    )
    db.add(user)
    db.flush()
    rolle = Role(name=f"ethik-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in ("ai.chat.use", "ai.background.use"):
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.commit()
    return user


def _server(db: Session, user: User, name: str) -> Server:
    server = Server(
        name=name, game_type="dayz", install_dir=f"/tmp/{name}",
        status="running", container_name=f"msm-{name}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in ("server.view", "server.restart"):
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()
    return server


@pytest.mark.asyncio
async def test_die_schreibrunde_gibt_das_urteil_ans_modell(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein ganzer Lauf: Neustart vorgeschlagen, Urteil im Werkzeugergebnis.

    Das ist der Pfad, an dem die Engine seit dem 25.08.2026 still scheiterte.
    Die zweite Runde des Modells sieht jetzt, was sie einwendet, und kann es
    dem Menschen sagen, bevor er die Karte bestätigt.
    """
    user = _auftraggeber(db, "ethikchat")
    _ethikanbieter(db)
    server = _server(db, user, "ethikark")
    _ethikmodell(monkeypatch)
    runden: list[list[dict]] = []

    async def chatmodell(
        _client, *, provider, api_key, messages, usage: StreamUsage,
        tools=None, tool_choice=None, **_,
    ):
        del provider, api_key, tools
        runden.append(list(messages))
        usage.total_tokens = 10
        if tool_choice != "none" and len(runden) == 1:
            usage.tool_calls = [_neustart(server.id)]
        yield StreamChunk("content", "Ich lege den Neustart vor.")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", chatmodell)

    with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
        ergebnis = ai_worker_service.worker_start(
            db, user=user,
            arguments={"auftrag": "Starte den Server neu", "titel": "Neustart"},
        )
    run = db.query(AiRun).filter(AiRun.conversation_id == ergebnis["worker_id"]).one()
    ai_run_broker.eroeffnen(run.id)
    ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)

    assert len(runden) >= 2
    werkzeugantworten = [
        json.loads(nachricht["content"])
        for nachricht in runden[-1]
        if nachricht.get("role") == "tool"
    ]
    assert werkzeugantworten, "die zweite Runde sah kein Werkzeugergebnis"
    antwort = werkzeugantworten[-1]
    assert antwort["tool"] == "propose_server_lifecycle"
    assert antwort["ethik"]["einschaetzung"] == "review"
    assert antwort["ethik"]["empfehlung"] == "Vorher die Spieler warnen."


@pytest.mark.asyncio
async def test_die_leserunde_gibt_das_urteil_ans_modell(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mit Freigabe laufen Desktop-Werkzeuge und `worker_start` über diesen Weg.

    Stellvertretend ein Lesewerkzeug im Modus `always`: die Leitung ist
    dieselbe, und ein Desktop-Werkzeug bräuchte einen Rechner.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=True)
    anbieter = _ethikanbieter(db, modus="always")
    _ethikmodell(monkeypatch)
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", lambda *a, **k: {"treffer": []})

    nachrichten, _, _ = await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=unterhaltung.id,
        tool_calls=[_suche()],
        ethik_anbieter_id=anbieter.id,
    )

    antwort = json.loads(next(n for n in nachrichten if n.get("role") == "tool")["content"])
    assert antwort["untrusted"] is True
    assert antwort["data"] == {"treffer": []}
    assert antwort["ethik"]["einschaetzung"] == "review"


@pytest.mark.asyncio
async def test_ohne_bedenken_bleibt_das_ergebnis_wie_es_war(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Lesen im Modus `auto` kostet keinen Modellaufruf."""
    unterhaltung = _benutzer(db, regular_user, autonom=True)
    anbieter = _ethikanbieter(db)
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", lambda *a, **k: {"treffer": []})

    nachrichten, _, _ = await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=unterhaltung.id,
        tool_calls=[_suche()],
        ethik_anbieter_id=anbieter.id,
    )

    antwort = json.loads(next(n for n in nachrichten if n.get("role") == "tool")["content"])
    assert antwort == {"untrusted": True, "tool": "search_docs", "data": {"treffer": []}}
    assert gesehen == []


# ── Stimme: dem Modell, nicht dem Schirm ───────────────────────────────────


def test_die_stimme_gibt_das_urteil_dem_modell_nicht_dem_schirm(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    unterhaltung = _benutzer(db, regular_user, autonom=True)
    _ethikanbieter(db, modus="always")
    _ethikmodell(monkeypatch)
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", lambda *a, **k: {"treffer": []})

    wert, fehler, anzeige, vorschlaege = voice_werkzeug_ausfuehren(
        regular_user.id, _suche(), conversation_id=unterhaltung.id
    )

    assert fehler is None
    assert vorschlaege == []
    assert wert["treffer"] == []
    assert wert["ethik"]["einschaetzung"] == "review"
    assert "ethik_engine" not in json.dumps(anzeige, default=str)


def test_die_stimme_beraet_neben_dem_werkzeug(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Werkzeug wartet nicht auf die Beratung; beide laufen zugleich.

    Hier gibt erst das Werkzeug den hängenden Schlüsselabruf frei. Liefe die
    Beratung davor, wartete sie ihre fünf Sekunden ab und käme ohne Urteil:
    Beratung und Werkzeug teilten sich die zwölf Sekunden der Stimme (Review
    vom 23.09.2026).
    """
    unterhaltung = _benutzer(db, regular_user, autonom=True)
    _ethikanbieter(db, modus="always")
    _ethikmodell(monkeypatch)
    freigabe = _schluesselabruf_haengt(monkeypatch)
    zukuenfte = _beratungen_mitschneiden(monkeypatch)

    def werkzeug(*_args, **_kwargs):
        freigabe.set()
        return {"treffer": []}

    monkeypatch.setattr(ai_stream_service, "execute_read_tool", werkzeug)
    beginn = time.monotonic()
    try:
        wert, fehler, _, _ = voice_werkzeug_ausfuehren(
            regular_user.id, _suche(), conversation_id=unterhaltung.id
        )
        dauer = time.monotonic() - beginn
    finally:
        freigabe.set()
        for zukunft in zukuenfte:
            zukunft.result(timeout=10)

    assert fehler is None
    assert wert["ethik"]["einschaetzung"] == "review"
    assert dauer < ai_ethics_service.BERATUNG_ZEITGRENZE_STIMME


def test_ohne_freigabe_steht_das_urteil_vor_der_entscheidung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Karte wartet auf das Ja, und das Modell kennt die Bedenken schon."""
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    _ethikanbieter(db, modus="always")
    _ethikmodell(monkeypatch)
    gelaufen: list = []
    monkeypatch.setattr(
        ai_stream_service, "execute_read_tool", lambda *a, **k: gelaufen.append(1) or {}
    )

    wert, _, anzeige, vorschlaege = voice_werkzeug_ausfuehren(
        regular_user.id, _suche(), conversation_id=unterhaltung.id
    )

    assert wert["status"] == "needs_confirmation"
    assert wert["ethik"]["einschaetzung"] == "review"
    assert [v["tool_name"] for v in vorschlaege] == ["search_docs"]
    assert "ethik_engine" not in json.dumps(anzeige, default=str)
    assert gelaufen == []


def test_ein_schreibvorschlag_der_stimme_traegt_das_urteil(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    unterhaltung = _benutzer(db, regular_user, autonom=False, rechte=("ai.notes.use",))
    _ethikanbieter(db)
    _ethikmodell(monkeypatch)

    wert, _, anzeige, vorschlaege = voice_werkzeug_ausfuehren(
        regular_user.id,
        ProviderToolCall(
            id="n1",
            name="propose_note_create",
            arguments={
                "title": "Einkauf",
                "content": "Milch",
                "rationale": "Der Benutzer hat die Liste diktiert.",
            },
        ),
        conversation_id=unterhaltung.id,
    )

    assert wert["ethik"]["einschaetzung"] == "review"
    assert [v.get("tool_name") for v in vorschlaege] == ["propose_note_create"]
    assert "ethik_engine" not in json.dumps(anzeige, default=str)


def test_der_umweg_wird_ueber_das_ziel_beraten(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`execute_server_action` erreicht jedes Werkzeug; beraten wird das Ziel."""
    unterhaltung = _benutzer(db, regular_user, autonom=True)
    _ethikanbieter(db, modus="always")
    gesehen: list = []
    _ethikmodell(monkeypatch, gesehen=gesehen)
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", lambda *a, **k: {"treffer": []})

    wert, fehler, _, _ = dispatch_voice_action(
        regular_user.id,
        {"tool_name": "search_docs", "parameters": {"query": "backup"}},
        conversation_id=unterhaltung.id,
    )

    assert fehler is None
    assert wert["executed_tool"] == "search_docs"
    assert wert["data"] == {"treffer": []}
    assert wert["ethik"]["einschaetzung"] == "review"
    assert "Werkzeug: search_docs" in json.dumps(gesehen[0]["messages"], ensure_ascii=False)
