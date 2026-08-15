"""Live-Autonomie-Benchmark für den MSM KI-Agenten.

Misst echte End-to-End-Szenarien gegen das Luna-Modell (OpenRouter):
1. asa_visibility_troubleshooting: Server nicht in Serverliste -> Query-Probing, Blueprint-Analyse, Websuche, Erreichbarkeits-Check.
2. gameplay_config_and_password: Name, Passwort und Casual-Multiplikatoren setzen -> Autonomes Finden von Configs, Setzen von ServerPassword und Multiplikatoren ohne 3-fache Rückfrageschleife.
3. blueprint_experiment_and_cleanup: Test-Blueprint ableiten, testen und am Ende Testressourcen sauber aufräumen (propose_blueprint_delete).

Liest automatisch ~/.msm-bench.env falls Umgebungsvariablen nicht gesetzt sind.
Ergebnisse werden unter backend/logs/ai-benchmark/ abgelegt.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

# ── Auto-Load ~/.msm-bench.env wenn nötig ─────────────────────────────────
def _lade_bench_env() -> None:
    env_pfade = [
        Path.home() / ".msm-bench.env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for pfad in env_pfade:
        if pfad.is_file():
            try:
                for zeile in pfad.read_text(encoding="utf-8").splitlines():
                    zeile = zeile.strip()
                    if not zeile or zeile.startswith("#"):
                        continue
                    if zeile.startswith("export "):
                        zeile = zeile[7:].strip()
                    if "=" in zeile:
                        k, v = zeile.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass

_lade_bench_env()

from database import SessionLocal
from models import AiConversation, AiProvider, Server, ServerPort, User
from services import (
    ai_action_service,
    ai_chat_service,
    ai_context_service,
    ai_context_window,
    ai_memory_service,
    ai_reasoning,
    ai_run_broker,
    ai_skill_service,
    ai_stream_service,
)
from services.ai_provider_service import _operator_aad

BENCH_KEY = os.environ.get("MSM_BENCH_AI_KEY", "").strip()
BENCH_MODEL = os.environ.get("MSM_BENCH_AI_MODEL", "openai/gpt-5.6-luna").strip()
BENCH_BASE_URL = os.environ.get("MSM_BENCH_AI_BASE_URL", "https://openrouter.ai/api/v1").strip()
BENCH_LABEL = os.environ.get("MSM_BENCH_LABEL", "autonomie-benchmark").strip()
BENCH_TIMEOUT = float(os.environ.get("MSM_BENCH_TIMEOUT", "180"))

NUR_MIT_SCHLUESSEL = pytest.mark.skipif(
    not BENCH_KEY,
    reason="MSM_BENCH_AI_KEY nicht gesetzt — Benchmark übersprungen",
)

LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "ai-benchmark"


@dataclass
class AutonomieErgebnis:
    szenario: str
    dauer_gesamt: float = 0.0
    ttft: float = 0.0
    runden_anzahl: int = 0
    write_runden_anzahl: int = 0
    werkzeuge_aufgerufen: list[str] = field(default_factory=list)
    fragen_gestellt: list[str] = field(default_factory=list)
    vorschlaege_erzeugt: list[str] = field(default_factory=list)
    antwort_text: str = ""
    erfolg: bool = False
    details: dict[str, Any] = field(default_factory=dict)


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


@pytest.mark.asyncio
@NUR_MIT_SCHLUESSEL
async def test_benchmark_asa_visibility_troubleshooting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark: ASA Server taucht nicht in der Serverliste auf."""
    db = SessionLocal()
    try:
        user, provider, server = _setup_test_environment(db)
        conv = _prepare_conversation(db, user)
        ai_run_broker.zuruecksetzen_fuer_tests()

        auftrag = "Ok der asa server taucht nicht in der server liste auf da scheint was nicht zu stimmen"

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
            ttft = 0.0
            antwort_chunks = []
            werkzeuge = []
            vorschlaege = []
            fragen = []

            echt_read_tool = ai_stream_service.execute_read_tool

            def _read_tool(*args, **kwargs):
                name = str(kwargs.get("tool_name") or "?")
                werkzeuge.append(name)
                return echt_read_tool(*args, **kwargs)

            echt_write = ai_stream_service._persist_write_proposals

            def _write(*args, **kwargs):
                aufrufe = kwargs.get("tool_calls") or []
                namen = [getattr(c, "name", "?") for c in aufrufe] or ["propose_*"]
                for n in namen:
                    vorschlaege.append(n)
                return echt_write(*args, **kwargs)

            echt_pub = ai_run_broker.veroeffentlichen

            def _pub(rid: str, ev: str, data: dict):
                nonlocal ttft
                if rid == run_id:
                    if ev == "delta":
                        if not ttft:
                            ttft = perf_counter() - t0
                        antwort_chunks.append(str(data.get("content") or ""))
                    elif ev == "question":
                        fragen.append(str(data.get("question") or ""))
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
                dauer = perf_counter() - t0
                antwort_text = "".join(antwort_chunks)

                LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_datei = LOG_DIR / f"autonomie_asa_visibility_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
                ergebnis_dict = {
                    "szenario": "asa_visibility_troubleshooting",
                    "model": BENCH_MODEL,
                    "label": BENCH_LABEL,
                    "dauer_sekunden": round(dauer, 3),
                    "ttft": round(ttft, 3),
                    "werkzeuge": werkzeuge,
                    "vorschlaege": vorschlaege,
                    "fragen": fragen,
                    "antwort_laenge": len(antwort_text),
                    "antwort_auszug": antwort_text[:1000],
                }
                log_datei.write_text(json.dumps(ergebnis_dict, indent=2, ensure_ascii=False), encoding="utf-8")

                print(f"\n[BENCHMARK] ASA Visibility: Dauer={dauer:.2f}s, TTFT={ttft:.2f}s, Tools={werkzeuge}, Proposals={vorschlaege}")
                assert "check_server_reachability" in werkzeuge or "read_server_network" in werkzeuge, (
                    f"KI sollte Erreichbarkeit oder Netzwerk prüfen: {werkzeuge}"
                )
    finally:
        db.close()


@pytest.mark.asyncio
@NUR_MIT_SCHLUESSEL
async def test_benchmark_gameplay_config_and_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark: Casual Gameplay & Passwort-Konfiguration."""
    db = SessionLocal()
    try:
        user, provider, server = _setup_test_environment(db)
        conv = _prepare_conversation(db, user)
        ai_run_broker.zuruecksetzen_fuer_tests()

        auftrag = (
            "Gut stelle den server Namen auf MauntARK passowrt auf Maik1234@ "
            "ernte und loot auf das doppelte machen und zähm geschwindikeit auf das 4 fache "
            "und alles sehr casual machen aber mit etwas herausfoderung"
        )

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
            ttft = 0.0
            antwort_chunks = []
            werkzeuge = []
            vorschlaege = []
            fragen = []

            echt_read_tool = ai_stream_service.execute_read_tool

            def _read_tool(*args, **kwargs):
                name = str(kwargs.get("tool_name") or "?")
                werkzeuge.append(name)
                return echt_read_tool(*args, **kwargs)

            echt_write = ai_stream_service._persist_write_proposals

            def _write(*args, **kwargs):
                aufrufe = kwargs.get("tool_calls") or []
                namen = [getattr(c, "name", "?") for c in aufrufe] or ["propose_*"]
                for n in namen:
                    vorschlaege.append(n)
                return echt_write(*args, **kwargs)

            echt_pub = ai_run_broker.veroeffentlichen

            def _pub(rid: str, ev: str, data: dict):
                nonlocal ttft
                if rid == run_id:
                    if ev == "delta":
                        if not ttft:
                            ttft = perf_counter() - t0
                        antwort_chunks.append(str(data.get("content") or ""))
                    elif ev == "question":
                        fragen.append(str(data.get("question") or ""))
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
                dauer = perf_counter() - t0
                antwort_text = "".join(antwort_chunks)

                LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_datei = LOG_DIR / f"autonomie_gameplay_config_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
                ergebnis_dict = {
                    "szenario": "gameplay_config_and_password",
                    "model": BENCH_MODEL,
                    "label": BENCH_LABEL,
                    "dauer_sekunden": round(dauer, 3),
                    "ttft": round(ttft, 3),
                    "werkzeuge": werkzeuge,
                    "vorschlaege": vorschlaege,
                    "fragen": fragen,
                    "antwort_laenge": len(antwort_text),
                    "antwort_auszug": antwort_text[:1000],
                }
                log_datei.write_text(json.dumps(ergebnis_dict, indent=2, ensure_ascii=False), encoding="utf-8")

                print(f"\n[BENCHMARK] Gameplay Config: Dauer={dauer:.2f}s, TTFT={ttft:.2f}s, Tools={werkzeuge}, Proposals={vorschlaege}")
    finally:
        db.close()
