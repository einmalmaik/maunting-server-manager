"""Zentrale Permission-Pruefung.

Reihenfolge:
1. Owner-Bypass (is_owner=True) -> alles erlaubt. Bootstrap-Safe.
2. Globale Rolle hat den Key (gilt auch fuer server-scoped Keys = pauschal alle Server).
3. Per-Server-Delegation (nur fuer server-scoped Keys, wenn server_id gegeben).
4. Ueber ein Team — aber nur bis zur Obergrenze der *direkten* Rechte des
   Teamgruenders. Siehe `_team_server_permission`.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import (
    RolePermission,
    Server,
    ServerPermission,
    Team,
    TeamMember,
    TeamServerGrant,
    User,
)
from services.role_service import (
    effective_user_role_ids,
    effective_user_role_permission_keys,
)


def has_global_permission(db: Session, user: User, key: str) -> bool:
    if user.is_owner:
        return True
    role_ids = effective_user_role_ids(db, user)
    if not role_ids:
        return False
    exists = (
        db.query(RolePermission.id)
        .filter(RolePermission.role_id.in_(role_ids), RolePermission.permission_key == key)
        .first()
    )
    return exists is not None


def direct_server_permission(db: Session, user: User, server_id: int, key: str) -> bool:
    """Rechte **ohne** Teams: Owner, globale Rolle, Per-Server-Delegation.

    Diese Funktion ist die Abbruchbedingung der Rechtekette. Sie fragt bewusst
    keine Teams ab, und genau deshalb kann es keine Weitergabe ueber mehrere
    Teams hinweg geben: ein Team darf nur reichen, was sein Gruender *selbst*
    haelt — nicht, was ihm seinerseits ein anderes Team geliehen hat.

    Ohne diese Trennung waere zweierlei moeglich: eine Endlosschleife (Team A
    fragt B fragt A) und eine Rechte-Waescherei ueber eine Kette von Teams, an
    deren Ende niemand mehr sagen koennte, woher ein Recht eigentlich stammt.
    """
    if user.is_owner:
        return True
    # Pauschale Rolle (z.B. admin oder Custom-Rolle mit server.* Keys)
    role_ids = effective_user_role_ids(db, user)
    if role_ids:
        role_grant = (
            db.query(RolePermission.id)
            .filter(RolePermission.role_id.in_(role_ids), RolePermission.permission_key == key)
            .first()
        )
        if role_grant is not None:
            return True
    # Per-Server-Delegation
    delegated = (
        db.query(ServerPermission.id)
        .filter(
            ServerPermission.user_id == user.id,
            ServerPermission.server_id == server_id,
            ServerPermission.permission_key == key,
        )
        .first()
    )
    return delegated is not None


def _team_server_permission(db: Session, user: User, server_id: int, key: str) -> bool:
    """Rechte ueber ein Team — gedeckelt durch die direkten Rechte des Gruenders.

    Der Eintrag in `team_server_grants` ist nur der Wunsch des Gruenders. Ob er
    wirkt, wird hier bei **jeder** Pruefung neu entschieden, indem nachgesehen
    wird, ob der Gruender den Key auf diesem Server direkt haelt.

    Das hat drei Folgen, die alle erwuenscht sind:

    - Rechteausweitung ist unmoeglich. Wer ein Team gruendet, sich selbst
      eintraegt und `server.console.exec` auf einem fremden Server vergibt,
      gewinnt nichts: die Obergrenze ist seine eigene Berechtigung.
    - Es heilt sich selbst. Verliert der Gruender den Zugriff, verfaellt die
      Weitergabe im selben Moment — ohne Aufraeumjob und ohne Zeilen, die
      laenger gelten als ihre Grundlage.
    - Es kostet eine zusaetzliche Abfrage, aber nur dann, wenn die direkte
      Pruefung bereits gescheitert ist. Fuer Owner und Rolleninhaber aendert
      sich nichts.
    """
    owner_ids = (
        db.query(Team.owner_user_id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .join(TeamServerGrant, TeamServerGrant.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.id,
            TeamServerGrant.server_id == server_id,
            TeamServerGrant.permission_key == key,
        )
        .distinct()
        .all()
    )
    for (owner_user_id,) in owner_ids:
        # Ein Gruender, der sich selbst ueber sein eigenes Team bedient, waere
        # eine Schleife ohne Erkenntnisgewinn — sein direkter Anspruch wurde
        # oben bereits geprueft.
        if owner_user_id == user.id:
            continue
        owner = db.get(User, owner_user_id)
        if owner is not None and direct_server_permission(db, owner, server_id, key):
            return True
    return False


def has_server_permission(db: Session, user: User, server_id: int, key: str) -> bool:
    if direct_server_permission(db, user, server_id, key):
        return True
    return _team_server_permission(db, user, server_id, key)


def has_permission_anywhere(db: Session, user: User, key: str) -> bool:
    """Haelt der Benutzer dieses Recht **irgendwo** — global oder auf irgendeinem Server?

    Gebraucht wird das an genau einer Stelle: beim Zusammenstellen des
    Werkzeugkatalogs fuer die KI. Dort gibt es noch keinen Server, ueber den
    geurteilt werden koennte — das Modell waehlt ihn erst im Argument des
    Aufrufs. Die Frage lautet deshalb nicht "darf er es hier", sondern "kann er
    es ueberhaupt".

    **Das ist bewusst die grosszuegigere Frage.** Sie entscheidet nur, was
    angeboten wird. Ob ein Aufruf laeuft, entscheidet weiterhin
    `has_server_permission` am konkreten Server — unveraendert und als einzige
    Wahrheit. Eine Verwechslung der beiden waere eine Rechteausweitung: wer auf
    Server A schreiben darf, duerfte es sonst auch auf B.

    Dieselbe Reihenfolge wie in `has_server_permission`, nur mengenweise:
    Owner, pauschale Rolle, Per-Server-Delegation, Team — und beim Team
    derselbe Deckel, das direkte Recht des Gruenders.
    """
    if has_global_permission(db, user, key):
        # Deckt Owner und die pauschale Rolle ab. Eine Rolle mit einem
        # server-scoped Key gilt fuer alle Server (siehe Kopf dieser Datei).
        return True
    delegiert = (
        db.query(ServerPermission.id)
        .filter(
            ServerPermission.user_id == user.id,
            ServerPermission.permission_key == key,
        )
        .first()
    )
    if delegiert is not None:
        return True
    rows = (
        db.query(TeamServerGrant.server_id, Team.owner_user_id)
        .join(Team, Team.id == TeamServerGrant.team_id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.id,
            TeamServerGrant.permission_key == key,
        )
        .distinct()
        .all()
    )
    for server_id, owner_user_id in rows:
        if owner_user_id == user.id:
            continue
        owner = db.get(User, owner_user_id)
        if owner is not None and direct_server_permission(db, owner, server_id, key):
            return True
    return False


def _direkte_rechte(
    db: Session, user: User, schluessel: set[str]
) -> tuple[set[str], set[tuple[int, str]]]:
    """Die direkten Rechte eines Benutzers, mengenweise — genau wie
    `direct_server_permission`, nur fuer viele Schluessel auf einmal.

    Zurueck kommen zwei Dinge, weil die Rechtekette zwei Arten von Anspruch
    kennt: **pauschal** (Owner oder eine Rolle mit dem Key — gilt auf allen
    Servern) und **je Server** (die Delegation in `server_permissions`). Beides
    in einen Topf zu werfen waere hier falsch: der Gruenderdeckel unten fragt
    nach einem Recht auf einem *bestimmten* Server, und ein pauschales Recht
    beantwortet diese Frage anders als ein delegiertes.

    Kosten: zwei Abfragen fuer die Rollen, eine fuer die Delegationen; beim
    Owner keine einzige.
    """
    if user.is_owner:
        return set(schluessel), set()
    if not schluessel:
        return set(), set()
    pauschal = set(effective_user_role_permission_keys(db, user)) & schluessel
    offen = schluessel - pauschal
    if not offen:
        return pauschal, set()
    rows = (
        db.query(ServerPermission.server_id, ServerPermission.permission_key)
        .filter(
            ServerPermission.user_id == user.id,
            ServerPermission.permission_key.in_(offen),
        )
        .distinct()
        .all()
    )
    return pauschal, {(server_id, key) for server_id, key in rows}


def rechte_irgendwo(db: Session, user: User, schluessel: set[str]) -> set[str]:
    """Welche dieser Rechte haelt der Benutzer **irgendwo** — global oder auf
    irgendeinem Server?

    Dieselbe Frage wie `has_permission_anywhere`, nur mengenweise gestellt. Die
    dortige Beschreibung gilt unveraendert, samt der Warnung: das ist bewusst
    die grosszuegigere Frage, sie entscheidet nur, was *angeboten* wird. Ob ein
    Aufruf laeuft, entscheidet weiterhin `has_server_permission` am konkreten
    Server.

    Der Anlass war gemessen, nicht vermutet: der Werkzeugkatalog der KI fragt
    24 verschiedene Rechteschluessel ab, und die Schleife ueber
    `has_permission_anywhere` kostete dabei 73 Abfragen bei einem gewoehnlichen
    Kunden und 93 bei einem Rolleninhaber — je Schluessel dreimal dieselbe
    Frage nach den Rollen des Benutzers. Der Aufruf sitzt auf dem Pfad zum
    ersten Token, dort zaehlt jede Runde zur Wartezeit.

    `has_permission_anywhere` bleibt bestehen; wer genau ein Recht wissen will,
    soll nicht erst eine Menge bauen muessen.
    """
    if not schluessel:
        return set()
    pauschal, delegiert = _direkte_rechte(db, user, schluessel)
    gefunden = pauschal | {key for _, key in delegiert}
    offen = schluessel - gefunden
    if not offen:
        return gefunden
    # Der Teamweg wird nur betreten, wenn ueberhaupt etwas offen ist — und er
    # kostet nur dann etwas, wenn es auch Zeilen gibt.
    rows = (
        db.query(
            TeamServerGrant.server_id,
            TeamServerGrant.permission_key,
            Team.owner_user_id,
        )
        .join(Team, Team.id == TeamServerGrant.team_id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.id,
            TeamServerGrant.permission_key.in_(offen),
        )
        .distinct()
        .all()
    )
    # Je Gruender einmal nachsehen, was er selbst haelt. Bei mehreren Servern
    # oder Schluesseln desselben Teams waere das sonst eine Abfrage pro Zeile —
    # genau der Fehler, den diese Funktion behebt.
    gruender_rechte: dict[int, tuple[set[str], set[tuple[int, str]]]] = {}
    for server_id, key, owner_user_id in rows:
        if key in gefunden:
            continue
        # Ein Gruender, der sich selbst ueber sein eigenes Team bedient, waere
        # eine Schleife ohne Erkenntnisgewinn — sein direkter Anspruch steht
        # oben schon in `pauschal`/`delegiert`.
        if owner_user_id == user.id:
            continue
        if owner_user_id not in gruender_rechte:
            gruender = db.get(User, owner_user_id)
            gruender_rechte[owner_user_id] = (
                _direkte_rechte(db, gruender, offen) if gruender is not None
                else (set(), set())
            )
        g_pauschal, g_delegiert = gruender_rechte[owner_user_id]
        if key in g_pauschal or (server_id, key) in g_delegiert:
            gefunden.add(key)
    return gefunden


def _team_visible_server_ids(db: Session, user: User) -> set[int]:
    """Server, die dieser Benutzer ueber ein Team sehen darf.

    Dieselbe Obergrenze wie in `_team_server_permission`, nur mengenweise. Ohne
    diese Ergaenzung saehe ein Teammitglied den Server im Detail (weil dort
    `has_server_permission` prueft), aber nicht in der Liste — ein Widerspruch,
    den die bestehende Delegation bewusst vermeidet.
    """
    rows = (
        db.query(TeamServerGrant.server_id, Team.owner_user_id)
        .join(Team, Team.id == TeamServerGrant.team_id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.id,
            TeamServerGrant.permission_key == "server.view",
        )
        .distinct()
        .all()
    )
    # Je Gruender nur einmal laden: bei mehreren Servern desselben Teams waere
    # das sonst eine Abfrage pro Server.
    owners: dict[int, User | None] = {}
    visible: set[int] = set()
    for server_id, owner_user_id in rows:
        if owner_user_id == user.id:
            continue
        if owner_user_id not in owners:
            owners[owner_user_id] = db.get(User, owner_user_id)
        owner = owners[owner_user_id]
        if owner is not None and direct_server_permission(db, owner, server_id, "server.view"):
            visible.add(server_id)
    return visible


def list_visible_server_ids(db: Session, user: User) -> list[int] | None:
    """Server-IDs, die der User sehen darf. None = alle (Owner/pauschale Rolle)."""
    if user.is_owner:
        return None
    role_ids = effective_user_role_ids(db, user)
    if role_ids:
        pauschal = (
            db.query(RolePermission.id)
            .filter(
                RolePermission.role_id.in_(role_ids),
                RolePermission.permission_key == "server.view",
            )
            .first()
        )
        if pauschal is not None:
            return None
    # Nur Server, fuer die explizit `server.view` delegiert wurde, sind sichtbar.
    # So bleibt die Liste konsistent mit dem Detail-Endpoint (der ebenfalls
    # `server.view` prueft) — kein "sehe Server im Listing, kriege aber 403 im Detail".
    rows = (
        db.query(ServerPermission.server_id)
        .filter(
            ServerPermission.user_id == user.id,
            ServerPermission.permission_key == "server.view",
        )
        .distinct()
        .all()
    )
    visible = {r[0] for r in rows} | _team_visible_server_ids(db, user)
    # Sortiert, damit die Reihenfolge nicht von der Mengenimplementierung
    # abhaengt — Tests und Oberflaeche sollen dasselbe sehen.
    return sorted(visible)


def list_visible_servers(db: Session, user: User) -> list[Server]:
    ids = list_visible_server_ids(db, user)
    if ids is None:
        return db.query(Server).all()
    if not ids:
        return []
    return db.query(Server).filter(Server.id.in_(ids)).all()


def list_user_server_permission_keys(
    db: Session, user_id: int, server_id: int
) -> list[str]:
    rows = (
        db.query(ServerPermission.permission_key)
        .filter(ServerPermission.user_id == user_id, ServerPermission.server_id == server_id)
        .all()
    )
    return [r[0] for r in rows]


def set_user_server_permissions(
    db: Session,
    user_id: int,
    server_id: int,
    keys: list[str],
    granted_by: int | None,
) -> list[str]:
    """Idempotent: ueberschreibt alle Server-Permissions des Users fuer diesen Server.

    Unbekannte Keys werden ignoriert (Whitelist via catalog).
    """
    from services.permission_catalog import SERVER_KEYS

    desired = {k for k in keys if k in SERVER_KEYS}

    existing = (
        db.query(ServerPermission)
        .filter(ServerPermission.user_id == user_id, ServerPermission.server_id == server_id)
        .all()
    )
    existing_by_key = {p.permission_key: p for p in existing}

    # Entfernen, was nicht mehr gewollt ist
    for key, perm in existing_by_key.items():
        if key not in desired:
            db.delete(perm)

    # Hinzufuegen, was neu ist
    for key in desired:
        if key not in existing_by_key:
            db.add(
                ServerPermission(
                    user_id=user_id,
                    server_id=server_id,
                    permission_key=key,
                    granted_by=granted_by,
                )
            )

    db.commit()
    return sorted(desired)


def list_user_effective_global_keys(db: Session, user: User) -> list[str]:
    """Globale Keys aller Rollen des Users (ohne Owner-Bypass auflisten)."""
    role_ids = effective_user_role_ids(db, user)
    if not role_ids:
        return []
    rows = (
        db.query(RolePermission.permission_key)
        .filter(RolePermission.role_id.in_(role_ids))
        .all()
    )
    return sorted({r[0] for r in rows})
