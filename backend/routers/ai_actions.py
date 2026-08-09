"""Bestaetigung und Ausfuehrung persistenter AI-Aktionsvorschlaege."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiActionProposal, User
from schemas.ai_action import (
    AiActionConfirmationResponse,
    AiActionExecuteRequest,
    AiActionExecuteResponse,
    AiActionProposalResponse,
)
from services import ai_action_errors, ai_chat_service, ai_proposal_service
from services.dis_client import DisSidecarError


router = APIRouter(prefix="/api/ai", tags=["ai-actions"])


def proposal_response(proposal: AiActionProposal) -> AiActionProposalResponse:
    try:
        preview = json.loads(proposal.preview_json)
    except (TypeError, json.JSONDecodeError):
        preview = {"unavailable": True}
    if not isinstance(preview, dict):
        preview = {"unavailable": True}
    return AiActionProposalResponse(
        id=proposal.id,
        conversation_id=proposal.conversation_id,
        server_id=proposal.server_id,
        tool_name=proposal.tool_name,
        preview=preview,
        expected_revision=proposal.expected_revision,
        requires_confirmation=proposal.requires_confirmation,
        autonomous=bool(proposal.autonomous),
        reason=proposal.reason,
        expected_effect=proposal.expected_effect,
        status=proposal.status,
        task_id=proposal.task_id,
        error_code=proposal.error_code,
        created_at=proposal.created_at,
    )


def _state_error(exc: ai_action_errors.AiActionStateError) -> HTTPException:
    if exc.code == "AI_ACTION_NOT_FOUND":
        return HTTPException(status_code=404, detail="Aktionsvorschlag nicht gefunden")
    if exc.code == "AI_ACTION_ACCESS_REVOKED":
        return HTTPException(status_code=403, detail="Berechtigung wurde entzogen")
    return HTTPException(status_code=409, detail={"code": exc.code})


@router.get("/conversation/actions", response_model=list[AiActionProposalResponse])
def list_conversation_actions(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> list[AiActionProposalResponse]:
    """Alle Vorschlaege der einen Unterhaltung, aeltester zuerst."""
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    rows = db.query(AiActionProposal).filter(
        AiActionProposal.conversation_id == conversation.id,
        AiActionProposal.user_id == user.id,
    ).order_by(AiActionProposal.created_at.asc()).all()
    return [proposal_response(row) for row in rows]


@router.get("/actions/{proposal_id}", response_model=AiActionProposalResponse)
def get_action(
    proposal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiActionProposalResponse:
    proposal = ai_proposal_service.owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Aktionsvorschlag nicht gefunden")
    return proposal_response(proposal)


@router.post(
    "/actions/{proposal_id}/confirm",
    response_model=AiActionConfirmationResponse,
)
def confirm_action(
    proposal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiActionConfirmationResponse:
    try:
        proposal, token = ai_proposal_service.confirm_proposal(
            db, proposal_id=proposal_id, user=user
        )
        assert proposal.confirmation_expires_at is not None
        return AiActionConfirmationResponse(
            proposal_id=proposal.id,
            confirmation_token=token,
            expires_at=proposal.confirmation_expires_at,
        )
    except ai_action_errors.AiActionStateError as exc:
        db.rollback()
        raise _state_error(exc) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Aktionsvorschlag ist nicht verfuegbar") from exc


@router.post(
    "/actions/{proposal_id}/execute",
    response_model=AiActionExecuteResponse,
)
def execute_action(
    proposal_id: str,
    payload: AiActionExecuteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiActionExecuteResponse:
    try:
        proposal, result = ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal_id,
            user=user,
            confirmation_token=payload.confirmation_token.get_secret_value(),
        )
        return AiActionExecuteResponse(proposal=proposal_response(proposal), result=result)
    except ai_action_errors.AiActionStateError as exc:
        db.rollback()
        raise _state_error(exc) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Aktionsvorschlag ist nicht verfuegbar") from exc
