"""Die Mengenabfrage muss dieselbe Antwort geben wie die Schleife.

`rechte_irgendwo` ist aus einem Tempogrund entstanden: der KI-Werkzeugkatalog
fragte 24 Rechteschluessel einzeln ueber `has_permission_anywhere` ab und
kostete dabei 73 bis 93 SQL-Abfragen — je Schluessel dreimal dieselbe Frage
nach den Rollen des Benutzers, und das am Beginn jedes Segments, also auf dem
Pfad zum ersten Token.

**Ein schnellerer Weg mit anderem Ergebnis waere eine stille Rechteaenderung.**
Deshalb prueft diese Datei nicht, was die neue Funktion liefert, sondern dass
sie dasselbe liefert wie die alte: fuer jede Benutzerart wird die Schleife ueber
`has_permission_anywhere` gegen die Mengenabfrage gerechnet. Wer den einen Weg
aendert und den anderen vergisst, faellt hier auf.

Besonders beachtet werden die drei Stellen, an denen sich die beiden Wege
unterscheiden koennten: der Owner-Bypass, der Gruenderdeckel bei Teams — ein
Team darf nie mehr weitergeben, als sein Gruender selbst haelt — und dass ein
Recht auf **irgendeinem** Server zaehlt. Genau diese Frage stellt der Katalog.
"""

from __future__ import annotations

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
from services import permission_service
from services.ai_tool_registry import WERKZEUGE, angebotsrechte
from services.auth_service import AuthService
from services.role_service import set_user_roles

#: Genau die Schluessel, die der Werkzeugkatalog abfragt — der Anlass fuer die
#: Funktion. Zusaetzlich ein Schluessel, den niemand haelt, damit auch der
#: negative Fall in jedem Vergleich steckt.
KATALOG_KEYS = {key for name in WERKZEUGE for key in angebotsrechte(name)} | {
    "gibt.es.nicht"
}


def _vergleiche(db: Session, user: User, schluessel: set[str]) -> set[str]:
    """Rechnet beide Wege gegeneinander und gibt die gemeinsame Antwort zurueck."""
    alt = {
        key for key in schluessel
        if permission_service.has_permission_anywhere(db, user, key)
    }
    neu = permission_service.rechte_irgendwo(db, user, schluessel)
    assert neu == alt, (
        f"Mengenabfrage weicht ab fuer {user.username}: "
        f"zuviel={sorted(neu - alt)} zuwenig={sorted(alt - neu)}"
    )
    return neu


# ── Hilfen ────────────────────────────────────────────────────────────

def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "MengePass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


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


def _rolle_mit(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"rolle-{user.username}", description=None, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _delegiere(db: Session, user: User, server: Server, *keys: str) -> None:
    for key in keys:
        db.add(
            ServerPermission(user_id=user.id, server_id=server.id, permission_key=key)
        )
    db.commit()


def _team(db: Session, gruender: User, mitglied: User, name: str) -> Team:
    team = Team(name=name, owner_user_id=gruender.id)
    db.add(team)
    db.commit()
    db.refresh(team)
    db.add(TeamMember(team_id=team.id, user_id=gruender.id))
    if mitglied.id != gruender.id:
        db.add(TeamMember(team_id=team.id, user_id=mitglied.id))
    db.commit()
    return team


def _grant(db: Session, team: Team, server: Server, *keys: str) -> None:
    """Traegt den Weitergabewunsch **roh** ein, an `set_server_grants` vorbei.

    Absicht: die Oberflaeche laesst einen Gruender gar nicht erst weitergeben,
    was er nicht haelt. Der Deckel zur Laufzeit ist die zweite, eigentliche
    Sperre — und nur sie kann hier gemessen werden. Roh eingetragene Zeilen
    sind ausserdem der realistische Fall nach einem Rechteentzug.
    """
    for key in keys:
        db.add(
            TeamServerGrant(team_id=team.id, server_id=server.id, permission_key=key)
        )
    db.commit()


# ── Der Vergleich, Benutzerart fuer Benutzerart ───────────────────────

def test_both_paths_agree_for_a_user_without_any_rights(
    db: Session, regular_user: User
) -> None:
    """Ein Kunde ohne alles — die Menge muss leer sein, auf beiden Wegen."""
    assert _vergleiche(db, regular_user, KATALOG_KEYS) == set()


def test_both_paths_agree_for_the_owner(db: Session, owner_user: User) -> None:
    """Der Owner-Bypass gilt pauschal: alles, ohne eine einzige Zeile in der DB.

    Die Mengenabfrage darf hier nicht heimlich strenger werden — der Owner haelt
    auch Schluessel, die in keiner Rolle stehen, und selbst einen erfundenen.
    """
    gefunden = _vergleiche(db, owner_user, KATALOG_KEYS)
    assert gefunden == KATALOG_KEYS


def test_both_paths_agree_for_a_role_holder(db: Session, regular_user: User) -> None:
    """Eine globale Rolle gilt pauschal — auch fuer server-scoped Schluessel."""
    _rolle_mit(
        db, regular_user,
        "server.files.read", "server.start", "server.stop", "nodes.read",
    )
    gefunden = _vergleiche(db, regular_user, KATALOG_KEYS)
    assert gefunden == {
        "server.files.read", "server.start", "server.stop", "nodes.read",
    }


def test_both_paths_agree_for_a_delegation_on_a_single_server(
    db: Session, regular_user: User
) -> None:
    """Ein Recht auf **irgendeinem** Server zaehlt — das ist die gestellte Frage.

    Der Benutzer darf auf genau einem von zwei Servern lesen. Fuer den Katalog
    genuegt das; ob er es auf dem gewaehlten Server darf, entscheidet spaeter
    `has_server_permission` am konkreten Server.
    """
    einer = _server(db, "einer")
    _server(db, "anderer")
    _delegiere(db, regular_user, einer, "server.files.read", "server.console.read")

    gefunden = _vergleiche(db, regular_user, KATALOG_KEYS)
    assert gefunden == {"server.files.read", "server.console.read"}


def test_both_paths_agree_for_a_team_member_within_the_founder_cap(
    db: Session, regular_user: User
) -> None:
    """Der Teamweg zaehlt — soweit der Gruender das Recht selbst haelt."""
    mitglied = _user(db, "mitglied")
    server = _server(db, "geteilt")
    _delegiere(db, regular_user, server, "server.files.read", "server.start")
    team = _team(db, regular_user, mitglied, "echtes-team")
    _grant(db, team, server, "server.files.read", "server.start")

    gefunden = _vergleiche(db, mitglied, KATALOG_KEYS)
    assert gefunden == {"server.files.read", "server.start"}


def test_both_paths_agree_when_the_founder_cap_bites(
    db: Session, regular_user: User
) -> None:
    """Der Deckel: der Wunsch steht in der Tabelle, die Grundlage fehlt.

    Der Gruender haelt nur `server.files.read`, das Team gibt zusaetzlich
    `server.files.write` und `server.console.read` weiter. Beide Wege muessen
    das Ueberschiessende verwerfen — sonst waere die schnellere Abfrage eine
    Rechteausweitung in drei Zeilen.
    """
    mitglied = _user(db, "mitglied")
    server = _server(db, "geteilt")
    _delegiere(db, regular_user, server, "server.files.read")
    team = _team(db, regular_user, mitglied, "echtes-team")
    _grant(db, team, server, "server.files.read", "server.files.write", "server.console.read")

    gefunden = _vergleiche(db, mitglied, KATALOG_KEYS)
    assert gefunden == {"server.files.read"}


def test_both_paths_agree_when_the_founder_holds_the_right_on_another_server(
    db: Session, regular_user: User
) -> None:
    """Der Deckel ist serverbezogen, nicht pauschal.

    Der Gruender darf auf Server B schreiben und gibt das Recht auf Server A
    weiter. Das darf nicht wirken: die Weitergabe braucht den Anspruch **auf
    diesem** Server. Genau hier koennte eine Mengenabfrage falsch abkuerzen,
    indem sie nur nach dem Schluessel fragt und den Server vergisst.
    """
    mitglied = _user(db, "mitglied")
    server_a = _server(db, "geteilt-a")
    server_b = _server(db, "eigener-b")
    _delegiere(db, regular_user, server_a, "server.files.read")
    _delegiere(db, regular_user, server_b, "server.files.write")
    team = _team(db, regular_user, mitglied, "echtes-team")
    _grant(db, team, server_a, "server.files.read", "server.files.write")

    gefunden = _vergleiche(db, mitglied, KATALOG_KEYS)
    assert gefunden == {"server.files.read"}


def test_both_paths_agree_when_the_founder_holds_the_right_globally(
    db: Session, regular_user: User
) -> None:
    """Haelt der Gruender das Recht ueber eine Rolle, gilt es auf allen Servern."""
    mitglied = _user(db, "mitglied")
    server = _server(db, "geteilt")
    _rolle_mit(db, regular_user, "server.mods.read")
    team = _team(db, regular_user, mitglied, "echtes-team")
    _grant(db, team, server, "server.mods.read")

    gefunden = _vergleiche(db, mitglied, KATALOG_KEYS)
    assert gefunden == {"server.mods.read"}


def test_both_paths_agree_when_a_member_founds_their_own_team(
    db: Session, regular_user: User
) -> None:
    """Wer sich ueber das eigene Team bedient, gewinnt nichts.

    Beide Wege ueberspringen den Gruender, wenn er selbst der Fragende ist —
    sein direkter Anspruch wurde bereits geprueft. Ohne dieses Ueberspringen
    entstuende aus einem selbst angelegten Grant ein Recht aus dem Nichts.
    """
    server = _server(db, "fremder")
    team = _team(db, regular_user, regular_user, "eigenes-team")
    _grant(db, team, server, "server.console.read", "server.files.write")

    assert _vergleiche(db, regular_user, KATALOG_KEYS) == set()


def test_both_paths_agree_for_a_user_holding_rights_on_every_way_at_once(
    db: Session, regular_user: User
) -> None:
    """Alle drei Wege gleichzeitig — Rolle, Delegation, Team.

    Der Realfall eines gewachsenen Kontos. Die Mengenabfrage vereinigt drei
    Quellen; hier faellt auf, wenn eine davon die andere ueberschreibt statt
    sie zu ergaenzen.
    """
    gruender = _user(db, "gruender")
    server = _server(db, "geteilt")
    eigener = _server(db, "eigener")

    _rolle_mit(db, regular_user, "ai.skills.use", "nodes.read")
    _delegiere(db, regular_user, eigener, "server.backups.create")
    _delegiere(db, gruender, server, "server.console.read")
    team = _team(db, gruender, regular_user, "fremdes-team")
    _grant(db, team, server, "server.console.read", "servers.delete")

    gefunden = _vergleiche(db, regular_user, KATALOG_KEYS)
    assert gefunden == {
        "ai.skills.use", "nodes.read", "server.backups.create", "server.console.read",
    }


def test_both_paths_agree_when_the_founder_account_is_gone(
    db: Session, regular_user: User
) -> None:
    """Ein Grant eines geloeschten Gruenders traegt nichts.

    Der Fremdschluessel raeumt die Zeile normalerweise mit ab; geprueft wird
    trotzdem, weil beide Wege den fehlenden Gruender still ueberspringen
    muessen statt zu stolpern.
    """
    gruender = _user(db, "gruender")
    server = _server(db, "geteilt")
    _delegiere(db, gruender, server, "server.files.read")
    team = _team(db, gruender, regular_user, "verwaistes-team")
    _grant(db, team, server, "server.files.read")
    # Nur die Mitgliedschaft des Gruenders faellt weg, das Team bleibt stehen.
    db.query(TeamMember).filter(TeamMember.user_id == gruender.id).delete()
    db.query(ServerPermission).filter(
        ServerPermission.user_id == gruender.id
    ).delete()
    db.commit()

    assert _vergleiche(db, regular_user, KATALOG_KEYS) == set()


def test_the_empty_question_costs_nothing_and_answers_nothing(
    db: Session, owner_user: User
) -> None:
    """Eine leere Frage bleibt leer — auch beim Owner, der sonst alles haelt."""
    assert permission_service.rechte_irgendwo(db, owner_user, set()) == set()


# ── Die Ersparnis darf nicht still zurueckfallen ──────────────────────

def _abfragen_beim_katalogbau(db: Session, user: User) -> list[str]:
    """Zaehlt die SQL-Anweisungen, die ein Katalogbau ausloest."""
    from sqlalchemy import event

    import database as db_module

    gesehen: list[str] = []

    def _hook(conn, cursor, statement, parameters, context, executemany) -> None:
        gesehen.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", _hook)
    try:
        from services import ai_action_service

        ai_action_service.angebotene_werkzeuge(db, user)
    finally:
        event.remove(db_module.engine, "before_cursor_execute", _hook)
    return gesehen


def test_building_the_tool_catalogue_costs_a_handful_of_queries(
    db: Session, regular_user: User
) -> None:
    """Der Katalogbau bleibt bei einer Handvoll Abfragen, nicht bei Dutzenden.

    Gemessen mit einem Horcher, nicht geschaetzt: vorher 73 Abfragen fuer einen
    gewoehnlichen Kunden und 93 fuer einen Rolleninhaber, nachher 3 und 4.

    Die Grenze steht bewusst grosszuegig bei acht. Sie soll keine Zaehlung
    festschreiben, sondern den einen Fehler abfangen, der hier zweimal
    passieren kann: eine Schleife, die je Rechteschluessel erneut fragt. Wer
    diesen Test brechen sieht, hat vermutlich genau das gebaut.
    """
    _rolle_mit(db, regular_user, "server.files.read", "nodes.read")
    # Den Benutzer laden, bevor gezaehlt wird — sonst misst der Horcher das
    # Nachladen des Objekts mit und nicht die Rechtefrage.
    assert regular_user.id is not None

    abfragen = _abfragen_beim_katalogbau(db, regular_user)

    assert len(abfragen) <= 8, (
        f"{len(abfragen)} Abfragen fuer einen Katalogbau:\n"
        + "\n".join(abfragen)
    )
