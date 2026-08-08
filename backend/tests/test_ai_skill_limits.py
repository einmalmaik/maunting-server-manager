"""Skill-Laeufe muessen denselben Kontingenten unterliegen wie ein Chat.

Vorher rief der Router `run_skill` ohne jede Reservierung auf. Ein Benutzer mit
`ai.skills.use` konnte damit `read_server_logs` und `read_config` beliebig oft
ausloesen — an `requests_per_minute`, `concurrent_operations` und jedem
Kostenlimit vorbei. Zielpunkt 6 verlangt Kontingente fuer KI-Vorgaenge, und ein
Skill-Lauf ist einer.
"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiUsageEvent, Role, RolePermission, User
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _role_with_limits(db: Session, user: User, limits: dict) -> Role:
    role = Role(name=f"skill-limits-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in ("ai.skills.use", "ai.skills.manage"):
        db.add(RolePermission(role_id=role.id, permission_key=key))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS} | limits)
    db.commit()
    set_user_roles(db, user, [role.id])
    return role


def _skill(client: TestClient, cookies: dict, key: str) -> str:
    created = client.post(
        "/api/ai/skills",
        json={
            "skill_key": key,
            "name": "Statuslauf",
            "description": "Liest nur den Status.",
            "steps": [{"tool_name": "read_server_status", "arguments": {}}],
            "enabled": True,
        },
        cookies=cookies,
        headers=_csrf(cookies),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _conversation(db: Session, user: User, server_id: int) -> int:
    """Legt die eine Unterhaltung an und liefert die Server-ID des Laufs zurueck.

    Ein Skill-Lauf braucht seit dem Einzelchat keine Unterhaltungs-ID mehr —
    der Serverbezug steht im Aufruf. Die Unterhaltung wird trotzdem angelegt,
    damit der Lauf denselben Zustand vorfindet wie im Panel.
    """
    db.add(AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Skill-Limit"
    ))
    db.commit()
    return server_id


def test_skill_run_is_blocked_once_requests_per_minute_is_exhausted(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
) -> None:
    _role_with_limits(db, owner_user, {"requests_per_minute": 1})
    skill_id = _skill(client, owner_cookies, "rate-limited")
    server_id = _conversation(db, owner_user, test_server.id)

    first = client.post(
        f"/api/ai/skills/{skill_id}/run",
        json={"server_id": server_id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    second = client.post(
        f"/api/ai/skills/{skill_id}/run",
        json={"server_id": server_id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "AI_QUOTA_REQUESTS_PER_MINUTE"


def test_a_completed_skill_run_leaves_an_accounted_usage_event(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
) -> None:
    """Der Verbrauch wird abgeschlossen, nicht dauerhaft reserviert.

    Eine haengende Reservierung wuerde einen Nebenlaeufigkeitsplatz des
    Benutzers bis zum Prozessneustart blockieren.
    """
    _role_with_limits(db, owner_user, {})
    skill_id = _skill(client, owner_cookies, "accounted")
    server_id = _conversation(db, owner_user, test_server.id)

    response = client.post(
        f"/api/ai/skills/{skill_id}/run",
        json={"server_id": server_id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200
    event = db.query(AiUsageEvent).one()
    assert event.status == "completed"
    assert event.server_id == test_server.id
    # Ein Skill ruft keinen Provider auf — es waere falsch, Tokens zu erfinden.
    assert event.accounted_tokens == 0
    assert event.accounted_cost_microunits == 0


def test_a_rejected_skill_run_does_not_hold_a_reservation(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Scheitert ein Schritt an einem fehlenden Recht, muss der Platz zurueck."""
    from models import Server, ServerPermission

    created = client.post(
        "/api/ai/skills",
        json={
            "skill_key": "needs-backup",
            "name": "Backup",
            "description": "Erzeugt einen Backup-Vorschlag.",
            "steps": [{"tool_name": "propose_backup", "arguments": {}}],
            "enabled": True,
        },
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert created.status_code == 201

    _role_with_limits(db, regular_user, {})
    server = Server(
        name="Skill Limit Server",
        game_type="dayz",
        install_dir="/tmp/skill-limit-server",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    ))
    db.commit()
    server_id = _conversation(db, regular_user, server.id)

    response = client.post(
        f"/api/ai/skills/{created.json()['id']}/run",
        json={"server_id": server_id},
        cookies=user_cookies, headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 403
    reserved = (
        db.query(AiUsageEvent).filter(AiUsageEvent.status == "reserved").count()
    )
    assert reserved == 0, "Eine haengende Reservierung blockiert dauerhaft Kontingent"


def test_skill_run_without_any_configured_limit_is_allowed(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
) -> None:
    """Ohne hinterlegtes Kontingent laeuft ein Skill — er wird nur gezaehlt.

    Frueher erwartete dieser Test hier ein 429 und beschrieb das als „sicheren
    Default 0“. Das war kein Default, sondern ein Totalausfall: auf einer
    frischen Installation hatte niemand eine `RoleAiLimit`-Zeile, also war jede
    KI-Aktion gesperrt — auch fuer Owner und Admin, und ohne erkennbaren Grund
    in der Oberflaeche. Wer nichts konfiguriert hat, hat keine Grenze gesetzt;
    die Zugangsgrenze ist das Recht, nicht ein unsichtbares Nulllimit.

    Der Verbrauch wird trotzdem verbucht, damit eine spaeter gesetzte Grenze
    sofort greift.
    """
    skill_id = _skill(client, owner_cookies, "no-budget")
    server_id = _conversation(db, owner_user, test_server.id)

    response = client.post(
        f"/api/ai/skills/{skill_id}/run",
        json={"server_id": server_id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200, response.text
    assert db.query(AiUsageEvent).count() == 1


def test_owner_has_no_bypass_for_an_explicitly_configured_zero_limit(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
) -> None:
    """Ein ausdruecklich auf 0 gesetztes Kontingent sperrt auch den Owner.

    Das ist die eigentliche Aussage des frueheren Tests und bleibt bestehen:
    Bei den KI-Limits gibt es keinen Owner-Bypass, denn die Kosten entstehen
    unabhaengig davon, wer die Anfrage ausloest. Unterschieden wird nur noch
    zwischen „nichts konfiguriert“ und „ausdruecklich auf 0 gesetzt“.
    """
    _role_with_limits(db, owner_user, {"requests_per_minute": 0})
    skill_id = _skill(client, owner_cookies, "zero-budget")
    server_id = _conversation(db, owner_user, test_server.id)

    response = client.post(
        f"/api/ai/skills/{skill_id}/run",
        json={"server_id": server_id},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "AI_QUOTA_REQUESTS_PER_MINUTE"
