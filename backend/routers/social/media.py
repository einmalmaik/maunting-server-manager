from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    verify_csrf,
)
from models import User
from schemas.chat_media import (
    ChatMediaUploadRequest,
    ChatMediaUploadResponse,
    ChatMediaSignedUrlResponse,
)
from services.chat_media_service import ChatMediaService
from services.chat_media_validator import sanitize_attachment_filename
from services.social_service import SocialService
from routers.social.basis import _check_social_enabled, mailbox_token

logger = logging.getLogger(__name__)

router = APIRouter()



@router.post(
    "/media/upload",
    response_model=ChatMediaUploadResponse,
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def upload_chat_media(
    req: ChatMediaUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    """Laedt einen clientseitig verschluesselten E2EE-Medienblob hoch.

    Der Server nimmt ausschliesslich verschluesselte Blobs entgegen (Zero-Knowledge).
    """
    SocialService.assert_mailbox_token(db, req.blind_mailbox_id, nachweis, current_user.id)
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=current_user,
        blind_mailbox_id=req.blind_mailbox_id,
        ciphertext_blob=req.ciphertext_blob,
        file_name=req.file_name,
        media_type=req.media_type,
        group_id=req.group_id,
        mailbox_token=nachweis,
    )
    return {
        "id": media.id,
        "blind_mailbox_id": media.blind_mailbox_id,
        "file_name": media.file_name,
        "media_type": media.media_type,
        "size_bytes": media.size_bytes,
        "sha256": media.sha256,
        "created_at": media.created_at,
    }


@router.delete(
    "/media/{media_id}",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def delete_chat_media(
    media_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Loescht einen verschluesselten Medienblob. Nur der Hochladende darf das.

    Gehoert zum Loeschen einer Nachricht: Bild, Datei, Sprachnachricht und
    Videonotiz liegen als eigene Blobs neben dem Umschlag.
    """
    geloescht = ChatMediaService.delete_media(db, user=current_user, media_id=media_id)
    return {"ok": True, "deleted": geloescht}


@router.get(
    "/media/{media_id}/signed-url",
    response_model=ChatMediaSignedUrlResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def get_chat_media_signed_url(
    media_id: str,
    ttl: int = Query(900, ge=60, le=86400),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    """Erzeugt eine zeitlich begrenzte signierte URL fuer einen Medienblob.

    Fremde ohne Chat-Mitgliedschaft werden mit 403 Forbidden abgewiesen.
    """
    signed_url, expires_at = ChatMediaService.generate_signed_url(
        db, user=current_user, media_id=media_id, ttl_seconds=ttl, mailbox_token=nachweis
    )
    return {
        "media_id": media_id,
        "signed_url": signed_url,
        "expires_at": expires_at,
    }


@router.get(
    "/media/{media_id}/download",
    dependencies=[Depends(_check_social_enabled)],
)
def download_chat_media_blob(
    media_id: str,
    token: str = Query(...),
    expires: int = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
    nachweis: str | None = Depends(mailbox_token),
) -> Response:
    """Liefert den verschluesselten Medienblob anhand einer signierten URL aus.

    Validiert Signatur, Ablaufzeit und Chat-Mitgliedschaft.
    """
    media = ChatMediaService.get_media_by_signed_url(
        db, media_id=media_id, token=token, expires=expires, user_id=user_id, mailbox_token=nachweis
    )
    safe_name = sanitize_attachment_filename(media.file_name)
    return Response(
        content=media.ciphertext_blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.e2ee"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, no-cache, no-store",
        },
    )
