"""Sicherheits- und Grenztests für rollenbasierte KI-Kontingente."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiUsageEvent, AuditLog, Role, RolePermission, User
from services.ai_limit_service import (
    LIMIT_FIELDS,
    resolve_effective_limits,
    set_role_limit,
)
from services.role_service import set_user_roles
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)


def _limits(**overrides: int | None) -> dict[str, int | None]:
    """Erzeugt ein vollständiges, standardmäßig gesperrtes Limit-Set."""
    values: dict[str, int | None] = {field: 0 for field in LIMIT_FIELDS}
    values.update(overrides)
    return values


def _role(db: Session, name: str) -> Role:
    """Legt eine isolierte Testrolle ohne implizite Rechte an."""
    role = Role(name=name, description=None, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_missing_configuration_resolves_to_unlimited(
    db: Session,
    regular_user: User,
) -> None:
    """Ohne jede Rollenkonfiguration gilt unbegrenzt, nicht gesperrt.

    Regression: frueher ergab die leere Zeilenmenge ueber ``max(default=0)``
    ein Limit von 0. Damit brach jede Anfrage auf einer frischen Installation
    mit ``ai.errors.quota`` ab, obwohl der Betreiber gar keine Grenze gesetzt
    hatte. Die Zugangsgrenze ist ``ai.chat.use``, nicht ein stilles Nulllimit.
    """
    role = _role(db, "ai-unconfigured")
    set_user_roles(db, regular_user, [role.id])

    effective = resolve_effective_limits(db, regular_user)

    assert all(getattr(effective, field) is None for field in LIMIT_FIELDS)
    # Und die Reservierung laeuft dann auch wirklich durch.
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=5_000,
    )
    assert event.status == "reserved"


def test_unconfigured_role_does_not_lift_a_configured_one(
    db: Session,
    regular_user: User,
) -> None:
    """Eine zusaetzliche Rolle ohne Konfiguration hebt kein Limit auf.

    Nur der voellig unkonfigurierte Fall bedeutet „unbegrenzt“. Sobald *eine*
    Rolle Werte traegt, tragen die uebrigen nichts bei — sonst waere jede neu
    angelegte Rolle ein stiller Freibrief.
    """
    limited = _role(db, "ai-limited-role")
    blank = _role(db, "ai-blank-role")
    set_role_limit(db, limited.id, _limits(daily_token_limit=100))
    db.commit()
    set_user_roles(db, regular_user, [limited.id, blank.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit == 100
    assert effective.requests_per_minute == 0


def test_unconfigured_role_is_listed_as_unlimited_not_zero(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Die Einstellungsansicht zeigt „nicht konfiguriert“ als unbegrenzt.

    Vorher stand dort 0. Ein unbeabsichtigtes Speichern haette die Rolle damit
    hart gesperrt, obwohl der angezeigte Zustand nie gespeichert worden war.
    """
    role = _role(db, "ai-listed-unconfigured")

    listed = client.get("/api/ai/settings/role-limits", cookies=owner_cookies)

    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["configured"] is False
    assert all(row[field] is None for field in LIMIT_FIELDS)


def test_service_rejects_invalid_internal_limit_values(db: Session) -> None:
    """Auch interne Aufrufer können die API-Validierung nicht umgehen."""
    role = _role(db, "ai-invalid-internal")
    with pytest.raises(ValueError, match="daily_token_limit"):
        set_role_limit(db, role.id, _limits(daily_token_limit=True))
    with pytest.raises(ValueError, match="requests_per_minute"):
        set_role_limit(db, role.id, _limits(requests_per_minute=10_001))


def test_highest_limit_and_explicit_unlimited_win(
    db: Session,
    regular_user: User,
) -> None:
    """Mehrere Rollen addieren Verbrauch nicht; sie wählen genau eine Grenze."""
    standard = _role(db, "ai-standard")
    vip = _role(db, "ai-vip-limits")
    set_role_limit(
        db,
        standard.id,
        _limits(daily_token_limit=1_000, requests_per_minute=5),
    )
    set_role_limit(
        db,
        vip.id,
        _limits(daily_token_limit=10_000, requests_per_minute=None),
    )
    db.commit()
    set_user_roles(db, regular_user, [standard.id, vip.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit == 10_000
    assert effective.requests_per_minute is None
    assert effective.weekly_token_limit == 0


def test_owner_updates_limits_atomically_and_audited(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Settings-Write speichert ein Vollset und erzeugt korrelierbares Audit."""
    role = _role(db, "ai-api-role")
    payload = _limits(
        daily_token_limit=25_000,
        weekly_token_limit=100_000,
        monthly_token_limit=None,
        requests_per_minute=20,
        concurrent_operations=2,
        monthly_cost_limit_cents=5_000,
    )

    response = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=payload,
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200
    assert response.json()["monthly_token_limit"] is None
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "ai.role_limits.updated")
        .one()
    )
    assert audit.origin == "direct"
    assert str(UUID(audit.correlation_id or "")) == audit.correlation_id
    assert "25000" not in (audit.details or "")


def test_update_rejects_partial_negative_and_extreme_payloads(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Fehlende, negative und übergroße Werte scheitern vor der DB-Mutation."""
    role = _role(db, "ai-invalid-limits")
    cases = [
        {"daily_token_limit": 1},
        _limits(daily_token_limit=-1),
        _limits(requests_per_minute=10_001),
    ]

    for payload in cases:
        response = client.put(
            f"/api/ai/settings/role-limits/{role.id}",
            json=payload,
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert response.status_code == 422

    listed = client.get(
        "/api/ai/settings/role-limits",
        cookies=owner_cookies,
    )
    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["configured"] is False


def test_role_limit_settings_require_backend_permissions_and_csrf(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    owner_cookies: dict,
) -> None:
    """Frontend-Sichtbarkeit kann weder RBAC noch CSRF ersetzen."""
    role = _role(db, "ai-rbac-limits")
    payload = _limits(daily_token_limit=100)

    forbidden = client.get(
        "/api/ai/settings/role-limits",
        cookies=user_cookies,
    )
    no_csrf = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=payload,
        cookies=owner_cookies,
    )

    assert forbidden.status_code == 403
    assert no_csrf.status_code == 403


def test_effective_limits_endpoint_returns_union_rule(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Der Client erhält nur das bereits backendseitig aufgelöste Ergebnis."""
    role = _role(db, "ai-own-limits")
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_role_limit(db, role.id, _limits(daily_token_limit=321))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    response = client.get("/api/ai/limits/me", cookies=user_cookies)

    assert response.status_code == 200
    assert response.json()["daily_token_limit"] == 321
    assert response.json()["role_ids"] == [role.id]


def _enable_usage(db: Session, user: User, role: Role, **overrides: int | None) -> None:
    """Gibt einem Testuser eine vollständig nutzbare, gezielt überschreibbare Quote."""
    values = _limits(
        daily_token_limit=10_000,
        weekly_token_limit=10_000,
        monthly_token_limit=10_000,
        requests_per_minute=10,
        concurrent_operations=2,
        monthly_cost_limit_cents=100,
    )
    values.update(overrides)
    set_role_limit(
        db,
        role.id,
        values,
    )
    db.commit()
    set_user_roles(db, user, [role.id])


def test_usage_reservation_counts_retry_exactly_once(
    db: Session,
    regular_user: User,
) -> None:
    """Dieselbe Request-ID liefert dieselbe Zeile und verbraucht keine zweite Quote."""
    role = _role(db, "ai-idempotent-usage")
    _enable_usage(db, regular_user, role)
    request_id = uuid4()

    first = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )
    db.commit()
    second = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )

    assert second.id == first.id
    assert db.query(AiUsageEvent).count() == 1

    complete_ai_usage(
        db,
        first,
        actual_tokens=450,
        actual_cost_microunits=9_000,
    )
    db.commit()
    completed_retry = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )
    assert completed_retry.id == first.id
    assert completed_retry.accounted_tokens == 450


def test_usage_reservation_enforces_tokens_rpm_and_concurrency(
    db: Session,
    regular_user: User,
) -> None:
    """Alle synchron prüfbaren Limits greifen vor einem späteren Provider-Aufruf."""
    role = _role(db, "ai-enforced-usage")
    _enable_usage(
        db,
        regular_user,
        role,
        daily_token_limit=100,
        weekly_token_limit=100,
        monthly_token_limit=100,
        requests_per_minute=1,
        concurrent_operations=1,
    )
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=80,
        now=now,
    )
    db.commit()

    with pytest.raises(AiQuotaExceeded, match="requests_per_minute"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=1,
            now=now,
        )

    complete_ai_usage(db, event, actual_tokens=80, actual_cost_microunits=0, now=now)
    db.commit()
    with pytest.raises(AiQuotaExceeded, match="daily_token_limit"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=21,
            now=now + timedelta(minutes=2),
        )


def test_failed_usage_frees_reservation_and_conflicting_retry_is_rejected(
    db: Session,
    regular_user: User,
) -> None:
    """Fehler erfinden keinen Verbrauch; UUID-Reuse mit anderer Payload bleibt verboten."""
    role = _role(db, "ai-failed-usage")
    _enable_usage(db, regular_user, role, concurrent_operations=1)
    request_id = uuid4()
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=50,
    )
    with pytest.raises(AiQuotaExceeded, match="concurrent_operations"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=1,
        )
    fail_ai_usage(db, event)
    db.commit()

    assert event.status == "failed"
    assert event.accounted_tokens == 0
    replacement = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=1,
    )
    assert replacement.status == "reserved"
    with pytest.raises(AiUsageConflict):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=request_id,
            estimated_tokens=51,
        )
