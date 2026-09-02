"""Live-Autonomie-Benchmark für den MSM KI-Agenten.

Die Nachbarn messen Tempo (``test_ai_benchmark_live.py``) und Güte
(``test_ai_qualitaet_live.py``) an einzelnen Anfragen. Diese Datei misst etwas
Drittes: wie weit der Assistent einen echten, unscharf formulierten Auftrag
**allein** trägt — welche Werkzeuge er von sich aus zieht, ob am Ende ein
Änderungsvorschlag steht und wie oft er stattdessen zurückfragt.

Zwei Szenarien, beide gegen das Luna-Modell (OpenRouter):

1. ``asa_visibility_troubleshooting`` — "der Server taucht nicht in der Liste
   auf". Erwartet wird, dass die KI nachsieht statt zu raten: Netzwerkangaben
   oder Erreichbarkeit, Blueprint, Websuche.
2. ``gameplay_config_and_password`` — Name, Passwort und Multiplikatoren in
   einem Satz. Erwartet wird, dass sie die zuständigen Konfigurationen selbst
   findet und einen Vorschlag erzeugt, statt sich durch eine Rückfrageschleife
   zu hangeln.

**Ausführen**

    cd backend
    set -a; source ~/.msm-bench.env; set +a
    python -m pytest tests/test_ai_autonomie_benchmark_live.py -o addopts="" -q -s

``-o addopts=""`` ist Pflicht: die ``pytest.ini`` setzt ``-n auto`` (acht Läufe,
die gegeneinander messen, ergeben Rauschen), ``--timeout=120`` (bricht die
längeren Szenarien mitten in der Messung ab) und ``-m "not live"`` — genau die
Marke, die diese Datei trägt.

**Zwei Schlösser, und beide muss der Aufrufer bewusst öffnen.** Hier stand
einmal ein ``_lade_bench_env()``, das beim Import ``~/.msm-bench.env`` **und**
``backend/.env`` nach ``os.environ`` schrieb — und zwar bevor ``BENCH_KEY``
gelesen wurde, auf dem die Sperre beruht. Damit sperrte die Sperre nichts: ein
schlichtes ``python -m pytest`` startete bezahlte Läufe gegen OpenRouter, und
weil diese Datei alphabetisch vor den beiden Nachbarn importiert wird, gleich
deren Modulebene mit. Dass ein Test die Produktiv-Umgebungsdatei ``backend/.env``
überhaupt aufmacht, war der zweite Fehler daran. Der Schlüssel kommt jetzt aus
der Umgebung, die der Aufrufer selbst gesetzt hat — wie in
``test_ai_live_openrouter.py`` und ``test_ai_qualitaet_live.py``. Und weil eine
Datei im Heimatverzeichnis ohnehin nie darüber entscheiden sollte, ob Geld
ausgegeben wird, liegt die Datei zusätzlich hinter der Marke ``live``.

Das Protokoll landet als JSON unter ``backend/logs/ai-benchmark/`` (gitignoriert)
und geht vorher durch dieselbe Schwärzung wie der Produktivpfad.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiConversation, AiProvider, Server, ServerPort, User
from services import (
    ai_chat_service,
    ai_context_window,
    ai_reasoning,
    ai_run_broker,
    ai_stream_service,
)
from services.ai_provider_service import _operator_aad
from services.ai_redaction import redact_sensitive_text

BENCH_KEY = os.environ.get("MSM_BENCH_AI_KEY", "").strip()
BENCH_MODEL = os.environ.get("MSM_BENCH_AI_MODEL", "openai/gpt-5.6-luna").strip()
BENCH_LABEL = os.environ.get("MSM_BENCH_LABEL", "autonomie-benchmark").strip()

#: Reissleine je Szenario, innerhalb des Tests. Ein Autonomielauf ist nicht eine
#: Anfrage, sondern mehrere Runden mit Werkzeugaufrufen dazwischen; drei Minuten
#: sind der beobachtete Rahmen für die längeren davon. Sie greift **vor** der
#: Zeitgrenze des Testlaufs (siehe ``pytestmark``), damit im Stillstandsfall noch
#: das Protokoll geschrieben wird und man sieht, wo der Lauf stehen blieb.
BENCH_TIMEOUT = float(os.environ.get("MSM_BENCH_TIMEOUT", "180"))

pytestmark = [
    # Die Marke ist das Schloss, das nicht vom Zustand des Rechners abhängt:
    # ``pytest.ini`` wählt ``live`` standardmäßig ab, ein voller Suitenlauf kann
    # hier also gar nichts auslösen, auch wenn der Schlüssel gesetzt ist.
    pytest.mark.live,
    pytest.mark.skipif(
        not BENCH_KEY,
        reason="MSM_BENCH_AI_KEY nicht gesetzt — Benchmark übersprungen",
    ),
    # Die äußere Grenze muss über der inneren liegen, sonst reisst sie zuerst und
    # das Protokoll bleibt leer. Die 30 Sekunden Aufschlag sind der Platz für
    # Aufbau, Datenbank und das Schreiben des Protokolls.
    pytest.mark.timeout(BENCH_TIMEOUT + 30),
]

LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "ai-benchmark"

#: Der Auftrag im Wortlaut des Betreibers, Tippfehler eingeschlossen — genau die
#: Unschärfe ist der Prüfgegenstand.
AUFTRAG_ASA_SICHTBARKEIT = (
    "Ok der asa server taucht nicht in der server liste auf da scheint was nicht zu stimmen"
)

#: Hier stand einmal ein echt aussehendes Passwort. Es wanderte von hier in die
#: Unterhaltung, von dort in die Antwort des Modells und aus der Antwort in das
#: Protokoll unter ``logs/ai-benchmark/`` — ein Zugangsdatum, das im Repo
#: beginnt, ist eines, das im nächsten Bugreport als Anhang landet. Der
#: Platzhalter ist bewusst so gewählt, dass er auf kein Zugangsdatenmuster passt
#: und niemand ihn versehentlich für ein echtes Passwort hält; für das, was
#: gemessen wird — findet die KI die Stelle und schlägt sie die Änderung vor —
#: ist der Wert selbst ohne Bedeutung.
AUFTRAG_GAMEPLAY = (
    "Gut stelle den server Namen auf MauntARK passowrt auf platzhalter-bench "
    "ernte und loot auf das doppelte machen und zähm geschwindikeit auf das 4 fache "
    "und alles sehr casual machen aber mit etwas herausfoderung"
)


@dataclass
class AutonomieErgebnis:
    """Was ein Szenariolauf hinterlässt — Kennzahlen und der rohe Antworttext.

    Der Text bleibt hier unbehandelt: die Zusicherungen sollen den sehen, den das
    Modell wirklich erzeugt hat. Geschwärzt wird erst beim Schreiben, weil erst
    dort etwas den Prozess verlässt.
    """

    szenario: str
    dauer: float = 0.0
    ttft: float = 0.0
    werkzeuge: list[str] = field(default_factory=list)
    vorschlaege: list[str] = field(default_factory=list)
    fragen: list[str] = field(default_factory=list)
    antwort_text: str = ""


def _setup_test_environment(db: Session) -> tuple[User, AiProvider, Server]:
    """Erstellt Testbenutzer, Provider und ASA-Server in SQLite."""
    user = db.query(User).filter(User.username == "bench_autonomie_owner").first()
    if user is None:
        user = User(
            username="bench_autonomie_owner",
            password_hash="x",
            is_active=True,
            is_owner=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    from services.dis_client import DisClient

    provider = db.query(AiProvider).filter(AiProvider.name == "OpenRouter (autonomie-bench)").first()
    if provider is None:
        provider = AiProvider(
            name="OpenRouter (autonomie-bench)",
            provider_kind="openrouter",
            default_model=BENCH_MODEL,
            enabled=True,
            requires_api_key=True,
        )
        db.add(provider)
        db.flush()
        provider.operator_api_key_encrypted = DisClient.encrypt(
            BENCH_KEY, aad=_operator_aad(provider.id)
        )
        db.commit()
        db.refresh(provider)
    else:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            BENCH_KEY, aad=_operator_aad(provider.id)
        )
        db.commit()
        db.refresh(provider)

    server = db.query(Server).filter(Server.id == 104).first()
    if server is None:
        server = Server(
            id=104,
            name="MauntASA",
            game_type="ark_survival_ascended",
            status="running",
            install_dir="/tmp/msm_bench_asa_104",
            container_name="msm-srv-bench-asa104",
            public_bind_ip="0.0.0.0",
        )
        db.add(server)
        db.commit()
        db.refresh(server)

        db.add_all([
            ServerPort(server_id=server.id, port=27045, protocol="udp", role="game"),
            ServerPort(server_id=server.id, port=27046, protocol="udp", role="query"),
            ServerPort(server_id=server.id, port=27047, protocol="tcp", role="rcon"),
        ])
        db.commit()

    return user, provider, server


def _prepare_conversation(db: Session, user: User) -> AiConversation:
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    ai_chat_service.clear_history(db, conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _protokoll_schreiben(ergebnis: AutonomieErgebnis) -> None:
    """Legt den Lauf als JSON ab — durch dieselbe Schwärzung wie der Produktivpfad.

    Antworttext und Rückfragen sind roher Modelltext, und das Modell zitiert
    darin, was es gelesen hat: Konfigurationszeilen, und in einer
    Konfigurationszeile steht das Server- oder RCON-Passwort. Ungeschwärzt läge
    es hier als Datei auf der Platte. ``redact_sensitive_text`` ist dieselbe
    Funktion, die denselben Text auf dem Produktivweg passiert —
    ``ai_context_service`` für Nachrichten, ``ai_action_service`` für
    Rückfragen; eine zweite, eigene Schwärzung wäre eine zweite, eigene Lücke.
    ``redact_freetext`` wäre die falsche: sie ist für Text gedacht, der von
    aussen in den Server kam, und würde hier die Bind-Adresse mitnehmen, an der
    gerade das Sichtbarkeitsszenario hängt.

    Geschwärzt wird **vor** dem Kürzen: ein Auszug, der mitten in einem Passwort
    endet, würde sonst an keinem Muster mehr hängenbleiben.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    zeitpunkt = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_datei = LOG_DIR / f"autonomie_{ergebnis.szenario}_{zeitpunkt}.json"
    ergebnis_dict = {
        "szenario": ergebnis.szenario,
        "model": BENCH_MODEL,
        "label": BENCH_LABEL,
        "dauer_sekunden": round(ergebnis.dauer, 3),
        "ttft": round(ergebnis.ttft, 3),
        "werkzeuge": ergebnis.werkzeuge,
        "vorschlaege": ergebnis.vorschlaege,
        "fragen": [redact_sensitive_text(frage) for frage in ergebnis.fragen],
        "antwort_laenge": len(ergebnis.antwort_text),
        "antwort_auszug": redact_sensitive_text(ergebnis.antwort_text)[:1000],
    }
    log_datei.write_text(json.dumps(ergebnis_dict, indent=2, ensure_ascii=False), encoding="utf-8")


async def _szenario_messen(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    szenario: str,
    auftrag: str,
) -> AutonomieErgebnis:
    """Führt einen Auftrag einmal vollständig aus und zählt mit, was dabei geschah.

    Beide Szenarien messen auf demselben Weg; sie unterscheiden sich im Auftrag
    und in dem, was sie danach behaupten. Der gemeinsame Teil steht deshalb hier
    und nicht zweimal darunter — sonst gilt jede Änderung an der Messung nur für
    das Szenario, an das man gerade gedacht hat.
    """
    user, provider, _server = _setup_test_environment(db)
    conv = _prepare_conversation(db, user)
    ai_run_broker.zuruecksetzen_fuer_tests()

    ergebnis = AutonomieErgebnis(szenario=szenario)

    async with httpx.AsyncClient(timeout=BENCH_TIMEOUT) as client:
        denken, stufe = await ai_reasoning.vorgabe(
            client, db, user=user, provider=provider, aktiv=True, wunsch=None,
        )
        fenster = await ai_context_window.ermitteln(client, provider)
        run, fehler = ai_stream_service.lauf_beginnen(
            db,
            user=user,
            conversation=conv,
            provider=provider,
            request_id=uuid4(),
            content=auftrag,
            reasoning=denken,
            reasoning_effort=stufe,
            context_chars=fenster.zeichen if fenster.bekannt else None,
        )
        assert run is not None, f"Lauf konnte nicht begonnen werden: {fehler}"
        run_id = run.id
        ai_run_broker.eroeffnen(run_id)

        t0 = perf_counter()
        antwort_chunks: list[str] = []

        echt_read_tool = ai_stream_service.execute_read_tool

        def _read_tool(*args, **kwargs):
            name = str(kwargs.get("tool_name") or "?")
            ergebnis.werkzeuge.append(name)
            return echt_read_tool(*args, **kwargs)

        echt_write = ai_stream_service._persist_write_proposals

        def _write(*args, **kwargs):
            aufrufe = kwargs.get("tool_calls") or []
            namen = [getattr(c, "name", "?") for c in aufrufe] or ["propose_*"]
            for n in namen:
                ergebnis.vorschlaege.append(n)
            return echt_write(*args, **kwargs)

        echt_pub = ai_run_broker.veroeffentlichen

        def _pub(rid: str, ev: str, data: dict):
            if rid == run_id:
                if ev == "delta":
                    if not ergebnis.ttft:
                        ergebnis.ttft = perf_counter() - t0
                    antwort_chunks.append(str(data.get("content") or ""))
                elif ev == "question":
                    ergebnis.fragen.append(str(data.get("question") or ""))
            return echt_pub(rid, ev, data)

        monkeypatch.setattr(ai_stream_service, "execute_read_tool", _read_tool)
        monkeypatch.setattr(ai_stream_service, "_persist_write_proposals", _write)
        monkeypatch.setattr(ai_run_broker, "veroeffentlichen", _pub)
        monkeypatch.setattr(ai_stream_service.ai_run_broker, "veroeffentlichen", _pub)

        try:
            await asyncio.wait_for(
                ai_stream_service.segment_ausfuehren(
                    run_id,
                    client=client,
                ),
                timeout=BENCH_TIMEOUT,
            )
        finally:
            # Nur das Protokoll. Eine Zusicherung an dieser Stelle würde auch dann
            # laufen, wenn der Lauf gerade an der Zeitgrenze zerbrochen ist, und
            # dabei den echten Fehler durch ihren eigenen ersetzen.
            ergebnis.dauer = perf_counter() - t0
            ergebnis.antwort_text = "".join(antwort_chunks)
            _protokoll_schreiben(ergebnis)

    # Nur Kennzahlen. Antworttext und Rückfragen stehen geschwärzt im Protokoll;
    # auf der Konsole eines CI-Laufs haben sie nichts zu suchen.
    print(
        f"\n[BENCHMARK] {szenario}: Dauer={ergebnis.dauer:.2f}s, TTFT={ergebnis.ttft:.2f}s, "
        f"Werkzeuge={len(ergebnis.werkzeuge)}, Vorschlaege={len(ergebnis.vorschlaege)}, "
        f"Rueckfragen={len(ergebnis.fragen)}, Antwortlaenge={len(ergebnis.antwort_text)}"
    )
    return ergebnis


@pytest.mark.asyncio
async def test_benchmark_asa_visibility_troubleshooting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark: ASA Server taucht nicht in der Serverliste auf."""
    db = SessionLocal()
    try:
        ergebnis = await _szenario_messen(
            db,
            monkeypatch,
            szenario="asa_visibility_troubleshooting",
            auftrag=AUFTRAG_ASA_SICHTBARKEIT,
        )

        assert ergebnis.antwort_text.strip(), (
            "Der Lauf lief durch, aber der Benutzer hätte nichts zu lesen bekommen"
        )
        assert (
            "check_server_reachability" in ergebnis.werkzeuge
            or "read_server_network" in ergebnis.werkzeuge
        ), f"KI sollte Erreichbarkeit oder Netzwerk prüfen: {ergebnis.werkzeuge}"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_benchmark_gameplay_config_and_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark: Casual Gameplay & Passwort-Konfiguration."""
    db = SessionLocal()
    try:
        ergebnis = await _szenario_messen(
            db,
            monkeypatch,
            szenario="gameplay_config_and_password",
            auftrag=AUFTRAG_GAMEPLAY,
        )

        assert ergebnis.werkzeuge, (
            "Kein einziger Werkzeugaufruf: die KI hat die Konfiguration nicht gesucht, "
            "sondern über sie geredet"
        )
        assert ergebnis.vorschlaege, (
            "Das Szenario misst das Setzen von Werten. Ohne einen einzigen "
            f"Änderungsvorschlag ist nichts gesetzt worden: {ergebnis.werkzeuge}"
        )
        # Der Anlass des Szenarios ist die dreifache Rückfrageschleife: drei Runden
        # "welchen Multiplikator genau?", bevor überhaupt etwas passiert. Gezählt
        # wird deshalb, nicht zitiert — der Fragetext ist roher Modelltext und kann
        # das genannte Passwort enthalten, und eine fehlgeschlagene Zusicherung
        # steht im CI-Protokoll.
        assert len(ergebnis.fragen) < 3, (
            f"Rückfrageschleife statt Autonomie: {len(ergebnis.fragen)} Rückfragen, "
            f"Vorschläge: {ergebnis.vorschlaege}"
        )
    finally:
        db.close()
