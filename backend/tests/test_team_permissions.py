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
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=True,
    )

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


# ── Die Vorschlagsliste: mengenweise, aber Zeichen fuer Zeichen dieselbe ──
#
# `assignable-servers` fragte je Server und je Rechteschluessel einzeln nach —
# 28 Rechtefragen mal drei Abfragen je Server, rund 1700 bei dreissig Servern.
# Die Bündelung ueber `direkte_rechte` ist nur dann eine Verbesserung, wenn sie
# **dieselbe** Antwort gibt. Eine schnellere Liste mit anderem Inhalt waere hier
# entweder eine stille Rechteausweitung (ein Server zuviel im Dialog) oder ein
# stiller Rechteverlust. Deshalb rechnet jeder Test unten beide Wege
# gegeneinander, statt eine Wunschliste zu behaupten.


def _alter_weg(db: Session, user: User) -> dict[int, list[str]]:
    """Die Liste, wie sie die Einzelabfrage je Schluessel ergeben wuerde."""
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
        f"Vorschlagsliste weicht ab fuer {user.username}: neu={neu} alt={alt}"
    )
    return neu


def test_assignable_servers_matches_the_single_lookup_for_a_delegated_customer(
    db: Session, regular_user: User
) -> None:
    """Ein Kunde mit Delegation auf genau einem von zwei Servern.

    Der zweite Server darf nicht auftauchen — die Vorschlagsliste ist die
    Obergrenze, die `permission_service` spaeter ohnehin durchsetzt.
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

    Genau hier trennt sich die gebuendelte Menge in `pauschal` und `je Server`;
    wer die beiden verwechselt, verliert entweder alle Server oder gibt einen
    Schluessel auf einem Server aus, auf dem er nicht gilt.
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
    """Rolle und Delegation ergaenzen sich, sie ersetzen sich nicht.

    Der Realfall eines gewachsenen Kontos: `server.view` pauschal ueber die
    Rolle, `server.console.exec` nur auf einem einzigen Server. Fiele die
    Vereinigung falsch aus, stuende das maechtigste Serverrecht entweder
    ueberall oder nirgends im Dialog.
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
    """Der Owner haelt alles ohne eine einzige Zeile in der Datenbank."""
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

    Die Grenze steht bewusst grosszuegig und schreibt keine Zaehlung fest. Sie
    faengt den einen Fehler ab, der hier zurueckfallen kann: eine Schleife, die
    je Server und je Schluessel erneut fragt. Gemessen waren das 1682 Abfragen
    bei dreissig Servern.
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
        f"{len(gesehen)} Abfragen fuer zwoelf Server:\n" + "\n".join(gesehen)
    )
