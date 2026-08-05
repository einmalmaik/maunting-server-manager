"""Verwaltung und sichere Ausfuehrung versionierter AI-Skills."""

from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiSkill, User
from schemas.ai_skill import AiSkillResponse, AiSkillRunRequest, AiSkillRunResponse, AiSkillStep, AiSkillWrite
from services import ai_skill_service


router = APIRouter(prefix="/api/ai/skills", tags=["ai-skills"])


def _response(row: AiSkill) -> AiSkillResponse:
    return AiSkillResponse(
        id=row.id, skill_key=row.skill_key, version=row.version, name=row.name,
        description=row.description,
        steps=[AiSkillStep(**step) for step in ai_skill_service.response_steps(row)],
        enabled=row.enabled, created_by=row.created_by, created_at=row.created_at,
    )


@router.get("", response_model=list[AiSkillResponse])
def list_skills(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.use")),
) -> list[AiSkillResponse]:
    del user
    return [_response(row) for row in ai_skill_service.latest_skills(db, include_disabled=False)]


@router.get("/manage", response_model=list[AiSkillResponse])
def list_skills_for_management(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.manage")),
) -> list[AiSkillResponse]:
    del user
    return [_response(row) for row in ai_skill_service.latest_skills(db, include_disabled=True)]


@router.post("", response_model=AiSkillResponse, status_code=status.HTTP_201_CREATED)
def create_skill(
    payload: AiSkillWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.manage")),
    _: None = Depends(verify_csrf),
) -> AiSkillResponse:
    return _response(ai_skill_service.create_version(
        db, user=user, skill_key=payload.skill_key, name=payload.name,
        description=payload.description,
        steps=[step.model_dump() for step in payload.steps], enabled=payload.enabled,
        require_existing=False,
    ))


@router.put("/{skill_key}", response_model=AiSkillResponse)
def update_skill(
    skill_key: str,
    payload: AiSkillWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.manage")),
    _: None = Depends(verify_csrf),
) -> AiSkillResponse:
    if payload.skill_key != skill_key:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Skill-Key darf nicht geaendert werden")
    return _response(ai_skill_service.create_version(
        db, user=user, skill_key=skill_key, name=payload.name,
        description=payload.description,
        steps=[step.model_dump() for step in payload.steps], enabled=payload.enabled,
        require_existing=True,
    ))


@router.post("/{skill_id}/run", response_model=AiSkillRunResponse)
def run_skill(
    skill_id: str,
    payload: AiSkillRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.use")),
    _: None = Depends(verify_csrf),
) -> AiSkillRunResponse:
    skill = ai_skill_service.get_skill(db, skill_id)
    correlation_id = str(uuid4())
    reads, proposals = ai_skill_service.run_skill(
        db, user=user, skill=skill, conversation_id=payload.conversation_id,
        correlation_id=correlation_id,
    )
    return AiSkillRunResponse(
        skill_id=skill.id, version=skill.version,
        read_results=reads, proposals=proposals,
    )
