"""Ein Team darf nie mehr weitergeben, als sein Gruender selbst haelt.

Das ist die einzige Stelle, an der MSM echte Macht ueber einen zweiten Weg
verteilt — bisher gab es nur globale Rollen und die Per-Server-Delegation, und
beide vergibt ausschliesslich, wer `users.permissions.manage` hat. Ein Team
darf dagegen **jeder** gruenden, der `teams.create` hat, also im Hoster-Betrieb
auch ein Kunde.

Ohne Obergrenze waere das eine Rechteausweitung in drei Zeilen: Team gruenden,
sich selbst eintragen, `server.console.exec` auf einem fremden Server
hinterlegen. Diese Datei prueft, dass genau das nicht geht — und zwar sowohl
beim Anlegen als auch bei jeder spaeteren Pruefung.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import (
    Role,
    RolePermission,
    Server,
    ServerPermission,
    Team,
    TeamMember,
    TeamServerGrant,
    User,
)
from services import permission_service, team_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _server(db: Session, name: str) -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{name}",
        status="stopped",
        container_name=f"msm-{name}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "TeamPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, server: Server, *keys: str) -> None:
    """Direkte Per-Server-Delegation — der uebliche Weg fuer einen Kunden."""
    for key in keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _grant_global(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"rolle-{user.username}", description="Test")
    db.add(role)
    db.commit()
    db.refresh(role)
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _team_with_member(db: Session, owner: User, member: User) -> Team:
    _grant_global(db, owner, "teams.create")
    team = team_service.create_team(db, user=owner, name=f"team-{owner.username}")
    team_service.add_member(
        db, team=team, user=owner, new_user_id=member.id,
        can_manage_skills=True, can_manage_memory=True,
    )
    return team


# ── Die Obergrenze beim Anlegen ───────────────────────────────────────


def test_founder_cannot_grant_a_permission_they_do_not_hold(
    db: Session, regular_user: User
) -> None:
    """Der Kern: was der Gruender nicht hat, kann er nicht weitergeben.

    Der Gruender darf den Server nur sehen. Er versucht, seinem Team zusaetzlich
    das Ausfuehren von Befehlen im Container zu geben — das maechtigste Recht,
    das MSM auf Serverebene kennt.
    """
    colleague = _user(db, "kollege")
    server = _server(db, "fremd")
    _allow(db, regular_user, server, "server.view")
    team = _team_with_member(db, regular_user, colleague)

    with pytest.raises(Exception) as exc:
        team_service.set_server_grants(
            db, team=team, user=regular_user, server_id=server.id,
            keys=["server.view", "server.console.exec"],
        )
    assert getattr(exc.value, "status_code", None) == 403
    # Und es darf auch nichts halb Angelegtes zurueckbleiben.
    assert team_service.team_server_keys(db, team.id, server.id) == []


def test_grants_require_server_view_as_their_base(
    db: Session, regular_user: User
) -> None:
    """Rechte ohne Sichtbarkeit waeren ein Zustand, den nichts darstellen kann."""
    colleague = _user(db, "kollege")
    server = _server(db, "server")
    _allow(db, regular_user, server, "server.view", "server.start")
    team = _team_with_member(db, regular_user, colleague)

    with pytest.raises(Exception) as exc:
        team_service.set_server_grants(
            db, team=team, user=regular_user, server_id=server.id,
            keys=["server.start"],
        )
    assert getattr(exc.value, "status_code", None) == 422


# ── Die Obergrenze zur Laufzeit ───────────────────────────────────────


def test_member_receives_exactly_the_granted_permissions(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.start", "server.stop")
    team = _team_with_member(db, regular_user, colleague)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )

    assert permission_service.has_server_permission(db, colleague, server.id, "server.view")
    assert permission_service.has_server_permission(db, colleague, server.id, "server.start")
    # Nicht weitergegeben heisst nicht erhalten — auch wenn der Gruender es haelt.
    assert not permission_service.has_server_permission(db, colleague, server.id, "server.stop")


def test_permission_evaporates_when_the_founder_loses_it(
    db: Session, regular_user: User
) -> None:
    """Selbstheilung: der Entzug beim Gruender wirkt sofort beim Mitglied.

    Waeren die Rechte beim Eintragen in `server_permissions` materialisiert
    worden, bliebe das Mitglied hier zurueck — mit einem Recht, dessen Grundlage
    verschwunden ist und das niemand mehr aufraeumt.
    """
    colleague = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.start")
    team = _team_with_member(db, regular_user, colleague)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )
    assert permission_service.has_server_permission(db, colleague, server.id, "server.start")

    db.query(ServerPermission).filter(
        ServerPermission.user_id == regular_user.id,
        ServerPermission.server_id == server.id,
        ServerPermission.permission_key == "server.start",
    ).delete()
    db.commit()

    assert not permission_service.has_server_permission(db, colleague, server.id, "server.start")
    # Die Zeile im Team bleibt bestehen — sie ist nur wirkungslos. Bekommt der
    # Gruender das Recht zurueck, wirkt sie wieder.
    assert "server.start" in team_service.team_server_keys(db, team.id, server.id)


def test_a_directly_written_grant_row_cannot_escalate(
    db: Session, regular_user: User
) -> None:
    """Auch wer die Pruefung im Dienst umgeht, gewinnt nichts.

    Simuliert den Fall, dass eine Zeile auf einem anderen Weg in die Tabelle
    kommt — durch einen kuenftigen Endpunkt, einen Fehler oder direkten
    Datenbankzugriff. Die Laufzeitpruefung ist die eigentliche Grenze; die
    Pruefung beim Anlegen ist nur die freundlichere Fehlermeldung.
    """
    colleague = _user(db, "kollege")
    server = _server(db, "fremd")
    _allow(db, regular_user, server, "server.view")
    team = _team_with_member(db, regular_user, colleague)

    db.add(TeamServerGrant(
        team_id=team.id, server_id=server.id,
        permission_key="server.console.exec", granted_by=regular_user.id,
    ))
    db.commit()

    assert not permission_service.has_server_permission(
        db, colleague, server.id, "server.console.exec"
    )


def test_teams_cannot_be_chained_to_launder_permissions(
    db: Session, regular_user: User
) -> None:
    """Ein Team kann nur reichen, was sein Gruender *direkt* haelt.

    Sonst waere folgende Kette moeglich: A gibt B ein Recht ueber Team 1, B
    gruendet Team 2 und gibt dasselbe Recht an C weiter. C haette dann ein
    Recht, das niemand ihm delegiert hat — und A koennte es nicht mehr entziehen,
    ohne von Team 2 zu wissen.
    """
    b = _user(db, "bene")
    c = _user(db, "cara")
    server = _server(db, "quelle")
    _allow(db, regular_user, server, "server.view", "server.start")

    team_one = _team_with_member(db, regular_user, b)
    team_service.set_server_grants(
        db, team=team_one, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )
    assert permission_service.has_server_permission(db, b, server.id, "server.start")

    # B versucht weiterzureichen, was er selbst nur geliehen hat.
    team_two = _team_with_member(db, b, c)
    with pytest.raises(Exception) as exc:
        team_service.set_server_grants(
            db, team=team_two, user=b, server_id=server.id,
            keys=["server.view", "server.start"],
        )
    assert getattr(exc.value, "status_code", None) == 403

    # Und auch eine an der Pruefung vorbei geschriebene Zeile traegt nicht.
    for key in ("server.view", "server.start"):
        db.add(TeamServerGrant(
            team_id=team_two.id, server_id=server.id, permission_key=key,
            granted_by=b.id,
        ))
    db.commit()
    assert not permission_service.has_server_permission(db, c, server.id, "server.start")


# ── Liste und Detail muessen dasselbe sagen ───────────────────────────


def test_list_and_detail_agree_for_team_members(
    db: Session, regular_user: User
) -> None:
    """Kein "sehe den Server nicht, darf ihn aber oeffnen" und nicht umgekehrt."""
    colleague = _user(db, "kollege")
    shared = _server(db, "geteilt")
    hidden = _server(db, "nicht-geteilt")
    _allow(db, regular_user, shared, "server.view")
    _allow(db, regular_user, hidden, "server.view")
    team = _team_with_member(db, regular_user, colleague)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=shared.id, keys=["server.view"],
    )

    visible = permission_service.list_visible_server_ids(db, colleague)
    assert visible == [shared.id]
    assert permission_service.has_server_permission(db, colleague, shared.id, "server.view")
    assert not permission_service.has_server_permission(db, colleague, hidden.id, "server.view")


def test_leaving_the_team_removes_access_immediately(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.start")
    team = _team_with_member(db, regular_user, colleague)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )
    assert permission_service.has_server_permission(db, colleague, server.id, "server.start")

    team_service.remove_member(db, team=team, user=regular_user, member_user_id=colleague.id)

    assert not permission_service.has_server_permission(db, colleague, server.id, "server.start")
    assert permission_service.list_visible_server_ids(db, colleague) == []


# ── Das persoenliche Team ─────────────────────────────────────────────


def test_personal_team_is_created_once_and_is_a_team_of_one(
    db: Session, regular_user: User
) -> None:
    first = team_service.personal_team(db, regular_user)
    db.commit()
    second = team_service.personal_team(db, regular_user)
    db.commit()

    assert first.id == second.id
    assert first.is_personal
    assert db.query(TeamMember).filter(TeamMember.team_id == first.id).count() == 1
    # Der Benutzer darf in seinem eigenen Team beides — sonst haette die KI
    # keinen Ort, an dem sie ohne Zutun lernen kann.
    member = team_service.membership(db, first.id, regular_user.id)
    assert member is not None and member.can_manage_skills and member.can_manage_memory


def test_personal_team_refuses_members_and_deletion(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    personal = team_service.personal_team(db, regular_user)
    db.commit()

    with pytest.raises(Exception) as add_exc:
        team_service.add_member(
            db, team=personal, user=regular_user, new_user_id=colleague.id,
            can_manage_skills=False, can_manage_memory=False,
        )
    assert getattr(add_exc.value, "status_code", None) == 409

    with pytest.raises(Exception) as delete_exc:
        team_service.delete_team(db, team=personal, user=regular_user)
    assert getattr(delete_exc.value, "status_code", None) == 409


def test_creating_a_real_team_requires_the_permission(
    db: Session, regular_user: User
) -> None:
    with pytest.raises(Exception) as exc:
        team_service.create_team(db, user=regular_user, name="Ohne Recht")
    assert getattr(exc.value, "status_code", None) == 403


# ── Wohin die KI lernt ────────────────────────────────────────────────


def test_learning_target_is_the_single_real_team(db: Session, regular_user: User) -> None:
    colleague = _user(db, "kollege")
    team = _team_with_member(db, regular_user, colleague)

    target, question = team_service.learning_team(db, colleague)
    assert question is None
    assert target is not None and target.id == team.id


def test_learning_target_falls_back_to_the_personal_team(
    db: Session, regular_user: User
) -> None:
    target, question = team_service.learning_team(db, regular_user)
    db.commit()
    assert question is None
    assert target is not None and target.is_personal


def test_learning_asks_when_several_teams_are_possible(
    db: Session, regular_user: User
) -> None:
    """Bei mehreren Teams ist Raten teuer: Wissen landete bei den Falschen."""
    colleague = _user(db, "kollege")
    other_owner = _user(db, "zweiter")
    _team_with_member(db, regular_user, colleague)
    _team_with_member(db, other_owner, colleague)

    target, question = team_service.learning_team(db, colleague)
    assert target is None
    assert question is not None and "mehreren Teams" in question


def test_member_without_the_switch_is_no_learning_target(
    db: Session, regular_user: User
) -> None:
    """Wer das Teamwissen nicht pflegen darf, fuer den lernt die KI persoenlich."""
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="Nur lesen")
    colleague = _user(db, "kollege")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    target, question = team_service.learning_team(db, colleague)
    db.commit()
    assert question is None
    assert target is not None and target.is_personal
