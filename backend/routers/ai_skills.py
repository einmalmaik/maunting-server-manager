"""Skills lesen, pflegen und freigeben.

Der Ausfuehrungsendpunkt ist entfallen. Ein Prosa-Skill wird nicht gestartet,
sondern gelesen — vom Modell im Chat, ueber das Werkzeug `read_skill`. Was
bleibt, ist Verwaltung.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import AiSkill, User
from schemas.ai_skill import (
    AiSkillDetail,
    AiSkillManaged,
    AiSkillSummary,
    AiSkillToggle,
    AiSkillWrite,
)
from services import ai_skill_service


router = APIRouter(prefix="/api/ai/skills", tags=["ai-skills"])


def _summary(view) -> AiSkillSummary:
    return AiSkillSummary(
        id=view.id, skill_key=view.skill_key, name=view.name, description=view.description,
        scope=view.scope, origin=view.origin, team_id=view.team_id, status=view.status,
        enabled=view.enabled, editable=view.editable,
    )


def _managed(row: AiSkill) -> AiSkillManaged:
    return AiSkillManaged(
        id=row.id, skill_key=row.skill_key, name=row.name, description=row.description,
        body=row.body, scope="global" if row.team_id is None else "team",
        origin=row.origin, team_id=row.team_id, status=row.status, enabled=row.enabled,
        created_by=row.created_by, created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("", response_model=list[AiSkillSummary])
def list_skills(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.use")),
) -> list[AiSkillSummary]:
    """Das Verzeichnis, das dieser Benutzer sieht — ohne die Texte.

    Dieselbe Liste, die auch in den Systemprompt geht. Wer sie hier abruft,
    sieht genau das, was die KI ueber verfuegbare Skills weiss.
    """
    return [_summary(view) for view in ai_skill_service.visible_skills(db, user)]


@router.get("/manage", response_model=list[AiSkillManaged])
def list_managed_skills(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AiSkillManaged]:
    """Was dieser Benutzer verwalten darf — global und in seinen Teams.

    Bewusst ohne `require_global`: die Berechtigung haengt hier nicht an einer
    globalen Rolle allein, sondern auch am Team-Schalter. Wer nichts verwalten
    darf, bekommt eine leere Liste statt eines 403 — die Oberflaeche blendet
    den Bereich dann aus, statt einen Fehler zu zeigen.
    """
    return [_managed(row) for row in ai_skill_service.manageable_skills(db, user)]


@router.get("/pending", response_model=list[AiSkillManaged])
def list_pending_skills(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.manage")),
) -> list[AiSkillManaged]:
    """Global gelernte Skills, die auf die Freigabe des Betreibers warten."""
    del user
    return [_managed(row) for row in ai_skill_service.pending_skills(db)]


@router.get("/{skill_key}", response_model=AiSkillDetail)
def read_skill(
    skill_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.use")),
) -> AiSkillDetail:
    view, body = ai_skill_service.read_body(db, user, skill_key)
    return AiSkillDetail(**_summary(view).model_dump(), body=body)


@router.put("", response_model=AiSkillManaged)
def upsert_skill(
    payload: AiSkillWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> AiSkillManaged:
    return _managed(ai_skill_service.upsert_skill(
        db, user=user, skill_key=payload.skill_key, name=payload.name,
        description=payload.description, body=payload.body, team_id=payload.team_id,
        origin="operator", status="active", enabled=payload.enabled,
    ))


@router.put("/{skill_id}/enabled", response_model=AiSkillManaged)
def toggle_skill(
    skill_id: str,
    payload: AiSkillToggle,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> AiSkillManaged:
    return _managed(ai_skill_service.set_enabled(
        db, user=user, skill_id=skill_id, enabled=payload.enabled
    ))


@router.post("/{skill_id}/approve", response_model=AiSkillManaged)
def approve_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.skills.manage")),
    _: None = Depends(verify_csrf),
) -> AiSkillManaged:
    return _managed(ai_skill_service.approve(db, user=user, skill_id=skill_id))


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> None:
    ai_skill_service.delete_skill(db, user=user, skill_id=skill_id)
