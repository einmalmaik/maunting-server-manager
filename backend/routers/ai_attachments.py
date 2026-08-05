"""Upload-Rand fuer begrenzte, isoliert validierte AI-Anhaenge."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiAttachment, User
from schemas.ai_attachment import AiAttachmentResponse
from services import ai_attachment_service, ai_chat_service, permission_service
from services.dis_client import DisSidecarError


router = APIRouter(prefix="/api/ai", tags=["ai-attachments"])


def _response(row: AiAttachment) -> AiAttachmentResponse:
    return AiAttachmentResponse(
        id=row.id, conversation_id=row.conversation_id,
        original_name=row.original_name, media_type=row.media_type,
        size_bytes=row.size_bytes, status=row.status,
        rejection_code=row.rejection_code, created_at=row.created_at,
    )


def _require_chat(db: Session, user: User) -> None:
    if not permission_service.has_global_permission(db, user, "ai.chat.use"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=AiAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.attachments.use")),
    _: None = Depends(verify_csrf),
) -> AiAttachmentResponse:
    _require_chat(db, user)
    conversation = ai_chat_service.get_owned_conversation(db, conversation_id, user)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden")
    data = await file.read(ai_attachment_service.MAX_ATTACHMENT_BYTES + 1)
    try:
        return _response(ai_attachment_service.create_attachment(
            db, user=user, conversation=conversation, filename=file.filename,
            declared_type=file.content_type, data=data,
        ))
    except ai_attachment_service.AttachmentRejected as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Anhangspeicher ist nicht verfuegbar") from exc
    finally:
        await file.close()


@router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=list[AiAttachmentResponse],
)
def list_attachments(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.attachments.use")),
) -> list[AiAttachmentResponse]:
    _require_chat(db, user)
    return [_response(row) for row in ai_attachment_service.list_owned(
        db, user, conversation_id
    )]


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.attachments.use")),
    _: None = Depends(verify_csrf),
) -> None:
    _require_chat(db, user)
    ai_attachment_service.delete_owned(db, user, attachment_id)
