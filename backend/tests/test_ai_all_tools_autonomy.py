"""Tests fuer die universelle Durchsetzung des Autonomie-Modus ueber alle Werkzeugtypen.

Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / „Schutz braucht Vertrauen“.
- Autonomie AUS: Alle Werkzeuge (Lesewerkzeuge, worker_start, Schreibwerkzeuge)
  verlangen eine Bestaetigungskarte.
- Autonomie AN: Autonome Ausfuehrung ohne Bestaetigung (ausser ALWAYS_CONFIRM_TOOLS).
- Ablehnung: POST /api/ai/actions/{id}/reject setzt den Status auf 'expired' mit
  'AI_ACTION_REJECTED' und weckt den Lauf.
- Aufgaben & Guardian: Hintergrundoperationen setzen zwingend eine aktive Autonomie-Freigabe voraus.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiAutonomyGrant,
    AiConversation,
    AiRun,
    AiTask,
    AiToolResult,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import (
    ai_autonomy_service,
    ai_proposal_service,
    ai_stream_service,
    ai_task_service,
    ai_tool_registry,
)
from services.role_service import set_user_roles


def _setup_user_and_server(
    db: Session,
    user: User,
    *,
    global_keys: tuple[str, ...] = ("ai.chat.use", "ai.autonomous.use", "ai.background.use", "ai.tasks.manage"),
    server_keys: tuple[str, ...] = ("server.view", "server.backup.create", "server.files.read", "server.files.write"),
) -> tuple[Server, AiConversation]:
    role = Role(name=f"auto-all-{user.id}-{uuid4().hex[:6]}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in global_keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])

    server = Server(
        name="Auto Test Server",
        game_type="dayz",
        install_dir="/tmp/auto-test-server",
        container_name="msm-auto-test",
        status="online",
        cpu_limit_percent=200,
        ram_limit_mb=4096,
        disk_limit_gb=50,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Autonomy All Tools"
    )
    db.add(conversation)
    db.commit()
    return server, conversation


def test_create_and_execute_read_tool_proposal(db: Session, regular_user: User):
    """Prueft, dass Lesewerkzeuge wie read_server_status ueber Proposals laufen koennen."""
    server, conv = _setup_user_and_server(db, regular_user)

    # Autonomie ist AUS (kein Grant vorhanden)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conv,
        tool_name="read_server_status",
        arguments={"server_id": server.id, "reason": "Status pruefen", "expected_effect": "Status erfahren"},
        correlation_id=str(uuid4()),
    )
    assert proposal.tool_name == "read_server_status"
    assert proposal.status == "proposed"
    assert proposal.requires_confirmation is True
    assert proposal.autonomous is False

    # Bestaetigen
    proposal, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=regular_user)
    assert proposal.status == "confirmed"

    # Ausfuehren
    executed_prop, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )
    assert executed_prop.status == "succeeded"
    assert result.get("status") == "online"
    assert result.get("game") == "dayz"


def test_create_and_execute_worker_start_proposal(db: Session, regular_user: User):
    """Prueft, dass worker_start bei deaktivierter Autonomie ein bestaetigungspflichtiges Proposal anlegt."""
    server, conv = _setup_user_and_server(db, regular_user)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conv,
        tool_name="worker_start",
        arguments={"auftrag": "Server untersuchen", "titel": "Diagnose", "reason": "Diagnose starten", "expected_effect": "Worker laeuft"},
        correlation_id=str(uuid4()),
    )
    assert proposal.tool_name == "worker_start"
    assert proposal.status == "proposed"
    assert proposal.requires_confirmation is True
    assert proposal.autonomous is False

    # Bestaetigen & Ausfuehren
    proposal, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=regular_user)
    executed_prop, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )
    assert executed_prop.status == "succeeded"
    assert "started" in result


def test_reject_proposal_via_service(db: Session, regular_user: User):
    """Prueft, dass reject_proposal den Vorschlag ablehnt und im Audit dokumentiert."""
    server, conv = _setup_user_and_server(db, regular_user)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conv,
        tool_name="read_server_status",
        arguments={"server_id": server.id, "reason": "Status pruefen", "expected_effect": "Status sehen"},
        correlation_id=str(uuid4()),
    )
    assert proposal.status == "proposed"

    rejected = ai_proposal_service.reject_proposal(db, proposal_id=proposal.id, user=regular_user)
    assert rejected.status == "expired"
    assert rejected.error_code == "AI_ACTION_REJECTED"


def test_reject_action_endpoint(client: TestClient, db: Session, regular_user: User, user_cookies: dict):
    """Prueft den HTTP POST /api/ai/actions/{id}/reject Endpunkt."""
    server, conv = _setup_user_and_server(db, regular_user)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conv,
        tool_name="read_server_status",
        arguments={"server_id": server.id, "reason": "Status pruefen", "expected_effect": "Status sehen"},
        correlation_id=str(uuid4()),
    )

    from tests.test_admin_router import _csrf
    res = client.post(
        f"/api/ai/actions/{proposal.id}/reject",
        headers=_csrf(user_cookies),
        cookies=user_cookies,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == proposal.id
    assert data["status"] == "expired"
    assert data["error_code"] == "AI_ACTION_REJECTED"


def test_vorschlag_ergebnisse_loads_tool_result(db: Session, regular_user: User):
    """Prueft, dass _vorschlag_ergebnisse bei Erfolg die Tool-Ergebnisse aus AiToolResult anhaengt."""
    server, conv = _setup_user_and_server(db, regular_user)

    run = AiRun(
        id=str(uuid4()),
        user_id=regular_user.id,
        conversation_id=conv.id,
        status="waiting_confirmation",
    )
    db.add(run)
    db.commit()

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conv,
        tool_name="read_server_status",
        arguments={"server_id": server.id, "reason": "Status pruefen", "expected_effect": "Status sehen"},
        correlation_id=str(uuid4()),
    )
    proposal.run_id = run.id
    db.commit()

    # Bestaetigen & Ausfuehren
    proposal, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=regular_user)
    executed_prop, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )

    ergebnisse = ai_stream_service._vorschlag_ergebnisse(db, [proposal.id])
    assert len(ergebnisse) == 1
    assert ergebnisse[0]["status"] == "succeeded"
    assert ergebnisse[0]["tool_name"] == "read_server_status"
    assert ergebnisse[0].get("result", {}).get("status") == "online"


def test_background_task_requires_autonomy(db: Session, regular_user: User, monkeypatch):
    """Prueft, dass Aufgaben im Hintergrund ohne Autonomie-Freigabe stillgelegt werden."""
    server, conv = _setup_user_and_server(db, regular_user)

    task = AiTask(
        id=str(uuid4()),
        user_id=regular_user.id,
        title="Check Server",
        instruction="Pruefe den Server status",
        kind="report",
        plan_kind="daily",
        time_of_day="08:00",
        time_zone="UTC",
        channel="chat",
        enabled=True,
    )
    db.add(task)
    db.commit()

    from services import ai_run_service
    monkeypatch.setattr(ai_run_service, "http_client", lambda: object())

    # Ohne Autonomie-Grant
    assert ai_task_service.darf_handeln(db, regular_user) is False

    import asyncio
    res = asyncio.run(ai_task_service.aufgabenlauf_starten(db, aufgabe=task))
    assert res is None
    db.refresh(task)
    assert task.enabled is False
    assert task.next_run_at is None


def test_cloudflare_dns_srv_record_payload():
    from services.ai_proposals.network_proposals import _cloudflare_dns_payload

    # SRV Record mit data/content
    payload, preview = _cloudflare_dns_payload({
        "zone_id": "zone123",
        "name": "_minecraft._tcp.mc.example.com",
        "rtype": "SRV",
        "content": "0 5 25566 mc.example.com",
        "proxied": True,  # should be auto-reset to False
    })
    assert payload["rtype"] == "SRV"
    assert payload["name"] == "_minecraft._tcp.mc.example.com"
    assert payload["content"] == "0 5 25566 mc.example.com"
    assert payload["proxied"] is False
    assert preview["operation"] == "cloudflare_dns_create"

    # MX Record mit Prioritaet
    payload_mx, _ = _cloudflare_dns_payload({
        "zone_id": "zone123",
        "name": "mail.example.com",
        "rtype": "MX",
        "content": "mailserver.example.com",
        "priority": 10,
    })
    assert payload_mx["rtype"] == "MX"
    assert payload_mx["priority"] == 10
    assert payload_mx["proxied"] is False

    # TXT Record
    payload_txt, _ = _cloudflare_dns_payload({
        "zone_id": "zone123",
        "name": "example.com",
        "rtype": "TXT",
        "content": "v=spf1 include:_spf.google.com ~all",
    })
    assert payload_txt["rtype"] == "TXT"
    assert payload_txt["content"] == "v=spf1 include:_spf.google.com ~all"

    # CAA Record mit data
    payload_caa, _ = _cloudflare_dns_payload({
        "zone_id": "zone123",
        "name": "example.com",
        "rtype": "CAA",
        "data": {"flags": 0, "tag": "issue", "value": "letsencrypt.org"},
    })
    assert payload_caa["rtype"] == "CAA"
    assert payload_caa["data"]["value"] == "letsencrypt.org"
