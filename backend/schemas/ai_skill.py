"""Typisierte Skill-Vertraege ohne freie Programme oder Skripte."""

from datetime import datetime

from pydantic import BaseModel, Field


class AiSkillStep(BaseModel):
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict = Field(default_factory=dict)


class AiSkillWrite(BaseModel):
    skill_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    steps: list[AiSkillStep] = Field(min_length=1, max_length=20)
    enabled: bool = True


class AiSkillResponse(BaseModel):
    id: str
    skill_key: str
    version: int
    name: str
    description: str
    steps: list[AiSkillStep]
    enabled: bool
    created_by: int | None
    created_at: datetime


class AiSkillRunRequest(BaseModel):
    # Ein Skill ist ein Ablauf, kein Serverbezug. Der Server wird beim Start
    # gewaehlt und anschliessend gegen die Rechte des Benutzers geprueft.
    server_id: int = Field(ge=1)


class AiSkillRunResponse(BaseModel):
    skill_id: str
    version: int
    read_results: list[dict]
    proposals: list[dict]
