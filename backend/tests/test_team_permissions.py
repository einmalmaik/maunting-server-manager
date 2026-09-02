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
    """Ein Team mit einem Mitglied — Einladung **und** Annahme.

    Beides gehört hierher, weil eine Mitgliedschaft seit der Einladungstabelle
    zwei Schritte braucht. Ein Helfer, der nur einlädt, würde in jedem Test
    darunter ein Nichtmitglied für ein Mitglied halten.
    """
    _grant_global(db, owner, "teams.create")
    team = team_service.create_team(db, user=owner, name=f"team-{owner.username}")
    team_service.invite_member(
        db, team=team, user=owner, new_user_id=member.id,
        can_manage_skills=True, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=member, team_id=team.id)
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


def test_a_team_grant_counts_as_holding_the_right_anywhere(
    db: Session, regular_user: User
) -> None:
    """`has_permission_anywhere` muss den Teamweg mitzaehlen.

    Die Funktion beantwortet die Frage des KI-Werkzeugkatalogs: "kann dieser
    Benutzer das ueberhaupt". Wuerde sie den Teamweg uebersehen, bekaeme ein
    Teammitglied Werkzeuge nicht angeboten, die es sehr wohl benutzen darf —
    kein Loch, aber ein stiller Funktionsverlust genau bei den Benutzern, die
    ihre Rechte nicht direkt halten.
    """
    colleague = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.files.read")
    assert not permission_service.has_permission_anywhere(
        db, colleague, "server.files.read"
    )

    team = _team_with_member(db, regular_user, colleague)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.files.read"],
    )

    assert permission_service.has_permission_anywhere(db, colleague, "server.files.read")
    # Und der Deckel gilt auch hier: was der Gruender nicht weitergegeben hat,
    # entsteht nicht.
    assert not permission_service.has_permission_anywhere(
        db, colleague, "server.files.write"
    )


def test_holding_a_right_anywhere_is_not_holding_it_everywhere(
    db: Session, regular_user: User
) -> None:
    """Die weichere Frage darf die harte nicht ersetzen.

    Sonst waere der Werkzeugkatalog eine Rechteausweitung: wer auf Server A
    schreiben darf, duerfte es auch auf B.
    """
    server_a = _server(db, "eigener")
    server_b = _server(db, "fremder")
    _allow(db, regular_user, server_a, "server.view", "server.files.write")

    assert permission_service.has_permission_anywhere(
        db, regular_user, "server.files.write"
    )
    assert not permission_service.has_server_permission(
        db, regular_user, server_b.id, "server.files.write"
    )


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


def test_a_borrowed_permission_cannot_be_written_down_as_a_permanent_one(
    db: Session, regular_user: User
) -> None:
    """Geliehenes weiterzugeben hiesse, es dauerhaft zu machen.

    `_ensure_no_server_escalation` in `routers/admin.py` prueft, ob der
    Handelnde ein Recht selbst besitzt — und benutzte dafuer
    `has_server_permission`. Das zaehlt seit der Team-Erweiterung auch die
    Leihe mit.

    Damit liess sich ein geliehenes Recht in eine eigene `ServerPermission`
    umschreiben. Die Zeile ueberlebt den Austritt aus dem Team, das Aufloesen
    des Teams und den Rechteverlust dessen, der das Recht ueberhaupt
    hineingebracht hat — genau das, was
    `test_permission_evaporates_when_the_founder_loses_it` und
    `test_leaving_the_team_removes_access_immediately` fuer den regulaeren Weg
    ausschliessen.

    Geprueft wird hier die Stelle selbst, nicht der HTTP-Weg: sie ist die
    Obergrenze, und sie muss dieselbe sein wie in `team_service`.
    """
    from routers.admin import _ensure_no_server_escalation
    from fastapi import HTTPException

    gruender = regular_user
    mitglied = _user(db, "mitglied-mit-verwaltung")
    server = _server(db, "geliehen")

    # Der Gruender haelt das Recht direkt und reicht es ueber das Team weiter.
    _allow(db, gruender, server, "server.view", "server.console.exec")
    team = _team_with_member(db, gruender, mitglied)
    team_service.set_server_grants(
        db, team=team, user=gruender, server_id=server.id,
        keys=["server.view", "server.console.exec"],
    )

    # Das Mitglied hat das Recht jetzt — aber nur geliehen.
    assert permission_service.has_server_permission(
        db, mitglied, server.id, "server.console.exec"
    )
    assert not permission_service.direct_server_permission(
        db, mitglied, server.id, "server.console.exec"
    )

    # Und darf es deshalb nicht als eigene Delegation eintragen — auch nicht
    # fuer sich selbst.
    _grant_global(db, mitglied, "users.permissions.manage")
    with pytest.raises(HTTPException) as fehler:
        _ensure_no_server_escalation(
            db, mitglied, server.id, ["server.view", "server.console.exec"]
        )
    assert fehler.value.status_code == 403

    # Die Gegenprobe: wer das Recht **selbst** haelt, darf es weitergeben.
    # Ohne sie waere der Test auch dann gruen, wenn die Delegation insgesamt
    # nicht mehr funktionierte.
    _ensure_no_server_escalation(
        db, gruender, server.id, ["server.view", "server.console.exec"]
    )


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
        team_service.invite_member(
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
    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )
    team_service.accept_invitation(db, user=colleague, team_id=team.id)

    target, question = team_service.learning_team(db, colleague)
    db.commit()
    assert question is None
    assert target is not None and target.is_personal


def test_a_memory_target_follows_the_memory_switch(db: Session, regular_user: User) -> None:
    """Welcher Schalter zaehlt, entscheidet die Art des Wissens.

    `learning_team` fragte fest `can_manage_skills` ab, wurde aber von beiden
    Erinnerungswerkzeugen benutzt. Ein Mitglied, das Teamwissen pflegen darf
    aber keine Skills, bekam sein „merk dir fuers Team" still ins persoenliche
    Gedaechtnis geschrieben — kein Fehler, keine Meldung, nur der falsche Ort.
    """
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="Nur Wissen")
    colleague = _user(db, "kollege")
    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=colleague, team_id=team.id)

    ziel, frage = team_service.learning_team(db, colleague, schalter="memory")
    assert frage is None
    assert ziel is not None and ziel.id == team.id

    # Die Gegenprobe: fuer Skills bleibt derselbe Benutzer beim persoenlichen.
    ziel_skills, _ = team_service.learning_team(db, colleague, schalter="skills")
    db.commit()
    assert ziel_skills is not None and ziel_skills.is_personal


def test_a_named_team_ends_the_dead_end_with_several_teams(
    db: Session, regular_user: User
) -> None:
    """Mit zwei Teams war Teamlernen bisher unmoeglich.

    Die Rueckfrage kam, aber kein Werkzeug nahm die Antwort entgegen — das
    Modell fragte erneut, und der Benutzer sah die Frage in Schleife. Der Name
    aus der Antwort darf jetzt **auswaehlen**, was der Dienst ohnehin ermittelt
    hat.
    """
    colleague = _user(db, "kollege")
    other_owner = _user(db, "zweiter")
    erstes = _team_with_member(db, regular_user, colleague)
    _team_with_member(db, other_owner, colleague)

    ziel, frage = team_service.learning_team(db, colleague, wunsch=erstes.name)
    assert frage is None
    assert ziel is not None and ziel.id == erstes.id

    # Gross- und Kleinschreibung sind keine Huerde: das Modell gibt den Namen
    # so wieder, wie der Benutzer ihn gesagt hat.
    gross, _ = team_service.learning_team(db, colleague, wunsch=erstes.name.upper())
    assert gross is not None and gross.id == erstes.id


def test_a_foreign_team_cannot_be_named(db: Session, regular_user: User) -> None:
    """Der Name ist ein Auswahlmittel, keine Berechtigung.

    Ein Team, in dem der Benutzer nicht ist, bleibt unerreichbar — und die
    Antwort verraet auch nicht, dass es dieses Team gibt. Sie ist Wort fuer Wort
    dieselbe wie ohne jede Nennung.
    """
    colleague = _user(db, "kollege")
    other_owner = _user(db, "zweiter")
    _team_with_member(db, regular_user, colleague)
    _team_with_member(db, other_owner, colleague)

    fremder = _user(db, "dritter")
    _grant_global(db, fremder, "teams.create")
    fremdes = team_service.create_team(db, user=fremder, name="Geheimprojekt")

    ziel, frage = team_service.learning_team(db, colleague, wunsch=fremdes.name)
    assert ziel is None
    assert frage is not None
    assert "Geheimprojekt" not in frage
    _, ohne_nennung = team_service.learning_team(db, colleague)
    assert frage == ohne_nennung


def test_the_personal_team_is_never_a_candidate(db: Session, regular_user: User) -> None:
    """Das Ein-Mann-Team ist der Rueckfall, kein Ziel unter mehreren.

    Es hier aufzunehmen hiesse, den Benutzer zwischen „meinem eigenen Bereich"
    und einem echten Team waehlen zu lassen, als waeren das dieselbe Art Sache.
    """
    colleague = _user(db, "kollege")
    team = _team_with_member(db, regular_user, colleague)
    team_service.personal_team(db, colleague)
    db.commit()

    kandidaten = team_service.learning_teams(db, colleague, schalter="skills")
    assert [row.id for row in kandidaten] == [team.id]


# ── Die Vorschlagsliste: mengenweise, aber Zeichen für Zeichen dieselbe ──
#
# `assignable-servers` fragte je Server und je Rechteschlüssel einzeln nach —
# 28 Rechtefragen mal drei Abfragen je Server, rund 1700 bei dreißig Servern.
# Die Bündelung über `direkte_rechte` ist nur dann eine Verbesserung, wenn sie
# **dieselbe** Antwort gibt. Eine schnellere Liste mit anderem Inhalt wäre hier
# entweder eine stille Rechteausweitung (ein Server zuviel im Dialog) oder ein
# stiller Rechteverlust. Deshalb rechnet jeder Test unten beide Wege
# gegeneinander, statt eine Wunschliste zu behaupten.


def _alter_weg(db: Session, user: User) -> dict[int, list[str]]:
    """Die Liste, wie sie die Einzelabfrage je Schlüssel ergeben würde."""
    from services.permission_catalog import SERVER_KEYS

    erwartet: dict[int, list[str]] = {}
    for server in db.query(Server).order_by(Server.name).all():
        if not permission_service.direct_server_permission(
            db, user, server.id, "server.view"
        ):
            continue
        erwartet[server.id] = sorted(
            key for key in SERVER_KEYS
            if permission_service.direct_server_permission(db, user, server.id, key)
        )
    return erwartet


def _vergleiche_vorschlagsliste(db: Session, user: User, team: Team) -> dict[int, list[str]]:
    """Rechnet Endpunkt und Einzelabfrage gegeneinander."""
    from routers.teams import assignable_servers

    neu = {
        eintrag.server_id: sorted(eintrag.permission_keys)
        for eintrag in assignable_servers(team.id, db, user)
    }
    alt = _alter_weg(db, user)
    assert neu == alt, (
        f"Vorschlagsliste weicht ab für {user.username}: neu={neu} alt={alt}"
    )
    return neu


def test_assignable_servers_matches_the_single_lookup_for_a_delegated_customer(
    db: Session, regular_user: User
) -> None:
    """Ein Kunde mit Delegation auf genau einem von zwei Servern.

    Der zweite Server darf nicht auftauchen — die Vorschlagsliste ist die
    Obergrenze, die `permission_service` später ohnehin durchsetzt.
    """
    einer = _server(db, "a-einer")
    _server(db, "b-anderer")
    _allow(db, regular_user, einer, "server.view", "server.start", "server.console.read")
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="kundenteam")

    liste = _vergleiche_vorschlagsliste(db, regular_user, team)
    assert liste == {
        einer.id: ["server.console.read", "server.start", "server.view"],
    }


def test_assignable_servers_matches_the_single_lookup_for_a_role_holder(
    db: Session, regular_user: User
) -> None:
    """Eine globale Rolle gilt pauschal — auf **allen** Servern.

    Genau hier trennt sich die gebündelte Menge in `pauschal` und `je Server`;
    wer die beiden verwechselt, verliert entweder alle Server oder gibt einen
    Schlüssel auf einem Server aus, auf dem er nicht gilt.
    """
    a = _server(db, "a-eins")
    b = _server(db, "b-zwei")
    _grant_global(
        db, regular_user,
        "teams.create", "server.view", "server.files.read", "server.backups.read",
    )
    team = team_service.create_team(db, user=regular_user, name="rollenteam")

    erwartet = ["server.backups.read", "server.files.read", "server.view"]
    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {
        a.id: erwartet, b.id: erwartet,
    }


def test_assignable_servers_mixes_a_global_role_with_a_delegation(
    db: Session, regular_user: User
) -> None:
    """Rolle und Delegation ergänzen sich, sie ersetzen sich nicht.

    Der Realfall eines gewachsenen Kontos: `server.view` pauschal über die
    Rolle, `server.console.exec` nur auf einem einzigen Server. Fiele die
    Vereinigung falsch aus, stünde das mächtigste Serverrecht entweder
    überall oder nirgends im Dialog.
    """
    a = _server(db, "a-eins")
    b = _server(db, "b-zwei")
    _grant_global(db, regular_user, "teams.create", "server.view")
    _allow(db, regular_user, b, "server.console.exec")
    team = team_service.create_team(db, user=regular_user, name="mischteam")

    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {
        a.id: ["server.view"],
        b.id: ["server.console.exec", "server.view"],
    }


def test_assignable_servers_matches_the_single_lookup_for_the_owner(
    db: Session, owner_user: User
) -> None:
    """Der Owner hält alles ohne eine einzige Zeile in der Datenbank."""
    from services.permission_catalog import SERVER_KEYS

    a = _server(db, "a-eins")
    b = _server(db, "b-zwei")
    team = team_service.create_team(db, user=owner_user, name="ownerteam")

    alle = sorted(SERVER_KEYS)
    assert _vergleiche_vorschlagsliste(db, owner_user, team) == {
        a.id: alle, b.id: alle,
    }


def test_assignable_servers_stays_empty_without_any_right(
    db: Session, regular_user: User
) -> None:
    """Ohne `server.view` bleibt die Liste leer, auch wenn Server existieren."""
    _server(db, "a-fremd")
    _server(db, "b-fremd")
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="leerteam")

    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {}


def test_assignable_servers_does_not_ask_once_per_server(
    db: Session, regular_user: User
) -> None:
    """Die Abfragezahl darf nicht mit der Serverzahl wachsen.

    Die Grenze steht bewusst großzügig und schreibt keine Zählung fest. Sie
    fängt den einen Fehler ab, der hier zurückfallen kann: eine Schleife, die
    je Server und je Schlüssel erneut fragt. Gemessen waren das 1682 Abfragen
    bei dreißig Servern.
    """
    from sqlalchemy import event

    import database as db_module
    from routers.teams import assignable_servers

    for i in range(12):
        _server(db, f"s{i:02d}")
    _grant_global(db, regular_user, "teams.create", "server.view", "server.start")
    team = team_service.create_team(db, user=regular_user, name="zaehlteam")

    gesehen: list[str] = []

    def _hook(conn, cursor, statement, parameters, context, executemany) -> None:
        gesehen.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", _hook)
    try:
        eintraege = assignable_servers(team.id, db, regular_user)
    finally:
        event.remove(db_module.engine, "before_cursor_execute", _hook)

    assert len(eintraege) == 12
    assert len(gesehen) <= 10, (
        f"{len(gesehen)} Abfragen für zwölf Server:\n" + "\n".join(gesehen)
    )


# ── Hoster-Kundenserver in der Vorschlagsliste ─────────────────────────────


def _als_kundenserver_markieren(db: Session, server: Server, kunde: User) -> None:
    """Eine hoster_services-Zeile mit server_id ist die Markierung, auf die
    der Sichtbarkeitsfilter schaut — mehr braucht dieser Test nicht."""
    from models import HosterIdentity, HosterIntegration, HosterService

    integration = HosterIntegration(
        name=f"Shop {server.name}",
        slug=f"shop-{server.id}",
        service_user_id=kunde.id,
        api_key_hash=f"hash-{server.id}",
    )
    db.add(integration)
    db.flush()
    identity = HosterIdentity(
        integration_id=integration.id,
        external_subject_hash=f"subject-{server.id}",
        user_id=kunde.id,
    )
    db.add(identity)
    db.flush()
    db.add(HosterService(
        integration_id=integration.id,
        external_service_id=f"svc-{server.id}",
        identity_id=identity.id,
        server_id=server.id,
        status="ready",
        correlation_id=f"corr-{server.id}",
    ))
    db.commit()


def test_assignable_servers_hides_hoster_customer_servers_from_blanket_roles(
    db: Session, regular_user: User, owner_user: User
) -> None:
    """Die Vorschlagsliste darf nicht anbieten, was das Speichern verweigert.

    Ohne den Filter leakte GET /teams/{id}/assignable-servers Existenz und
    Namen aller Hoster-Kundenserver an jeden mit pauschalem `server.view` —
    genau die Menge, die `list_visible_server_ids` verbirgt, und der Grant
    selbst scheiterte danach mit 404. Der Aequivalenzvergleich unten rechnet
    den Endpunkt gegen `direct_server_permission`, das den Hoster-Gate traegt.
    """
    eigener = _server(db, "a-eigener")
    kunde = _server(db, "b-kundenserver")
    _als_kundenserver_markieren(db, kunde, owner_user)
    _grant_global(db, regular_user, "teams.create", "server.view", "server.start")
    team = team_service.create_team(db, user=regular_user, name="supportteam")

    erwartet = ["server.start", "server.view"]
    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {
        eigener.id: erwartet,
    }


def test_assignable_servers_offers_customer_servers_to_hoster_key_holders(
    db: Session, regular_user: User, owner_user: User
) -> None:
    """Mit dem Hoster-Key gilt die pauschale Rolle auch auf Kundenservern —
    und eine Delegation auf dem Kundenserver zaehlt unabhaengig vom Key."""
    kunde = _server(db, "b-kundenserver")
    _als_kundenserver_markieren(db, kunde, owner_user)
    _grant_global(
        db, regular_user,
        "teams.create", "server.view", "servers.hoster_customers.view",
    )
    team = team_service.create_team(db, user=regular_user, name="hosterteam")

    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {
        kunde.id: ["server.view"],
    }


def test_assignable_servers_keeps_the_customers_own_delegation(
    db: Session, regular_user: User, owner_user: User
) -> None:
    """Der Kunde selbst sieht seinen Vertragsserver ueber die Delegation."""
    kunde = _server(db, "b-kundenserver")
    _als_kundenserver_markieren(db, kunde, owner_user)
    _allow(db, regular_user, kunde, "server.view", "server.console.read")
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="kundenteam2")

    assert _vergleiche_vorschlagsliste(db, regular_user, team) == {
        kunde.id: ["server.console.read", "server.view"],
    }


# ── Einladung statt Zwangsmitgliedschaft ──────────────────────────────
#
# Bis zum 23.08.2026 trug `add_member` einen beliebigen Benutzer direkt ein.
# Wer `teams.create` hatte — im Hoster-Betrieb jeder Kunde —, konnte damit
# jeden anderen still in sein Team ziehen. Das öffnete nicht nur eine
# Mitgliederliste: das Team-Wissen des Gründers fließt ab diesem Moment in
# jeden KI-Lauf des Hinzugefügten, und was dessen KI für das Team lernt,
# landet im Team des Fremden und ist dort im Klartext lesbar.
#
# Die Tests unten prüfen deshalb nicht "es gibt eine Einladungstabelle",
# sondern die drei Türen, die eine Mitgliedschaft öffnet: Serverrechte,
# Team-Gedächtnis und Lernziel. Vor dem Beitritt muss jede davon zu sein.


def _lade_ein(db: Session, owner: User, target: User, *, name: str = "einladeteam") -> Team:
    _grant_global(db, owner, "teams.create")
    team = team_service.create_team(db, user=owner, name=name)
    team_service.invite_member(
        db, team=team, user=owner, new_user_id=target.id,
        can_manage_skills=True, can_manage_memory=True,
    )
    return team


def test_an_invitation_opens_no_door_until_it_is_accepted(
    db: Session, regular_user: User
) -> None:
    """Der Kern des Befunds: Einladen allein verschafft dem Gründer nichts.

    Mit dem alten `add_member` war jede einzelne Zusage unten falsch — das
    Opfer war sofort Mitglied, sein KI-Lauf las das fremde Team-Wissen, und
    sein "merk dir das fürs Team" schrieb in ein Team, das ihm niemand
    angeboten hatte.
    """
    opfer = _user(db, "opfer")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.console.exec")
    team = _lade_ein(db, regular_user, opfer)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.console.exec"],
    )

    # 1. Keine Mitgliedschaft — und damit ist jede Abfrage, die auf
    #    `team_members` schaut, ohne Zutun richtig.
    assert team_service.membership(db, team.id, opfer.id) is None
    # 2. Keine Serverrechte des Teams.
    assert not permission_service.has_server_permission(
        db, opfer, server.id, "server.console.exec"
    )
    assert permission_service.list_visible_server_ids(db, opfer) == []
    # 3. Kein Zugriff auf das Team-Gedächtnis: `user_team_ids` ist die Liste,
    #    nach der die Erinnerungen im KI-Lauf gefiltert werden.
    assert team.id not in team_service.user_team_ids(db, opfer)
    # 4. Und kein Lernziel: was die KI des Opfers fürs Team merkt, darf nicht
    #    im Team des Fremden landen.
    ziel, frage = team_service.learning_team(db, opfer, schalter="memory")
    db.commit()
    assert frage is None
    assert ziel is not None and ziel.is_personal


def test_accepting_makes_the_membership_real(db: Session, regular_user: User) -> None:
    """Die Gegenprobe: nach der Annahme steht dem Team nichts im Weg.

    Ohne sie wäre der Test oben auch dann grün, wenn Teams gar nicht mehr
    funktionierten.
    """
    kollege = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.start")
    team = _lade_ein(db, regular_user, kollege)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )

    mitglied = team_service.accept_invitation(db, user=kollege, team_id=team.id)

    assert mitglied.role == "member"
    assert permission_service.has_server_permission(db, kollege, server.id, "server.start")
    assert team.id in team_service.user_team_ids(db, kollege)
    # Die Einladung ist verbraucht, nicht liegengeblieben.
    assert team_service.invitation(db, team.id, kollege.id) is None


def test_the_switches_come_from_the_invitation_not_from_the_accepting_user(
    db: Session, regular_user: User
) -> None:
    """Angenommen wird genau das, was angeboten wurde.

    Sonst wäre die Annahme eine Selbstbedienung: der Eingeladene träte mit
    mehr Rechten am gemeinsamen Wissen bei, als der Gründer vergeben hat.
    """
    kollege = _user(db, "kollege")
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="Nur lesen")
    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    mitglied = team_service.accept_invitation(db, user=kollege, team_id=team.id)

    assert not mitglied.can_manage_skills
    assert not mitglied.can_manage_memory
    assert not team_service.can_manage_team_memory(db, kollege, team.id)


def test_only_the_invited_user_can_accept(db: Session, regular_user: User) -> None:
    """Eine Einladung an B ist keine offene Tür für C."""
    b = _user(db, "bene")
    c = _user(db, "cara")
    team = _lade_ein(db, regular_user, b)

    with pytest.raises(Exception) as exc:
        team_service.accept_invitation(db, user=c, team_id=team.id)
    assert getattr(exc.value, "status_code", None) == 404
    assert team_service.membership(db, team.id, c.id) is None
    # Die Einladung an B ist unversehrt.
    assert team_service.invitation(db, team.id, b.id) is not None


def test_declining_ends_the_invitation_for_good(db: Session, regular_user: User) -> None:
    kollege = _user(db, "kollege")
    team = _lade_ein(db, regular_user, kollege)

    team_service.decline_invitation(db, user=kollege, team_id=team.id)

    assert team_service.invitation(db, team.id, kollege.id) is None
    with pytest.raises(Exception) as exc:
        team_service.accept_invitation(db, user=kollege, team_id=team.id)
    assert getattr(exc.value, "status_code", None) == 404


def test_a_second_invitation_replaces_the_offer_instead_of_stacking(
    db: Session, regular_user: User
) -> None:
    """Der Gründer kann sein Angebot zurücknehmen, solange niemand angenommen hat.

    Ohne diese Zusage bräuchte es einen zweiten Weg, eine offene Einladung
    zurückzuziehen — und bis dahin bliebe ein zu großzügiges Angebot stehen.
    """
    kollege = _user(db, "kollege")
    team = _lade_ein(db, regular_user, kollege)

    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    assert len(team_service.team_invitations(db, team.id)) == 1
    mitglied = team_service.accept_invitation(db, user=kollege, team_id=team.id)
    assert not mitglied.can_manage_skills and not mitglied.can_manage_memory


def test_an_existing_member_cannot_be_invited_again(
    db: Session, regular_user: User
) -> None:
    kollege = _user(db, "kollege")
    team = _team_with_member(db, regular_user, kollege)

    with pytest.raises(Exception) as exc:
        team_service.invite_member(
            db, team=team, user=regular_user, new_user_id=kollege.id,
            can_manage_skills=True, can_manage_memory=True,
        )
    assert getattr(exc.value, "status_code", None) == 409


def test_only_the_founder_may_invite(db: Session, regular_user: User) -> None:
    """Ein Mitglied ist kein Gründer — auch nicht mit beiden Schaltern."""
    mitglied = _user(db, "mitglied")
    fremder = _user(db, "fremder")
    team = _team_with_member(db, regular_user, mitglied)

    with pytest.raises(Exception) as exc:
        team_service.invite_member(
            db, team=team, user=mitglied, new_user_id=fremder.id,
            can_manage_skills=True, can_manage_memory=True,
        )
    assert getattr(exc.value, "status_code", None) == 403


# ── Gehen darf man allein ─────────────────────────────────────────────


def test_a_member_can_leave_without_the_founder(db: Session, regular_user: User) -> None:
    """Das Gegenstück zur Einladung.

    Vorher konnte nur der Gründer entlassen — ein zwangsweise Hinzugefügter
    saß fest, bis der, der ihn hineingezogen hatte, ihn wieder freigab.
    """
    kollege = _user(db, "kollege")
    server = _server(db, "geteilt")
    _allow(db, regular_user, server, "server.view", "server.start")
    team = _team_with_member(db, regular_user, kollege)
    team_service.set_server_grants(
        db, team=team, user=regular_user, server_id=server.id,
        keys=["server.view", "server.start"],
    )

    team_service.remove_member(db, team=team, user=kollege, member_user_id=kollege.id)

    assert team_service.membership(db, team.id, kollege.id) is None
    assert not permission_service.has_server_permission(db, kollege, server.id, "server.start")
    assert team.id not in team_service.user_team_ids(db, kollege)


def test_leaving_alone_is_not_a_licence_to_remove_others(
    db: Session, regular_user: User
) -> None:
    """Selbstaustritt ist genau das — kein Recht am Nachbarn."""
    einer = _user(db, "einer")
    anderer = _user(db, "anderer")
    team = _team_with_member(db, regular_user, einer)
    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=anderer.id,
        can_manage_skills=False, can_manage_memory=False,
    )
    team_service.accept_invitation(db, user=anderer, team_id=team.id)

    with pytest.raises(Exception) as exc:
        team_service.remove_member(db, team=team, user=einer, member_user_id=anderer.id)
    assert getattr(exc.value, "status_code", None) == 403
    assert team_service.membership(db, team.id, anderer.id) is not None


def test_the_founder_cannot_leave_their_own_team(db: Session, regular_user: User) -> None:
    """Sein Konto ist die Obergrenze für alles, was das Team weitergibt."""
    kollege = _user(db, "kollege")
    team = _team_with_member(db, regular_user, kollege)

    with pytest.raises(Exception) as exc:
        team_service.remove_member(
            db, team=team, user=regular_user, member_user_id=regular_user.id
        )
    assert getattr(exc.value, "status_code", None) == 409


# ── Anheben braucht den Betroffenen ───────────────────────────────────
#
# Die Einladung sichert den Beitritt — und hielt genau bis zum nächsten
# `update_member`. Dort prüfte nur `assert_team_owner`, der Gründer konnte den
# Schalter eines Mitglieds also nachträglich einschalten. Damit war der
# Ausgangsbefund mit einem Zwischenschritt wieder da: harmlos einladen (beide
# Schalter aus), annehmen lassen, danach anheben.


def _stilles_mitglied(db: Session, owner: User, target: User, *, name: str) -> Team:
    """Ein Mitglied, das beiden Schaltern nach nichts darf — der Ausgangspunkt."""
    _grant_global(db, owner, "teams.create")
    team = team_service.create_team(db, user=owner, name=name)
    team_service.invite_member(
        db, team=team, user=owner, new_user_id=target.id,
        can_manage_skills=False, can_manage_memory=False,
    )
    team_service.accept_invitation(db, user=target, team_id=team.id)
    return team


def test_the_founder_cannot_raise_a_switch_on_their_own(
    db: Session, regular_user: User
) -> None:
    """Der Kern: eingeschaltet wird nur, was der Betroffene annimmt.

    Der Schaden hängt nicht an der Mitgliederliste, sondern am Lernziel. Hat
    das Opfer kein eigenes verwaltbares Team, ist das fremde nach der Anhebung
    der **einzige** Kandidat in `learning_team` — was seine KI „fürs Team“
    merkt, landet ab dann im Team des Gründers und ist dort im Klartext lesbar.
    """
    opfer = _user(db, "opfer")
    team = _stilles_mitglied(db, regular_user, opfer, name="Betrieb")

    mitglied = team_service.update_member(
        db, team=team, user=regular_user, member_user_id=opfer.id,
        can_manage_skills=True, can_manage_memory=True,
    )

    assert not mitglied.can_manage_skills
    assert not mitglied.can_manage_memory
    assert not team_service.can_manage_team_memory(db, opfer, team.id)
    # Und damit bleibt das fremde Team kein Lernziel.
    ziel, frage = team_service.learning_team(db, opfer, schalter="memory")
    db.commit()
    assert frage is None
    assert ziel is not None and ziel.is_personal


def test_a_raised_switch_takes_effect_once_the_member_accepts(
    db: Session, regular_user: User
) -> None:
    """Die Gegenprobe: der Weg ist nicht zu, er führt nur über den Betroffenen.

    Ohne sie wäre der Test darüber auch dann grün, wenn `update_member`
    überhaupt nichts mehr täte.
    """
    kollege = _user(db, "kollege")
    team = _stilles_mitglied(db, regular_user, kollege, name="Betrieb")
    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=True,
    )

    # Der Betroffene sieht das Angebot dort, wo auch eine Einladung steht.
    offen = team_service.open_invitations(db, kollege)
    assert [zeile.team_id for zeile in offen] == [team.id]
    assert offen[0].can_manage_memory and not offen[0].can_manage_skills

    mitglied = team_service.accept_invitation(db, user=kollege, team_id=team.id)

    assert mitglied.can_manage_memory
    # Angenommen wird genau das Angebot — auch der Schalter, der aus bleibt.
    assert not mitglied.can_manage_skills
    assert team_service.can_manage_team_memory(db, kollege, team.id)
    ziel, frage = team_service.learning_team(db, kollege, schalter="memory")
    assert frage is None
    assert ziel is not None and ziel.id == team.id
    # Eine Mitgliedschaft, keine zweite daneben.
    assert db.query(TeamMember).filter(
        TeamMember.team_id == team.id, TeamMember.user_id == kollege.id
    ).count() == 1
    assert team_service.invitation(db, team.id, kollege.id) is None


def test_declining_a_raise_leaves_the_member_where_they_were(
    db: Session, regular_user: User
) -> None:
    """Ablehnen kostet die Mitgliedschaft nicht — es kostet nur das Angebot."""
    kollege = _user(db, "kollege")
    team = _stilles_mitglied(db, regular_user, kollege, name="Betrieb")
    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=True, can_manage_memory=True,
    )

    team_service.decline_invitation(db, user=kollege, team_id=team.id)

    assert team_service.membership(db, team.id, kollege.id) is not None
    assert not team_service.can_manage_team_memory(db, kollege, team.id)
    assert team_service.invitation(db, team.id, kollege.id) is None


def test_a_withdrawn_raise_cannot_be_accepted_later(
    db: Session, regular_user: User
) -> None:
    """Der Gründer nimmt sein Angebot zurück, indem er etwas anderes will.

    Ohne diese Zusage bliebe das alte Angebot liegen: der Gründer sagt „doch
    nicht“, und das Mitglied nimmt Wochen später trotzdem an.
    """
    kollege = _user(db, "kollege")
    team = _stilles_mitglied(db, regular_user, kollege, name="Betrieb")
    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=True, can_manage_memory=True,
    )

    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    assert team_service.open_invitations(db, kollege) == []
    with pytest.raises(Exception) as exc:
        team_service.accept_invitation(db, user=kollege, team_id=team.id)
    assert getattr(exc.value, "status_code", None) == 404


def test_lowering_a_switch_stays_the_founders_call(
    db: Session, regular_user: User
) -> None:
    """Zurücknehmen braucht niemanden: der Gründer entzieht, was er gab."""
    kollege = _user(db, "kollege")
    team = _team_with_member(db, regular_user, kollege)

    mitglied = team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    assert not mitglied.can_manage_skills and not mitglied.can_manage_memory
    assert not team_service.can_manage_team_memory(db, kollege, team.id)
    # Kein Angebot, über das jemand entscheiden müsste.
    assert team_service.open_invitations(db, kollege) == []


def test_the_founder_decides_about_their_own_switches_at_once(
    db: Session, regular_user: User
) -> None:
    """Wer über sich selbst entscheidet, hat damit zugestimmt.

    Sonst müsste der Gründer sich selbst eine Einladung schicken und sie
    annehmen, um an sein eigenes Teamwissen zu kommen.
    """
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="Allein")
    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=regular_user.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    mitglied = team_service.update_member(
        db, team=team, user=regular_user, member_user_id=regular_user.id,
        can_manage_skills=True, can_manage_memory=True,
    )

    assert mitglied.can_manage_skills and mitglied.can_manage_memory
    assert team_service.open_invitations(db, regular_user) == []


def test_a_pending_raise_does_not_outlive_the_membership(
    db: Session, regular_user: User
) -> None:
    """Sonst wäre die Annahme ein Wiedereintritt.

    Der Gründer hätte jemanden entlassen und erführe nichts davon, dass der
    über ein liegengebliebenes Angebot zurückkommt.
    """
    kollege = _user(db, "kollege")
    team = _stilles_mitglied(db, regular_user, kollege, name="Betrieb")
    team_service.update_member(
        db, team=team, user=regular_user, member_user_id=kollege.id,
        can_manage_skills=True, can_manage_memory=True,
    )

    team_service.remove_member(db, team=team, user=regular_user, member_user_id=kollege.id)

    assert team_service.open_invitations(db, kollege) == []
    with pytest.raises(Exception) as exc:
        team_service.accept_invitation(db, user=kollege, team_id=team.id)
    assert getattr(exc.value, "status_code", None) == 404
    assert team_service.membership(db, team.id, kollege.id) is None


# ── Namen bleiben ansprechbar ─────────────────────────────────────────


def test_a_founder_cannot_run_two_teams_with_the_same_name(
    db: Session, regular_user: User
) -> None:
    """Sonst wäre der Name als Auswahlmittel wertlos.

    Über verschiedene Gründer hinweg dürfen Namen sich wiederholen — eine
    Absage würde sonst verraten, dass es irgendwo ein Team dieses Namens gibt.
    """
    _grant_global(db, regular_user, "teams.create")
    team_service.create_team(db, user=regular_user, name="Alpha")

    with pytest.raises(Exception) as exc:
        team_service.create_team(db, user=regular_user, name="alpha")
    assert getattr(exc.value, "status_code", None) == 409

    zweiter = _user(db, "zweiter")
    _grant_global(db, zweiter, "teams.create")
    fremdes = team_service.create_team(db, user=zweiter, name="Alpha")
    assert fremdes.name == "Alpha"


def test_two_teams_of_the_same_name_stay_selectable(
    db: Session, regular_user: User
) -> None:
    """Namensgleichheit war eine Sackgasse: dieselbe Rückfrage, endlos.

    `learning_team` löst den Wunsch nur bei genau einem Treffer auf. Hießen
    beide Kandidaten "Alpha", traf jede Antwort zwei — Merken und Vergessen im
    Team-Bereich waren für diesen Benutzer dauerhaft unmöglich, und die
    Rückfrage bot obendrein zweimal denselben Namen an.
    """
    zweiter = _user(db, "zweiter")
    kollege = _user(db, "kollege")
    _grant_global(db, regular_user, "teams.create")
    _grant_global(db, zweiter, "teams.create")
    eins = team_service.create_team(db, user=regular_user, name="Alpha")
    zwei = team_service.create_team(db, user=zweiter, name="alpha")
    for team, owner in ((eins, regular_user), (zwei, zweiter)):
        team_service.invite_member(
            db, team=team, user=owner, new_user_id=kollege.id,
            can_manage_skills=True, can_manage_memory=True,
        )
        team_service.accept_invitation(db, user=kollege, team_id=team.id)

    ziel, frage = team_service.learning_team(db, kollege, wunsch="Alpha")
    assert ziel is None
    assert frage is not None
    # Die Rückfrage muss die beiden auseinanderhalten, sonst führt jede
    # Antwort wieder hierher.
    assert regular_user.username in frage and zweiter.username in frage

    gewaehlt, nachfrage = team_service.learning_team(
        db, kollege, wunsch=f"Alpha ({zweiter.username})"
    )
    assert nachfrage is None
    assert gewaehlt is not None and gewaehlt.id == zwei.id


def test_a_unique_name_keeps_its_plain_form(db: Session, regular_user: User) -> None:
    """Nur der mehrdeutige Name bekommt den Gründer dazu.

    Sonst müsste ein Benutzer mit zwei verschieden benannten Teams plötzlich
    Namen nennen, die er nirgends in der Oberfläche sieht.
    """
    zweiter = _user(db, "zweiter")
    kollege = _user(db, "kollege")
    _grant_global(db, regular_user, "teams.create")
    _grant_global(db, zweiter, "teams.create")
    for owner, name in ((regular_user, "Alpha"), (zweiter, "Beta")):
        team = team_service.create_team(db, user=owner, name=name)
        team_service.invite_member(
            db, team=team, user=owner, new_user_id=kollege.id,
            can_manage_skills=True, can_manage_memory=True,
        )
        team_service.accept_invitation(db, user=kollege, team_id=team.id)

    _, frage = team_service.learning_team(db, kollege)
    assert frage is not None
    assert "Alpha, Beta" in frage


def test_the_addressable_name_selects_the_team_it_named(
    db: Session, regular_user: User
) -> None:
    """Wer ein Team benennt, muss es damit auch wieder treffen.

    `ansprechbarer_name` ist die Form, in der ein Team ausserhalb der Auswahl
    genannt wird — in der vollen Absage und im Suchtreffer. Wäre sie eine
    andere als die, die `learning_team` annimmt, benennte sie ein Team, das
    danach niemand ansprechen kann; mit dem blossen "Alpha" benennt sie sogar
    zwei, und ein Löschen darunter träfe zur Hälfte das falsche.
    """
    zweiter = _user(db, "zweiter")
    kollege = _user(db, "kollege")
    _grant_global(db, regular_user, "teams.create")
    _grant_global(db, zweiter, "teams.create")
    eins = team_service.create_team(db, user=regular_user, name="Alpha")
    zwei = team_service.create_team(db, user=zweiter, name="Alpha")
    for team, owner in ((eins, regular_user), (zwei, zweiter)):
        team_service.invite_member(
            db, team=team, user=owner, new_user_id=kollege.id,
            can_manage_skills=True, can_manage_memory=True,
        )
        team_service.accept_invitation(db, user=kollege, team_id=team.id)

    for team in (eins, zwei):
        genannt = team_service.ansprechbarer_name(db, kollege, team)
        ziel, frage = team_service.learning_team(
            db, kollege, schalter="memory", wunsch=genannt
        )
        assert frage is None, f"„{genannt}“ wählt nichts aus"
        assert ziel is not None and ziel.id == team.id

    # Die Probe aufs Exempel: der blanke Name benennt beide und wählt keines.
    _, ohne_gruender = team_service.learning_team(
        db, kollege, schalter="memory", wunsch="Alpha"
    )
    assert ohne_gruender is not None

    # Die Gegenprobe: ein Team, das im Bestand des Benutzers einmalig heisst,
    # behält seinen blanken Namen — sonst müsste er Namen nennen, die er
    # nirgends in der Oberfläche sieht.
    allein = team_service.create_team(db, user=regular_user, name="Beta")
    team_service.invite_member(
        db, team=allein, user=regular_user, new_user_id=kollege.id,
        can_manage_skills=True, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=kollege, team_id=allein.id)
    assert team_service.ansprechbarer_name(db, kollege, allein) == "Beta"


def test_the_addressable_name_ignores_the_personal_team(
    db: Session, regular_user: User
) -> None:
    """Das Ein-Mann-Team steht in keiner Auswahl und macht nichts mehrdeutig.

    Es heisst wie sein Benutzer. Zählte es mit, bekäme ein Kunde namens
    "alpha" für sein einziges echtes Team "Alpha" plötzlich einen
    zusammengesetzten Namen zu hören, obwohl es nichts zu unterscheiden gibt.
    """
    _grant_global(db, regular_user, "teams.create")
    team = team_service.create_team(db, user=regular_user, name=regular_user.username)
    team_service.personal_team(db, regular_user)
    db.commit()

    assert team_service.ansprechbarer_name(db, regular_user, team) == team.name


def test_the_invited_user_can_see_and_accept_over_http(
    db: Session,
    client,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Der Weg, den ein Mensch wirklich geht — samt der Falle im Routenbaum.

    `/api/teams/invitations` muss **vor** `/api/teams/{team_id}` stehen, sonst
    liest FastAPI "invitations" als Teamnummer und antwortet mit 422. Der
    Eingeladene hätte dann keine Möglichkeit zu erfahren, dass ihn jemand
    haben will — und die Einladung wäre eine Sackgasse.
    """
    team = team_service.create_team(db, user=owner_user, name="Fernteam")
    team_service.invite_member(
        db, team=team, user=owner_user, new_user_id=regular_user.id,
        can_manage_skills=True, can_manage_memory=False,
    )

    offen = client.get("/api/teams/invitations", cookies=user_cookies)
    assert offen.status_code == 200
    assert [eintrag["team_id"] for eintrag in offen.json()] == [team.id]
    assert offen.json()[0]["invited_by_username"] == owner_user.username

    # Vor der Annahme ist das Team für ihn nicht einmal sichtbar.
    assert client.get(f"/api/teams/{team.id}", cookies=user_cookies).status_code == 404

    angenommen = client.post(
        f"/api/teams/invitations/{team.id}/accept",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert angenommen.status_code == 200
    assert regular_user.id in [
        mitglied["user_id"] for mitglied in angenommen.json()["members"]
    ]
    assert client.get("/api/teams/invitations", cookies=user_cookies).json() == []
