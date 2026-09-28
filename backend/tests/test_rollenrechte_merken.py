"""Rollenrechte werden je Sitzung kurz gemerkt, ein Entzug wirkt trotzdem.

Bis 27.09.2026 kostete jede Rechtepruefung eines Nicht-Owners dieselben zwei
Abfragen, ein KI-Kontext fragte fuenf Rechte nacheinander ab. Gemerkt werden
darf aber nur, solange sich nichts geaendert haben kann: jedes Schreiben,
jeder Commit, jedes Rollback und zwei Sekunden Alter werfen es weg.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

import database as db_module
from models import Role, RolePermission, User, UserRole
from services import permission_service
from services.permission_service import has_global_permission


@pytest.fixture
def rolle(db: Session, regular_user: User) -> Role:
    rolle = Role(name="merkrolle")
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    db.add(UserRole(user_id=regular_user.id, role_id=rolle.id))
    db.commit()
    return rolle


def _rollenabfragen(db: Session) -> list[str]:
    abfragen: list[str] = []

    def mit(_c, _cur, stmt, *_a, **_k):
        if "FROM user_roles" in stmt or "FROM role_permissions" in stmt:
            abfragen.append(stmt)

    event.listen(db.get_bind(), "before_cursor_execute", mit)
    return abfragen


def test_mehrere_pruefungen_lesen_die_rollen_einmal(db: Session, regular_user: User, rolle: Role) -> None:
    abfragen = _rollenabfragen(db)

    assert has_global_permission(db, regular_user, "ai.chat.use")
    assert not has_global_permission(db, regular_user, "ai.memory.use")
    assert has_global_permission(db, regular_user, "ai.chat.use")

    assert len(abfragen) == 2


def test_entzug_in_derselben_sitzung_wirkt_sofort(db: Session, regular_user: User, rolle: Role) -> None:
    assert has_global_permission(db, regular_user, "ai.chat.use")

    db.query(UserRole).filter(UserRole.user_id == regular_user.id).delete()
    db.flush()

    assert not has_global_permission(db, regular_user, "ai.chat.use")


def test_rollback_nimmt_ein_vorlaeufiges_recht_wieder_weg(db: Session, regular_user: User) -> None:
    rolle = Role(name="vorlaeufig")
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    db.add(UserRole(user_id=regular_user.id, role_id=rolle.id))
    db.flush()
    assert has_global_permission(db, regular_user, "ai.chat.use")

    db.rollback()

    assert not has_global_permission(db, regular_user, "ai.chat.use")


def test_entzug_aus_einer_anderen_sitzung_wirkt_nach_dem_commit(
    db: Session, regular_user: User, rolle: Role
) -> None:
    assert has_global_permission(db, regular_user, "ai.chat.use")
    with db_module.SessionLocal() as andere:
        andere.query(UserRole).filter(UserRole.user_id == regular_user.id).delete()
        andere.commit()

    db.commit()

    assert not has_global_permission(db, regular_user, "ai.chat.use")


def test_entzug_aus_einer_anderen_sitzung_wirkt_spaetestens_nach_der_frist(
    db: Session, regular_user: User, rolle: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Eine Sitzung, die lange nichts schreibt (ein KI-Lauf), darf ein
    # entzogenes Recht nicht beliebig lange behalten.
    uhr = [1000.0]
    monkeypatch.setattr(permission_service.time, "monotonic", lambda: uhr[0])
    assert has_global_permission(db, regular_user, "ai.chat.use")
    with db_module.SessionLocal() as andere:
        andere.query(UserRole).filter(UserRole.user_id == regular_user.id).delete()
        andere.commit()

    uhr[0] += 2.5

    assert not has_global_permission(db, regular_user, "ai.chat.use")


def test_merken_gilt_nur_fuer_denselben_benutzer(
    db: Session, regular_user: User, owner_user: User, rolle: Role
) -> None:
    anderer = User(username="ohnerolle", email="ohnerolle@test.de", password_hash="x", email_verified=True)
    db.add(anderer)
    db.commit()

    assert has_global_permission(db, regular_user, "ai.chat.use")
    assert not has_global_permission(db, anderer, "ai.chat.use")
