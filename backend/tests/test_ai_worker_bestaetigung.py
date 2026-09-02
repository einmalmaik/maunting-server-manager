"""Ein Auftrag wartet auf den Knopf — er gibt nicht auf.

Betriebsvorfall vom 22.08.2026: der Betreiber bat im Chat, eine Mod zu
aktivieren und den ARK-Server neu zu starten. Der Auftrag legte den Vorschlag
an, und danach passierte nichts Sichtbares; im Chat stand ein Satz darueber,
dass etwas zu bestaetigen waere („Soll MauntARK trotzdem jetzt neu gestartet
werden?"). Einen Knopf gab es nirgends.

Die Ursache lag im Schreibrunden-Zweig fuer unbeaufsichtigte Laeufe. Ein
Worker zaehlt als unbeaufsichtigt — ihm fehlt `ask_user`, und niemand liest
seinen Verlauf mit —, und der Zweig ist fuer Heilungen und faellige Aufgaben
geschrieben: erst eine Freigabe per E-Mail, und ohne eingerichteten
Versandweg wird der Vorschlag zurueckgenommen und der Lauf beendet.

Fuer eine Heilung ist das richtig; um drei Uhr nachts sitzt niemand davor.
Fuer einen Worker ist es falsch: **jemand hat ihn gerade beauftragt** und
sitzt vor dem Chat. Die Unterscheidung heisst deshalb nicht "unbeaufsichtigt",
sondern "niemand da".

Die zweite Haelfte — dass die Karte auch im Dauerchat auftaucht und nicht nur
in der Worker-Ansicht — steht in `test_ai_worker_endpunkte.py`.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiProvider,
    AiRun,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_run_broker, ai_run_service, ai_stream_service, ai_worker_service
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk, StreamUsage
from services.role_service import set_user_roles


#: Der Lauf prueft nur, **dass** ein Client da ist — gestreamt wird gefaelscht.
_KEIN_CLIENT = object()


def _benutzer(db: Session, name: str) -> User:
    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    role = Role(name=f"wb-{name}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in ("ai.chat.use", "ai.background.use"):
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()
    return user


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name=f"Zugang-{uuid4().hex[:6]}",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _server(db: Session, user: User, name: str) -> Server:
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
    for key in ("server.view", "server.restart"):
        db.add(ServerPermission(
            user_id=user.id, server_id=server.id, permission_key=key
        ))
    db.commit()
    return server


def _fake_stream(monkeypatch: pytest.MonkeyPatch, aufruf: ProviderToolCall):
    """Ein Modell, das genau einmal einen Neustart vorschlaegt."""
    zaehler = {"runde": 0}

    async def fake(
        _client, *, provider, api_key, messages, usage: StreamUsage,
        tools=None, tool_choice=None, reasoning=False, reasoning_effort=None,
        cache_marke=False, model=None,
    ):
        del provider, api_key, messages, reasoning, reasoning_effort, cache_marke
        if tool_choice == "none":
            usage.total_tokens = 10
            yield StreamChunk("content", "ok")
            return
        if zaehler["runde"] == 0:
            usage.tool_calls = [aufruf]
        zaehler["runde"] += 1
        usage.total_tokens = 10
        yield StreamChunk("content", "Ich lege den Neustart vor.")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)


async def _worker_lauf_abarbeiten(db: Session, user: User) -> AiRun:
    with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
        ergebnis = ai_worker_service.worker_start(
            db, user=user,
            arguments={"auftrag": "Starte MauntARK neu", "titel": "Neustart"},
        )
    assert ergebnis["started"] is True
    run = (
        db.query(AiRun)
        .filter(AiRun.conversation_id == ergebnis["worker_id"])
        .one()
    )
    ai_run_broker.eroeffnen(run.id)
    ai_run_broker.abonnieren(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=_KEIN_CLIENT)
    db.expire_all()
    return db.get(AiRun, run.id)


@pytest.mark.asyncio
async def test_ein_worker_parkt_auf_die_bestaetigung(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf endet nicht, er wartet — und die Karte bleibt offen.

    Ohne diese Unterscheidung nahm `_vorschlaege_zuruecknehmen` die Karte mit
    dem Grund `guardian_unattended` zurueck und der Lauf endete. Der Mensch
    sah dann nur noch, dass die KI davon erzaehlt.
    """
    user = _benutzer(db, "wartender")
    _provider(db)
    server = _server(db, user, "mauntark")
    _fake_stream(monkeypatch, ProviderToolCall(
        id="a1",
        name="propose_server_lifecycle",
        arguments={
            "server_id": server.id,
            "operation": "restart",
            "reason": "Der Benutzer hat den Neustart verlangt.",
            "expected_effect": "Der Server startet neu und laedt die Mods.",
        },
    ))

    run = await _worker_lauf_abarbeiten(db, user)

    assert run.status == "waiting_confirmation"
    karte = db.query(AiActionProposal).filter(
        AiActionProposal.run_id == run.id
    ).one()
    assert karte.status == "proposed"
    assert karte.requires_confirmation is True


@pytest.mark.asyncio
async def test_der_geparkte_auftrag_merkt_sich_worauf_er_wartet(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Vorschlagskennung steht im Laufzustand.

    Daran haengt der Weckweg: der Klick des Menschen weckt genau diesen Lauf,
    und ohne die Kennung wuesste er nach dem Aufwachen nicht, was inzwischen
    entschieden wurde.
    """
    user = _benutzer(db, "gemerkt")
    _provider(db)
    server = _server(db, user, "zweitark")
    _fake_stream(monkeypatch, ProviderToolCall(
        id="a2",
        name="propose_server_lifecycle",
        arguments={
            "server_id": server.id,
            "operation": "restart",
            "reason": "Der Benutzer hat den Neustart verlangt.",
            "expected_effect": "Der Server startet neu.",
        },
    ))

    run = await _worker_lauf_abarbeiten(db, user)

    karte = db.query(AiActionProposal).filter(
        AiActionProposal.run_id == run.id
    ).one()
    zustand = ai_run_service.zustand_lesen(run)
    assert zustand.get("pending", {}).get("proposal_ids") == [karte.id]


@pytest.mark.asyncio
async def test_der_geparkte_auftrag_sagt_bescheid(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Worker, der wartet, muss sich hoerbar machen.

    Solange er zurueckgenommen hat und **endete**, sprach die Meldestelle
    wenigstens sein Ergebnis an. Seit er stattdessen parkt, ist
    `waiting_confirmation` kein Endzustand — und damit passierte gar nichts
    mehr. Wer per Stimme arbeitet oder den Chat zugeklappt hat, wartete auf
    eine Antwort, die nie kam.

    Die Meldung nennt den Ort bewusst: eine gesprochene Zusage bestaetigt nur
    Vorschlaege des laufenden Gespraechs, nicht die eines fremden Fensters.
    """
    from models import AiMeldung

    user = _benutzer(db, "melder")
    _provider(db)
    server = _server(db, user, "drittark")
    _fake_stream(monkeypatch, ProviderToolCall(
        id="a3",
        name="propose_server_lifecycle",
        arguments={
            "server_id": server.id,
            "operation": "restart",
            "reason": "Der Benutzer hat den Neustart verlangt.",
            "expected_effect": "Der Server startet neu.",
        },
    ))

    run = await _worker_lauf_abarbeiten(db, user)

    assert run.status == "waiting_confirmation"
    meldung = db.query(AiMeldung).filter(AiMeldung.user_id == user.id).one()
    assert "Freigabe" in meldung.text
    assert "Neustart" in meldung.text
    assert meldung.worker_id == run.conversation_id
