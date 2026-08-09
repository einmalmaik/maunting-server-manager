"""API-Vertraege fuer Teams, Mitglieder und weitergegebene Serverrechte."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)


class TeamRename(BaseModel):
    name: str = Field(min_length=2, max_length=64)


class TeamMemberWrite(BaseModel):
    user_id: int = Field(ge=1)
    can_manage_skills: bool = False
    can_manage_memory: bool = False


class TeamMemberUpdate(BaseModel):
    can_manage_skills: bool
    can_manage_memory: bool


class TeamServerGrantWrite(BaseModel):
    server_id: int = Field(ge=1)
    # Leere Liste heisst: der Server wird dem Team wieder entzogen.
    permission_keys: list[str] = Field(default_factory=list, max_length=64)


class TeamMemberResponse(BaseModel):
    user_id: int
    username: str
    role: Literal["owner", "member"]
    can_manage_skills: bool
    can_manage_memory: bool
    joined_at: datetime


class TeamServerResponse(BaseModel):
    server_id: int
    server_name: str
    permission_keys: list[str]


class TeamResponse(BaseModel):
    id: int
    name: str
    is_personal: bool
    owner_user_id: int
    # Ob der Abrufende dieses Team verwalten darf. Spart der Oberflaeche eine
    # zweite Abfrage und haelt die Bedingung an einer Stelle.
    is_owner: bool
    can_manage_skills: bool
    can_manage_memory: bool
    member_count: int
    created_at: datetime


class TeamDetailResponse(TeamResponse):
    members: list[TeamMemberResponse]
    servers: list[TeamServerResponse]
