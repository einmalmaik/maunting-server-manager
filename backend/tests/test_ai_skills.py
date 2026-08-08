"""Allowlist-, Versionierungs- und Bestaetigungsgrenzen fuer AI-Skills."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, AiSkill, AuditLog, Role, RolePermission, User
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _grant_ai_budget(db: Session, user: User) -> None:
    """Gibt dem Benutzer ausdruecklich ein unbegrenztes KI-Kontingent.

    Skill-Laeufe reservieren seit Phase 2 gegen dieselben Rollenkontingente wie
    ein Chat — sonst liefen sie an `requests_per_minute` und
    `concurrent_operations` vorbei.

    Die Freigabe steht hier ausdruecklich, obwohl eine voellig unkonfigurierte
    Rolle inzwischen ohnehin unbegrenzt bedeutet: diese Tests sollen die Skills
    pruefen, nicht die Limitaufloesung. Sie duerfen sich deshalb nicht still auf
    deren Defaultverhalten stuetzen. Dass es bei den KI-Limits keinen
    Owner-Bypass gibt, prueft `test_ai_skill_limits.py`.
    """
    role = Role(name=f"ai-budget-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.skills.use"))
    db.add(RolePermission(role_id=role.id, permission_key="ai.skills.manage"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def test_skill_versions_are_immutable_and_latest_is_visible(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    first_payload = {
        "skill_key": "safe-backup",
        "name": "Sicheres Backup",
        "description": "Erstellt einen bestaetigungspflichtigen Backup-Vorschlag.",
        "steps": [{"tool_name": "propose_backup", "arguments": {}}],
        "enabled": True,
    }
    first = client.post(
        "/api/ai/skills", json=first_payload, cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    second = client.put(
        "/api/ai/skills/safe-backup",
        json={**first_payload, "description": "Version zwei."},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    listed = client.get("/api/ai/skills/manage", cookies=owner_cookies)

    assert first.status_code == 201 and first.json()["version"] == 1
    assert second.status_code == 200 and second.json()["version"] == 2
    assert listed.status_code == 200
    assert [(row["skill_key"], row["version"]) for row in listed.json()] == [
        ("safe-backup", 2)
    ]
    assert db.query(AiSkill).count() == 2


def test_skill_rejects_unregistered_or_secret_bearing_steps(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    base = {
        "skill_key": "unsafe",
        "name": "Unsafe",
        "description": "Muss abgelehnt werden.",
        "enabled": True,
    }
    shell = client.post(
        "/api/ai/skills",
        json={**base, "steps": [{"tool_name": "execute_shell", "arguments": {}}]},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    secret = client.post(
        "/api/ai/skills",
        json={
            **base,
            "steps": [{
                "tool_name": "read_server_logs",
                "arguments": {"lines": 50, "api_key": "never-store"},
            }],
        },
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert shell.status_code == 422
    assert secret.status_code == 422
    assert db.query(AiSkill).count() == 0


def test_running_skill_creates_proposal_and_never_executes_it(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
) -> None:
    _grant_ai_budget(db, owner_user)
    conversation = AiConversation(
        id=str(uuid4()), user_id=owner_user.id, server_id=test_server.id,
        title="Skill Run",
    )
    db.add(conversation)
    db.commit()
    created = client.post(
        "/api/ai/skills",
        json={
            "skill_key": "restart-check",
            "name": "Restart Check",
            "description": "Liest Status und schlaegt einen Neustart vor.",
            "steps": [
                {"tool_name": "read_server_status", "arguments": {}},
                {"tool_name": "propose_server_lifecycle", "arguments": {"operation": "restart"}},
            ],
            "enabled": True,
        },
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    ).json()

    run = client.post(
        f"/api/ai/skills/{created['id']}/run",
        json={"conversation_id": conversation.id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert run.status_code == 200
    assert run.json()["read_results"][0]["result"]["status"] == "stopped"
    assert len(run.json()["proposals"]) == 1
    proposal = db.query(AiActionProposal).one()
    assert proposal.status == "proposed"
    assert db.query(AuditLog).filter(AuditLog.action == "ai.action.executed").count() == 0


def test_old_skill_version_cannot_run_after_latest_is_disabled(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
) -> None:
    conversation = AiConversation(
        id=str(uuid4()), user_id=owner_user.id, server_id=None, title="Version guard"
    )
    db.add(conversation)
    db.commit()
    payload = {
        "skill_key": "version-guard",
        "name": "Version guard",
        "description": "Only the latest version can run.",
        "steps": [{"tool_name": "read_server_status", "arguments": {}}],
        "enabled": True,
    }
    created = client.post(
        "/api/ai/skills", json=payload, cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert created.status_code == 201
    payload["enabled"] = False
    updated = client.put(
        "/api/ai/skills/version-guard", json=payload, cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert updated.status_code == 200

    response = client.post(
        f"/api/ai/skills/{created.json()['id']}/run",
        json={"conversation_id": conversation.id},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 409


def test_skill_run_without_step_permission_returns_403_not_500(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Ein Skill-Schritt ohne Recht ist ein Berechtigungsfall, kein Serverfehler.

    Die Schrittpruefung wirft AiActionValidationError (ein ValueError). Ohne
    Umsetzung in eine HTTPException haette FastAPI daraus einen 500 gemacht und
    dem Benutzer waere unklar geblieben, dass ihm schlicht ein Recht fehlt.
    """
    from models import Role, RolePermission, Server, ServerPermission
    from services.role_service import set_user_roles

    created = client.post(
        "/api/ai/skills",
        json={
            "skill_key": "needs-backup-right",
            "name": "Backup-Skill",
            "description": "Erzeugt einen Backup-Vorschlag.",
            "steps": [{"tool_name": "propose_backup", "arguments": {}}],
            "enabled": True,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert created.status_code == 201

    role = Role(name=f"skill-runner-{regular_user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.skills.use"))
    # Ohne Kontingent scheiterte der Lauf schon an der Reservierung (429) und
    # der eigentlich gepruefte Rechtefall waere gar nicht erreicht worden.
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    set_user_roles(db, regular_user, [role.id])
    server = Server(
        name="Skill RBAC Server",
        game_type="dayz",
        install_dir="/tmp/skill-rbac-server",
        status="stopped",
    )
    db.add(server)
    db.commit()
    # Nur Sichtrecht, bewusst KEIN server.backups.create.
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    ))
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=server.id, title="Skill"
    )
    db.add(conversation)
    db.commit()

    response = client.post(
        f"/api/ai/skills/{created.json()['id']}/run",
        json={"conversation_id": conversation.id},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 403
    assert db.query(AiActionProposal).count() == 0
