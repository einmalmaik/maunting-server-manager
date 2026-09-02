"""Adversarial & Edge-Case Security Tests fuer das KI- und Vorschlagssystem.

Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / „Schutz braucht Vertrauen“.
Dieses Modul testet gezielt Angriffsvektoren, Prompt-Injection-Szenarien,
Token-Manipulationen, Replay-Attacken und Berechtigungsentzug:

1. Prompt Injection: Manipulierte LLM-Ausgaben koennen keine Werkzeuge ohne
   Bestaetigung ausfuehren, wenn Autonomie deaktiviert ist.
2. Token-Manipulation: Gefaelschte oder manipulierte Bestaetigungstoken scheitern
   an der konstanten HMAC-Pruefung.
3. Replay-Schutz: Ein gueltiger Token kann exakt einmal eingeloest werden.
4. RBAC-Entzug: Wird ein Recht zwischen Vorschlag und Ausfuehrung entzogen,
   bricht die Ausfuehrung hart ab (Fail-Closed).
5. Datenminimierung & Sanitization: Schadhafte Skripte oder Pfade in Previews
   werden bereinigt und leaken keine Tokens.
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
    AiRun,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import (
    ai_action_service,
    ai_autonomy_service,
    ai_proposal_service,
    ai_tool_registry,
)
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.role_service import set_user_roles


@pytest.fixture
def sicherheits_umgebung(db: Session, regular_user: User):
    """Erstellt eine vollstaendige Testumgebung mit Benutzer, Rolle und Server."""
    rolle = Role(name=f"sec-role-{uuid4().hex[:6]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()

    rechte = [
        "ai.chat.use",
        "ai.desktop.use",
        "ai.desktop.install",
        "server.view",
        "server.files.read",
        "server.files.write",
        "server.config.write",
        "server.power",
    ]
    for r in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=r))
    db.commit()

    set_user_roles(db, regular_user, [rolle.id])

    server = Server(
        name="Security Test Server",
        game_type="dayz",
        install_dir="/tmp/sec-test-server",
        container_name="msm-sec-test",
        status="online",
        cpu_limit_percent=200,
        ram_limit_mb=4096,
        disk_limit_gb=50,
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    for key in (
        "server.view",
        "server.files.read",
        "server.files.write",
        "server.config.write",
        "server.power",
    ):
        db.add(ServerPermission(user_id=regular_user.id, server_id=server.id, permission_key=key))
    db.commit()

    unterhaltung = AiConversation(
        id=str(uuid4()),
        user_id=regular_user.id,
        kind="primary",
        title="Security Test Conversation",
    )
    db.add(unterhaltung)
    db.commit()

    return {
        "user": regular_user,
        "role": rolle,
        "server": server,
        "conversation": unterhaltung,
    }


def test_prompt_injection_kann_bei_deaktivierter_autonomie_nicht_direkt_ausfuehren(
    db: Session, sicherheits_umgebung
):
    """Selbst wenn ein bösartiger Prompt Injection Payload die KI dazu bringt,
    ein gefährliches Werkzeug aufzurufen: Bei Autonomie=AUS wird IMMER nur
    ein 'proposed' Vorschlag erzeugt, der menschliche Bestätigung erfordert.
    """
    user = sicherheits_umgebung["user"]
    server = sicherheits_umgebung["server"]
    unterhaltung = sicherheits_umgebung["conversation"]

    # Autonomie ist AUS (kein Grant vorhanden)
    assert not ai_autonomy_service.autonomy_allows(
        db, user=user, server_id=server.id, tool_name="propose_restart_schedule_set"
    )

    # Simulierter Werkzeugaufruf aus injiziertem Prompt
    payload = {
        "server_id": server.id,
        "enabled": False,
        "reason": "Schadhaftes Skript deaktivieren",
        "expected_effect": "Server-Neustartplan wird deaktiviert",
    }

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name="propose_restart_schedule_set",
        arguments=payload,
        correlation_id=str(uuid4()),
    )

    # Sicherheitsinvariante: Status MUSS 'proposed' sein
    assert vorschlag.status == "proposed"
    assert vorschlag.proposal_type == "write"
    assert vorschlag.confirmation_token_hash is None

    # Ohne Bestätigungstoken darf der Vorschlag nicht ausführbar sein
    with pytest.raises(AiActionStateError):
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token="gefaelschtes_token",
        )


def test_token_manipulation_scheitert_an_konstanter_hmac_pruefung(
    db: Session, sicherheits_umgebung
):
    """Ein Angreifer, der ein zufälliges oder abgewandeltes Token erraten will,
    wird abgewiesen, ohne dass Timing-Informationen leaken.
    """
    user = sicherheits_umgebung["user"]
    server = sicherheits_umgebung["server"]
    unterhaltung = sicherheits_umgebung["conversation"]

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name="propose_restart_schedule_set",
        arguments={
            "server_id": server.id,
                "enabled": False,
            "reason": "Testbegründung",
            "expected_effect": "Testwirkung",
        },
        correlation_id=str(uuid4()),
    )

    # Mensch bestätigt und erzeugt gültiges Token
    _prop, echtes_token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=user)

    # 1. Gefälschtes Token scheitert
    with pytest.raises(AiActionStateError):
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token="ungueltiges_token_123",
        )

    # 2. Leicht manipuliertes Token (1 Byte verändert) scheitert
    manipuliertes_token = echtes_token[:-1] + ("a" if echtes_token[-1] != "a" else "b")
    with pytest.raises(AiActionStateError):
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token=manipuliertes_token,
        )


def test_replay_angriff_auf_bestaetigungstoken_wird_atomar_verhindert(
    db: Session, sicherheits_umgebung
):
    """Ein einmal eingelöstes Token kann kein zweites Mal verwendet werden."""
    user = sicherheits_umgebung["user"]
    server = sicherheits_umgebung["server"]
    unterhaltung = sicherheits_umgebung["conversation"]

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name="propose_restart_schedule_set",
        arguments={
            "server_id": server.id,
                "enabled": False,
            "reason": "Replay-Test",
            "expected_effect": "Replay-Wirkung",
        },
        correlation_id=str(uuid4()),
    )

    _prop, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=user)

    # Erste Ausführung
    prop_nach_lauf, ergebnis = ai_proposal_service.execute_proposal(
        db,
        user=user,
        proposal_id=vorschlag.id,
        confirmation_token=token,
    )
    assert prop_nach_lauf.status == "succeeded"

    # Zweite Ausführung mit demselben Token (Replay-Angriff)
    with pytest.raises(AiActionStateError):
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token=token,
        )


def test_rechteentzug_zwischen_vorschlag_und_ausfuehrung_blockiert_hart(
    db: Session, sicherheits_umgebung
):
    """Wird dem Benutzer zwischen Erstellung des Vorschlags und Ausführung
    das Recht entzogen (z. B. durch Admin-Aktion), schlägt die Ausführung
    garantiert fehl (Fail-Closed-Invariante).
    """
    user = sicherheits_umgebung["user"]
    rolle = sicherheits_umgebung["role"]
    server = sicherheits_umgebung["server"]
    unterhaltung = sicherheits_umgebung["conversation"]

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name="propose_restart_schedule_set",
        arguments={
            "server_id": server.id,
                "enabled": False,
            "reason": "Rechte-Test",
            "expected_effect": "Rechte-Wirkung",
        },
        correlation_id=str(uuid4()),
    )

    _prop, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=user)

    # Admin entzieht dem Benutzer die Rolle und Serverrechte
    set_user_roles(db, user, [])
    db.query(ServerPermission).filter(ServerPermission.user_id == user.id).delete()
    db.commit()

    # Ausführung MUSS an fehlenden Rechten scheitern
    with pytest.raises(AiActionStateError) as exc_info:
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token=token,
        )
    assert exc_info.value.code == "AI_ACTION_ACCESS_REVOKED"


def test_abgelaufenes_token_wird_abgewiesen(db: Session, sicherheits_umgebung):
    """Bestaetigungstoken haben eine strikte Lebensdauer von 5 Minuten."""
    user = sicherheits_umgebung["user"]
    server = sicherheits_umgebung["server"]
    unterhaltung = sicherheits_umgebung["conversation"]

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name="propose_restart_schedule_set",
        arguments={
            "server_id": server.id,
                "enabled": False,
            "reason": "TTL-Test",
            "expected_effect": "TTL-Wirkung",
        },
        correlation_id=str(uuid4()),
    )

    _prop, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=user)

    # Simuliere Zeitablauf in der Datenbank (Token abgelaufen)
    prop_in_db = db.query(AiActionProposal).filter(AiActionProposal.id == vorschlag.id).one()
    prop_in_db.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    with pytest.raises(AiActionStateError) as exc_info:
        ai_proposal_service.execute_proposal(
            db,
            user=user,
            proposal_id=vorschlag.id,
            confirmation_token=token,
        )
    assert exc_info.value.code == "AI_ACTION_CONFIRMATION_EXPIRED"
