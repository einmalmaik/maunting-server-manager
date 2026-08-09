"""Teams anlegen, besetzen und mit Servern verbinden.

Die Rechtepruefung selbst steht bewusst nicht hier, sondern in
`permission_service`. Dieser Dienst schreibt nur den *Wunsch* des Gruenders;
ob er wirkt, entscheidet die Pruefung bei jedem Zugriff neu. Beides zu trennen
ist der Grund, warum ein entzogenes Recht nicht nachgepflegt werden muss.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Team, TeamMember, TeamServerGrant, User
from services import audit_service, permission_service


MAX_TEAMS_PER_OWNER = 20
MAX_MEMBERS_PER_TEAM = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def personal_team(db: Session, user: User) -> Team:
    """Das Ein-Mann-Team des Benutzers, bei Bedarf angelegt.

    Es entsteht erst, wenn es gebraucht wird — nicht bei der Registrierung.
    Damit bekommt kein Bestandsbenutzer eine Migration, die Zeilen fuer Konten
    anlegt, die die KI nie benutzen werden.

    Der Zweck ist ein sprachlicher und zugleich ein struktureller: Skills gibt
    es ausschliesslich teambezogen. Ohne dieses Team haette ein Benutzer ohne
    Kollegen keinen Ort, an dem die KI etwas lernen koennte.
    """
    existing = db.query(Team).filter(Team.personal_for_user_id == user.id).first()
    if existing is not None:
        return existing

    team = Team(
        name=user.username,
        owner_user_id=user.id,
        personal_for_user_id=user.id,
    )
    db.add(team)
    try:
        db.flush()
    except IntegrityError:
        # Zwei gleichzeitige Anfragen desselben Benutzers. Die UNIQUE-Bedingung
        # auf `personal_for_user_id` hat den Verlierer abgewiesen — das Team des
        # Gewinners ist genauso gut.
        db.rollback()
        found = db.query(Team).filter(Team.personal_for_user_id == user.id).first()
        if found is None:
            raise
        return found

    db.add(TeamMember(
        team_id=team.id, user_id=user.id, role="owner",
        can_manage_skills=True, can_manage_memory=True, added_by=user.id,
    ))
    db.flush()
    return team


def user_team_ids(db: Session, user: User) -> list[int]:
    """Alle Teams, in denen der Benutzer Mitglied ist — persoenliche und echte.

    Grundlage fuer das Lesen von Team-Wissen. Bewusst ohne das persoenliche
    Team automatisch anzulegen: Lesen darf nichts schreiben, sonst erzeugt eine
    einzige Chatanfrage ohne Zutun des Benutzers eine Zeile.
    """
    rows = db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id).all()
    return [row[0] for row in rows]


def list_user_teams(db: Session, user: User) -> list[Team]:
    ids = user_team_ids(db, user)
    if not ids:
        return []
    return db.query(Team).filter(Team.id.in_(ids)).order_by(Team.name).all()


def membership(db: Session, team_id: int, user_id: int) -> TeamMember | None:
    return (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        .first()
    )


def get_team_for_member(db: Session, team_id: int, user: User) -> Team:
    """Laedt ein Team, das der Benutzer sehen darf.

    Ein Nichtmitglied bekommt 404 statt 403: ob es ein Team mit dieser Nummer
    ueberhaupt gibt, geht ihn nichts an.
    """
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team nicht gefunden")
    if membership(db, team_id, user.id) is None and not user.is_owner:
        raise HTTPException(status_code=404, detail="Team nicht gefunden")
    return team


def assert_team_owner(db: Session, team: Team, user: User) -> None:
    if user.is_owner:
        return
    member = membership(db, team.id, user.id)
    if member is None or member.role != "owner":
        raise HTTPException(status_code=403, detail="Nur der Teamgruender darf das")


def create_team(db: Session, *, user: User, name: str) -> Team:
    """Legt ein echtes Team an. Der Gruender ist automatisch Mitglied.

    Er bekommt beide Schalter: wer ein Team gruendet, soll dessen Wissen auch
    pflegen koennen, ohne sich das erst selbst zu erlauben.
    """
    if not permission_service.has_global_permission(db, user, "teams.create"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    safe_name = name.strip()
    if not 2 <= len(safe_name) <= 64:
        raise HTTPException(status_code=422, detail="Teamname ist ungueltig")

    owned = (
        db.query(Team)
        .filter(Team.owner_user_id == user.id, Team.personal_for_user_id.is_(None))
        .count()
    )
    if owned >= MAX_TEAMS_PER_OWNER:
        raise HTTPException(status_code=409, detail="Zu viele Teams")

    team = Team(name=safe_name, owner_user_id=user.id, personal_for_user_id=None)
    db.add(team)
    db.flush()
    db.add(TeamMember(
        team_id=team.id, user_id=user.id, role="owner",
        can_manage_skills=True, can_manage_memory=True, added_by=user.id,
    ))
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.created", target_type="team",
        target_id=team.id, details={"name": safe_name}, origin="direct",
    )
    db.commit()
    db.refresh(team)
    return team


def rename_team(db: Session, *, team: Team, user: User, name: str) -> Team:
    assert_team_owner(db, team, user)
    if team.is_personal:
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt wie es ist")
    safe_name = name.strip()
    if not 2 <= len(safe_name) <= 64:
        raise HTTPException(status_code=422, detail="Teamname ist ungueltig")
    team.name = safe_name
    team.updated_at = _now()
    db.commit()
    db.refresh(team)
    return team


def delete_team(db: Session, *, team: Team, user: User) -> None:
    assert_team_owner(db, team, user)
    if team.is_personal:
        # Ohne persoenliches Team haette das Lernen keinen Ort mehr. Es ist
        # kein Besitz, den man aufgeben kann, sondern Teil des Kontos.
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt bestehen")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.deleted", target_type="team",
        target_id=team.id, details={"name": team.name}, origin="direct",
    )
    db.delete(team)
    db.commit()


def add_member(
    db: Session, *, team: Team, user: User, new_user_id: int,
    can_manage_skills: bool, can_manage_memory: bool,
) -> TeamMember:
    assert_team_owner(db, team, user)
    if team.is_personal:
        # Das persoenliche Team ist per Definition eines. Wer teilen will,
        # gruendet ein echtes — sonst waere "persoenlich" nur noch ein Name.
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt allein")
    target = db.get(User, new_user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if membership(db, team.id, new_user_id) is not None:
        raise HTTPException(status_code=409, detail="Benutzer ist bereits im Team")
    if db.query(TeamMember).filter(TeamMember.team_id == team.id).count() >= MAX_MEMBERS_PER_TEAM:
        raise HTTPException(status_code=409, detail="Team ist voll")

    member = TeamMember(
        team_id=team.id, user_id=new_user_id, role="member",
        can_manage_skills=can_manage_skills, can_manage_memory=can_manage_memory,
        added_by=user.id,
    )
    db.add(member)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.added", target_type="team",
        target_id=team.id,
        details={"member_user_id": new_user_id}, origin="direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Benutzer ist bereits im Team") from exc
    db.refresh(member)
    return member


def update_member(
    db: Session, *, team: Team, user: User, member_user_id: int,
    can_manage_skills: bool, can_manage_memory: bool,
) -> TeamMember:
    assert_team_owner(db, team, user)
    member = membership(db, team.id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")
    member.can_manage_skills = can_manage_skills
    member.can_manage_memory = can_manage_memory
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.updated", target_type="team",
        target_id=team.id,
        details={
            "member_user_id": member_user_id,
            "can_manage_skills": can_manage_skills,
            "can_manage_memory": can_manage_memory,
        },
        origin="direct",
    )
    db.commit()
    db.refresh(member)
    return member


def remove_member(db: Session, *, team: Team, user: User, member_user_id: int) -> None:
    assert_team_owner(db, team, user)
    if member_user_id == team.owner_user_id:
        raise HTTPException(status_code=409, detail="Der Gruender bleibt im Team")
    member = membership(db, team.id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.removed", target_type="team",
        target_id=team.id, details={"member_user_id": member_user_id},
        origin="direct",
    )
    db.delete(member)
    db.commit()


def set_server_grants(
    db: Session, *, team: Team, user: User, server_id: int, keys: list[str],
) -> list[str]:
    """Legt fest, welche Rechte das Team auf einem Server weitergeben soll.

    Hier steht die **zweite** Sperre gegen Rechteausweitung. Die erste ist die
    Pruefung zur Laufzeit in `permission_service`; sie allein wuerde genuegen.
    Diese hier existiert, damit ein Gruender nicht stillschweigend eine Zeile
    anlegen kann, die spaeter wirksam wuerde, sobald er das Recht selbst
    erhaelt — und damit die Oberflaeche sofort sagt, was nicht geht.

    Geprueft wird gegen `direct_server_permission`, nicht gegen
    `has_server_permission`: was jemand selbst nur ueber ein Team hat, darf er
    nicht weiterreichen.
    """
    from services.permission_catalog import SERVER_KEYS

    assert_team_owner(db, team, user)
    if team.is_personal:
        raise HTTPException(status_code=409, detail="Das persoenliche Team teilt keine Server")

    desired = {key for key in keys if key in SERVER_KEYS}
    for key in sorted(desired):
        if not permission_service.direct_server_permission(db, user, server_id, key):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Du haelst \"{key}\" auf diesem Server nicht selbst und "
                    "kannst es deshalb nicht an das Team weitergeben."
                ),
            )
    # `server.view` ist die Voraussetzung fuer alles Weitere. Ohne sie sieht das
    # Mitglied den Server nicht einmal in der Liste, haette aber Rechte auf ihm
    # — ein Zustand, den die Oberflaeche nicht darstellen kann.
    if desired and "server.view" not in desired:
        raise HTTPException(
            status_code=422,
            detail="Ohne \"server.view\" ergeben die uebrigen Rechte keinen Sinn",
        )

    existing = (
        db.query(TeamServerGrant)
        .filter(TeamServerGrant.team_id == team.id, TeamServerGrant.server_id == server_id)
        .all()
    )
    existing_by_key = {row.permission_key: row for row in existing}
    for key, row in existing_by_key.items():
        if key not in desired:
            db.delete(row)
    for key in desired:
        if key not in existing_by_key:
            db.add(TeamServerGrant(
                team_id=team.id, server_id=server_id, permission_key=key,
                granted_by=user.id,
            ))
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.server.grants.set", target_type="team",
        target_id=team.id,
        details={"server_id": server_id, "keys": sorted(desired)}, origin="direct",
    )
    db.commit()
    return sorted(desired)


def team_server_keys(db: Session, team_id: int, server_id: int) -> list[str]:
    rows = (
        db.query(TeamServerGrant.permission_key)
        .filter(TeamServerGrant.team_id == team_id, TeamServerGrant.server_id == server_id)
        .all()
    )
    return sorted(row[0] for row in rows)


def team_server_ids(db: Session, team_id: int) -> list[int]:
    rows = (
        db.query(TeamServerGrant.server_id)
        .filter(TeamServerGrant.team_id == team_id)
        .distinct()
        .all()
    )
    return sorted(row[0] for row in rows)


def learning_team(db: Session, user: User) -> tuple[Team | None, str | None]:
    """Wohin die KI schreibt, wenn sie etwas fuer das Team lernt.

    Gibt entweder ein Team zurueck oder einen Grund, warum die KI nachfragen
    muss. Die Regel folgt dem Alltag statt einer Einstellung:

    - genau ein echtes Team, in dem der Benutzer verwalten darf → dorthin
    - keines → das persoenliche Team, damit ueberhaupt gelernt wird
    - mehrere → Rueckfrage, denn eine falsche Wahl waere hier nicht folgenlos:
      Wissen landete bei Kollegen, die es nichts angeht

    Der zweite Rueckgabewert ist bewusst Text fuer das Modell, nicht ein Code:
    er landet direkt im Werkzeugergebnis und soll dort verstanden werden.
    """
    memberships = (
        db.query(TeamMember, Team)
        .join(Team, Team.id == TeamMember.team_id)
        .filter(
            TeamMember.user_id == user.id,
            Team.personal_for_user_id.is_(None),
            TeamMember.can_manage_skills.is_(True),
        )
        .all()
    )
    if len(memberships) == 1:
        return memberships[0][1], None
    if len(memberships) > 1:
        names = ", ".join(sorted(team.name for _member, team in memberships))
        return None, (
            "Der Benutzer ist in mehreren Teams: "
            f"{names}. Frage nach, in welchem Team der Skill gelten soll."
        )
    return personal_team(db, user), None


def can_manage_team_skills(db: Session, user: User, team_id: int) -> bool:
    member = membership(db, team_id, user.id)
    return member is not None and member.can_manage_skills


def can_manage_team_memory(db: Session, user: User, team_id: int) -> bool:
    member = membership(db, team_id, user.id)
    return member is not None and member.can_manage_memory
