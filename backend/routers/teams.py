"""Teams: gemeinsame KI-Wissensbasis und gebuendelte Serverrechte.

Bewusst ohne `require_global`: jeder angemeldete Benutzer darf seine eigenen
Teams sehen — auch das persoenliche, das er nie selbst angelegt hat. Erst das
*Gruenden* eines echten Teams verlangt `teams.create`, geprueft in
`team_service.create_team`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, verify_csrf
from models import Server, Team, TeamMember, User
from schemas.team import (
    TeamCreate,
    TeamDetailResponse,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamMemberWrite,
    TeamRename,
    TeamResponse,
    TeamServerGrantWrite,
    TeamServerResponse,
)
from services import permission_service, team_service


router = APIRouter(prefix="/api/teams", tags=["teams"])


def _summary(db: Session, team: Team, user: User) -> TeamResponse:
    member = team_service.membership(db, team.id, user.id)
    count = db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
    return TeamResponse(
        id=team.id,
        name=team.name,
        is_personal=team.is_personal,
        owner_user_id=team.owner_user_id,
        is_owner=user.is_owner or (member is not None and member.role == "owner"),
        can_manage_skills=member is not None and member.can_manage_skills,
        can_manage_memory=member is not None and member.can_manage_memory,
        member_count=count,
        created_at=team.created_at,
    )


def _detail(db: Session, team: Team, user: User) -> TeamDetailResponse:
    rows = (
        db.query(TeamMember, User.username)
        .join(User, User.id == TeamMember.user_id)
        .filter(TeamMember.team_id == team.id)
        .order_by(TeamMember.role.desc(), User.username)
        .all()
    )
    members = [
        TeamMemberResponse(
            user_id=member.user_id, username=username, role=member.role,
            can_manage_skills=member.can_manage_skills,
            can_manage_memory=member.can_manage_memory,
            joined_at=member.joined_at,
        )
        for member, username in rows
    ]
    servers: list[TeamServerResponse] = []
    for server_id in team_service.team_server_ids(db, team.id):
        server = db.get(Server, server_id)
        if server is None:
            continue
        servers.append(TeamServerResponse(
            server_id=server_id, server_name=server.name,
            permission_keys=team_service.team_server_keys(db, team.id, server_id),
        ))
    base = _summary(db, team, user)
    return TeamDetailResponse(**base.model_dump(), members=members, servers=servers)


@router.get("", response_model=list[TeamResponse])
def list_teams(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TeamResponse]:
    """Alle Teams des Benutzers, einschliesslich des persoenlichen.

    Das persoenliche Team wird hier bei Bedarf angelegt: Diese Liste ist die
    Stelle, an der ein Benutzer Teams zum ersten Mal bewusst ansieht — ein
    guter Zeitpunkt, ihm sein eigenes zu geben, statt es beim ersten Chat
    unbemerkt entstehen zu lassen.
    """
    team_service.personal_team(db, user)
    db.commit()
    return [_summary(db, team, user) for team in team_service.list_user_teams(db, user)]


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamResponse:
    team = team_service.create_team(db, user=user, name=payload.name)
    return _summary(db, team, user)


@router.get("/{team_id}", response_model=TeamDetailResponse)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TeamDetailResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    return _detail(db, team, user)


@router.put("/{team_id}", response_model=TeamResponse)
def rename_team(
    team_id: int,
    payload: TeamRename,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    return _summary(db, team_service.rename_team(db, team=team, user=user, name=payload.name), user)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> None:
    team = team_service.get_team_for_member(db, team_id, user)
    team_service.delete_team(db, team=team, user=user)


@router.post("/{team_id}/members", response_model=TeamDetailResponse, status_code=status.HTTP_201_CREATED)
def add_member(
    team_id: int,
    payload: TeamMemberWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamDetailResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    team_service.add_member(
        db, team=team, user=user, new_user_id=payload.user_id,
        can_manage_skills=payload.can_manage_skills,
        can_manage_memory=payload.can_manage_memory,
    )
    return _detail(db, team, user)


@router.put("/{team_id}/members/{member_user_id}", response_model=TeamDetailResponse)
def update_member(
    team_id: int,
    member_user_id: int,
    payload: TeamMemberUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamDetailResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    team_service.update_member(
        db, team=team, user=user, member_user_id=member_user_id,
        can_manage_skills=payload.can_manage_skills,
        can_manage_memory=payload.can_manage_memory,
    )
    return _detail(db, team, user)


@router.delete("/{team_id}/members/{member_user_id}", response_model=TeamDetailResponse)
def remove_member(
    team_id: int,
    member_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamDetailResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    team_service.remove_member(db, team=team, user=user, member_user_id=member_user_id)
    return _detail(db, team, user)


@router.put("/{team_id}/servers", response_model=TeamDetailResponse)
def set_server_grants(
    team_id: int,
    payload: TeamServerGrantWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> TeamDetailResponse:
    team = team_service.get_team_for_member(db, team_id, user)
    # Ob es den Server gibt, erfaehrt nur, wer ihn ohnehin sehen darf.
    if not permission_service.direct_server_permission(
        db, user, payload.server_id, "server.view"
    ):
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    team_service.set_server_grants(
        db, team=team, user=user, server_id=payload.server_id,
        keys=payload.permission_keys,
    )
    return _detail(db, team, user)


@router.get("/{team_id}/assignable-servers", response_model=list[TeamServerResponse])
def assignable_servers(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TeamServerResponse]:
    """Server, die der Gruender dem Team ueberhaupt geben koennte.

    Bewusst nur, was er **direkt** haelt — nicht, was er selbst ueber ein
    anderes Team bekommen hat. Damit zeigt die Oberflaeche dieselbe Obergrenze,
    die `permission_service` spaeter durchsetzt, statt dass der Benutzer sie
    erst beim Speichern als Fehlermeldung erlebt.
    """
    from services.permission_catalog import SERVER_KEYS

    team = team_service.get_team_for_member(db, team_id, user)
    team_service.assert_team_owner(db, team, user)

    result: list[TeamServerResponse] = []
    for server in db.query(Server).order_by(Server.name).all():
        if not permission_service.direct_server_permission(db, user, server.id, "server.view"):
            continue
        keys = sorted(
            key for key in SERVER_KEYS
            if permission_service.direct_server_permission(db, user, server.id, key)
        )
        result.append(TeamServerResponse(
            server_id=server.id, server_name=server.name, permission_keys=keys,
        ))
    return result
