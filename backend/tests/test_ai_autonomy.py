"""Autonomer Modus: was er entfernt und was er ausdruecklich nicht entfernt.

Zielpunkt 3.7. Autonomie ersetzt genau einen Schritt — die Bestaetigung durch
einen Menschen. Die Rechtepruefung, die Aktivpruefung des Benutzers, der
Server-Mutex und das Audit bleiben unveraendert. Diese Tests halten beide
Richtungen fest: dass er wirkt, und dass er nichts anderes aufhebt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiAutonomyGrant,
    AiConversation,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_action_errors, ai_autonomy_service, ai_proposal_service, ai_tool_registry
from services.ai_action_errors import AiActionValidationError
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _setup(
    db: Session, user: User, *, global_keys: tuple[str, ...], server_keys: tuple[str, ...]
) -> tuple[Server, AiConversation]:
    role = Role(name=f"autonomy-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in global_keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])

    server = Server(
        name="Autonomy Server",
        game_type="dayz",
        install_dir="/tmp/autonomy-server",
        container_name="msm-autonomy",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Autonomie"
    )
    db.add(conversation)
    db.commit()
    return server, conversation


def _backup_arguments(server_id: int) -> dict:
    return {
        "server_id": server_id,
        "reason": "Vor der Aenderung absichern.",
        "expected_effect": "Ein wiederherstellbarer Stand liegt vor.",
    }


def _propose(
    db: Session, user: User, conversation: AiConversation, server: Server
) -> AiActionProposal:
    proposal = ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments=_backup_arguments(server.id),
        correlation_id=str(uuid4()),
    )
    db.commit()
    return proposal


def test_without_a_grant_every_proposal_stays_confirmable(
    db: Session, regular_user: User
) -> None:
    """Der Standardmodus bleibt der unterstuetzte — auch mit der Berechtigung."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False
    assert proposal.requires_confirmation is True


def test_without_the_permission_a_grant_alone_does_nothing(
    db: Session, regular_user: User
) -> None:
    """Eine Freigabe kann keine fehlende Berechtigung ersetzen."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use",),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False
    assert proposal.requires_confirmation is True


def test_with_permission_and_grant_the_proposal_is_autonomous(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is True
    assert proposal.requires_confirmation is False


def test_a_disabled_grant_does_not_count(db: Session, regular_user: User) -> None:
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=False,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False


def test_a_server_grant_wins_over_the_panel_wide_one(
    db: Session, regular_user: User
) -> None:
    """Die spezifischere Angabe entscheidet — hier gegen die Autonomie."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=False,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False


def test_the_hourly_budget_falls_back_to_confirmation(
    db: Session, regular_user: User
) -> None:
    """Ist das Budget erschoepft, scheitert nichts — es wird nur wieder gefragt."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=1, granted_by=regular_user.id,
    )
    db.commit()

    first = _propose(db, regular_user, conversation, server)
    second = _propose(db, regular_user, conversation, server)

    assert first.autonomous is True
    assert second.autonomous is False, "Das Stundenbudget muss greifen"
    assert second.requires_confirmation is True


def test_actions_older_than_an_hour_free_the_budget_again(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    del server
    now = datetime.now(timezone.utc)
    db.add(AiActionProposal(
        id=str(uuid4()),
        # Die echte Unterhaltung, nicht eine erfundene Kennung: seit die Tests
        # Fremdschluessel pruefen, faellt eine Zeile ins Leere sofort auf.
        conversation_id=conversation.id,
        user_id=regular_user.id,
        server_id=None,
        tool_name="propose_backup",
        payload_encrypted="x",
        preview_json="{}",
        autonomous=True,
        correlation_id=str(uuid4()),
        created_at=now - timedelta(hours=2),
    ))
    db.commit()

    assert ai_autonomy_service.hourly_usage(db, user_id=regular_user.id) == 0


def test_always_confirm_tools_are_never_autonomous(
    db: Session, regular_user: User
) -> None:
    """Die Sperrliste gilt unabhaengig von jeder Freigabe."""
    server, _ = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    for tool in sorted(ai_tool_registry.ALWAYS_CONFIRM_TOOLS):
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=server.id, tool_name=tool
        ), f"{tool} darf niemals autonom laufen"


def test_a_reversible_change_runs_under_a_grant(
    db: Session, regular_user: User
) -> None:
    """Die Gegenprobe zur Sperrliste — sonst waere sie durch Ausweitung erfuellbar.

    Ein Test, der nur prueft "diese Werkzeuge laufen nicht autonom", bliebe auch
    dann gruen, wenn **gar nichts** mehr autonom liefe. Deshalb hier ein
    umkehrbarer Vorgang, der durchlaufen muss: eine falsche Bind-IP macht den
    Server unerreichbar, bis jemand sie zurueckstellt — und das kann die KI
    selbst. Kein Datenverlust, also keine Sperre.
    """
    server, _ = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.network.manage"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    assert ai_autonomy_service.autonomy_allows(
        db, user=regular_user, server_id=server.id,
        tool_name="propose_bind_ip_update",
    )


def test_autonomous_execution_still_rechecks_the_permission(
    db: Session, regular_user: User
) -> None:
    """Autonomie entfernt die Bestaetigung, nicht die Rechtepruefung."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()
    proposal = _propose(db, regular_user, conversation, server)

    db.query(ServerPermission).filter(
        ServerPermission.user_id == regular_user.id,
        ServerPermission.permission_key == "server.backups.create",
    ).delete()
    db.commit()

    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_ACCESS_REVOKED"


def test_a_revoked_autonomy_permission_stops_the_execution(
    db: Session, regular_user: User
) -> None:
    """Autonomie darf ihren eigenen Widerruf nicht überleben.

    Zwischen dem Anlegen des Vorschlags und seiner Ausführung liegt ein
    Zeitfenster ohne Obergrenze — ein Vorschlag im Status 'proposed' altert
    nicht. Nimmt der Betreiber in dieser Zeit `ai.autonomous.use` weg, muss das
    sofort wirken; die Werkzeug- und Serverrechte bleiben hier ausdrücklich
    stehen, sonst prüfte der Test nur die schon vorhandene Rechteschranke.
    """
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()
    proposal = _propose(db, regular_user, conversation, server)
    assert proposal.autonomous is True

    db.query(RolePermission).filter(
        RolePermission.permission_key == "ai.autonomous.use"
    ).delete()
    db.commit()

    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_NOT_AUTONOMOUS"
    db.refresh(proposal)
    assert proposal.status == "proposed", "Fail-closed: der Vorschlag bleibt liegen"


def test_a_grant_switched_off_after_the_proposal_stops_the_execution(
    db: Session, regular_user: User
) -> None:
    """Dieselbe Zusage von der anderen Seite: die Freigabe selbst wird abgeschaltet."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()
    proposal = _propose(db, regular_user, conversation, server)
    assert proposal.autonomous is True

    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=False,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_NOT_AUTONOMOUS"


def test_the_last_action_of_an_hour_still_executes(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Nachprüfung fragt die Grundlage, nicht das Budget.

    Der Vorschlag zählt selbst schon in `hourly_usage` mit. Würde vor der
    Ausführung noch einmal das volle `autonomy_allows` gefragt, verweigerte ein
    Budget von eins genau die eine Aktion, für die es erteilt wurde — die
    Autonomie wäre bei jeder Einstellung um eins zu klein.
    """
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=1, granted_by=regular_user.id,
    )
    db.commit()
    proposal = _propose(db, regular_user, conversation, server)
    assert proposal.autonomous is True
    # Das Budget ist jetzt aufgebraucht — durch diesen Vorschlag selbst.
    assert not ai_autonomy_service.autonomy_allows(
        db, user=regular_user, server_id=server.id, tool_name="propose_backup"
    )
    assert ai_autonomy_service.autonomie_grundlage(
        db, user=regular_user, server_id=server.id, tool_name="propose_backup"
    ) is not None

    # Nur die Ausführung selbst wird ersetzt: geprüft wird der Weg dorthin,
    # nicht das Anlegen eines echten Backups.
    monkeypatch.setattr(
        ai_proposal_service,
        "execute_proposal",
        lambda db_, **kwargs: (proposal, {"ok": True}),
    )

    _, ergebnis = ai_proposal_service.execute_autonomously(
        db, proposal_id=proposal.id, user=regular_user
    )
    assert ergebnis == {"ok": True}


def test_a_confirmable_proposal_can_not_be_executed_autonomously(
    db: Session, regular_user: User
) -> None:
    """Sonst waere der autonome Pfad eine Umgehung der Bestaetigungspflicht."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    proposal = _propose(db, regular_user, conversation, server)

    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_NOT_AUTONOMOUS"


# ── Router ────────────────────────────────────────────────────────────────


def test_grant_endpoints_require_the_permission(client, user_cookies, user_csrf_token) -> None:
    listed = client.get("/api/ai/autonomy", cookies=user_cookies)
    written = client.put(
        "/api/ai/autonomy",
        json={"server_id": None, "enabled": True, "max_actions_per_hour": 5},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert listed.status_code == 403
    assert written.status_code == 403


def test_a_grant_for_a_foreign_server_is_not_possible(
    client, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str
) -> None:
    """Ein Grant auf einen unsichtbaren Server wuerde dessen Existenz verraten."""
    role = Role(name="autonomy-router", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.autonomous.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])
    foreign = Server(
        name="Fremd", game_type="dayz", install_dir="/tmp/fremd", status="stopped"
    )
    db.add(foreign)
    db.commit()

    response = client.put(
        "/api/ai/autonomy",
        json={"server_id": foreign.id, "enabled": True, "max_actions_per_hour": 5},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 404
    assert db.query(AiAutonomyGrant).count() == 0


def test_grant_roundtrip_reports_the_used_budget(
    client, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    role = Role(name="autonomy-owner", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.autonomous.use"))
    db.commit()
    set_user_roles(db, owner_user, [role.id])

    created = client.put(
        "/api/ai/autonomy",
        json={"server_id": None, "enabled": True, "max_actions_per_hour": 7},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    listed = client.get("/api/ai/autonomy", cookies=owner_cookies)
    removed = client.delete(
        "/api/ai/autonomy", cookies=owner_cookies, headers=_csrf(owner_cookies)
    )
    empty = client.get("/api/ai/autonomy", cookies=owner_cookies)

    assert created.status_code == 200
    assert created.json()["max_actions_per_hour"] == 7
    assert created.json()["used_last_hour"] == 0
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert removed.status_code == 204
    assert empty.json() == []


def test_a_delete_stays_confirmable_even_with_every_grant_in_place(
    db: Session, regular_user: User
) -> None:
    """Loeschen ist nicht rueckgaengig zu machen — ausdrueckliche Vorgabe.

    Die Sperre wird hier nicht an der Menge geprueft, sondern am Ergebnis: ein
    Benutzer mit **allem**, was Autonomie sonst ausloest — `ai.autonomous.use`,
    eine aktive Freigabe fuer diesen Server, freies Stundenbudget und das
    globale `servers.delete` — bekommt trotzdem einen Vorschlag, der auf einen
    Menschen wartet.

    Anlass: der Betreiber bat die KI, einen Server zu stoppen und zu loeschen.
    Gestoppt hat sie ihn autonom; loeschen konnte sie ihn nicht, weil es das
    Werkzeug nicht gab. Jetzt gibt es das Werkzeug — und den Riegel dazu.
    """
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use", "servers.delete"),
        server_keys=("server.view",),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_delete",
        arguments={
            "server_id": server.id,
            "reason": "Der Benutzer will den Server entfernen.",
            "expected_effect": "Server, Dateien und Backups sind weg.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert proposal.autonomous is False, "Ein Loeschvorschlag darf nie autonom laufen"
    assert proposal.requires_confirmation is True
    # Und der Server steht noch.
    assert db.query(Server).filter(Server.id == server.id).first() is not None


def test_a_delete_needs_the_global_permission_not_just_server_access(
    db: Session, regular_user: User
) -> None:
    """`servers.delete` ist bewusst global und nicht delegierbar.

    Wer einen Server nur sehen darf, darf ihn nicht ueber die KI loeschen — auch
    dann nicht, wenn er auf diesem Server sonst alles darf. Ohne diese
    Unterscheidung waere `propose_server_delete` ein Weg, das ausdrueckliche
    "nicht delegierbar" aus dem Rechtekatalog zu umgehen.
    """
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use",),
        server_keys=(
            "server.view", "server.start", "server.stop", "server.restart",
            "server.files.write", "server.backups.create",
        ),
    )

    with pytest.raises(AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_delete",
            arguments={
                "server_id": server.id,
                "reason": "Aufraeumen.",
                "expected_effect": "Weg.",
            },
            correlation_id=str(uuid4()),
        )
    assert db.query(Server).filter(Server.id == server.id).first() is not None


def test_a_delete_still_needs_to_see_the_server(
    db: Session, regular_user: User
) -> None:
    """Zwei Huerden, nicht eine.

    Das globale Loeschrecht allein reicht nicht: `_resolve_server` verlangt
    vorher `server.view`. Sonst waere eine geratene Server-ID ein Weg, die
    Existenz fremder Server zu bestaetigen — und im schlimmsten Fall einen zu
    treffen.
    """
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "servers.delete"),
        server_keys=(),  # kein server.view
    )

    with pytest.raises(AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_delete",
            arguments={
                "server_id": server.id,
                "reason": "Aufraeumen.",
                "expected_effect": "Weg.",
            },
            correlation_id=str(uuid4()),
        )
    assert db.query(Server).filter(Server.id == server.id).first() is not None


def test_a_restore_stays_confirmable_but_creating_a_backup_does_not(
    db: Session, regular_user: User
) -> None:
    """Der Unterschied zwischen Anlegen und Einspielen, als Testfall.

    Ein zusaetzliches Backup schadet nie — das darf autonom laufen. Ein
    eingespieltes Backup ueberschreibt alles, was seither entstanden ist, und
    holt niemand zurueck. Beide haengen am selben Bereich `server.backups.*`,
    und ohne diese Unterscheidung wuerde eine Freigabe fuer das eine das andere
    mitfreigeben.
    """
    from models import Backup

    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create", "server.backups.restore"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    backup = Backup(server_id=server.id, filename="/tmp/x.tar.gz", size_mb=1)
    db.add(backup)
    db.commit()
    db.refresh(backup)

    anlegen = _propose(db, regular_user, conversation, server)
    einspielen = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_backup_restore",
        arguments={
            "server_id": server.id,
            "backup_id": backup.id,
            "reason": "Alter Stand gewuenscht.",
            "expected_effect": "Daten wie im Backup.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert anlegen.autonomous is True, "Ein Backup anzulegen darf autonom laufen"
    assert anlegen.requires_confirmation is False
    assert einspielen.autonomous is False, "Ein Restore darf nie autonom laufen"
    assert einspielen.requires_confirmation is True
