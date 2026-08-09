"""Kleine Quarantaene fuer begrenzte Text- und Bildanhaenge."""

from __future__ import annotations

import base64
import hashlib
from pathlib import PurePath
import struct
import zlib
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiAttachment, AiConversation, User
from services import audit_service
from services.ai_chat_service import get_owned_conversation
from services.ai_redaction import redact_sensitive_text
from services.dis_client import DisClient


MAX_ATTACHMENT_BYTES = 256 * 1024
MAX_ATTACHMENTS_PER_CONVERSATION = 10
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 16_000_000
MAX_PROVIDER_TEXT_CHARS = 12_000
MAX_PROVIDER_ATTACHMENTS = 5
TEXT_TYPES = {
    ".cfg": "text/plain", ".conf": "text/plain", ".ini": "text/plain",
    ".json": "application/json", ".log": "text/plain", ".properties": "text/plain",
    ".toml": "text/plain", ".txt": "text/plain", ".yaml": "text/yaml", ".yml": "text/yaml",
}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
BLOCKED_SIGNATURES = (b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"MZ", b"\x7fELF", b"%PDF")


class AttachmentRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _aad(attachment_id: str, kind: str) -> str:
    return f"msm:ai:attachment:{attachment_id}:{kind}"


def _safe_name(value: str | None) -> tuple[str, str]:
    if (
        not value
        or len(value) > 128
        or "/" in value
        or "\\" in value
        or PurePath(value).name != value
        or any(
        ord(char) < 32 for char in value
        )
    ):
        raise AttachmentRejected("AI_ATTACHMENT_NAME_INVALID")
    suffix = PurePath(value).suffix.lower()
    if suffix not in TEXT_TYPES and suffix not in IMAGE_TYPES:
        raise AttachmentRejected("AI_ATTACHMENT_TYPE_BLOCKED")
    return value, suffix


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 7:
                break
            return (
                int.from_bytes(data[offset + 5:offset + 7], "big"),
                int.from_bytes(data[offset + 3:offset + 5], "big"),
            )
        offset += length
    raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")


def _validate_image(data: bytes, suffix: str) -> None:
    if suffix == ".png":
        if len(data) < 45 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
        offset = 8
        width = height = 0
        saw_idat = saw_iend = False
        chunk_index = 0
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            chunk_type = data[offset + 4:offset + 8]
            end = offset + 12 + length
            if length > MAX_ATTACHMENT_BYTES or end > len(data):
                raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
            chunk_data = data[offset + 8:offset + 8 + length]
            expected_crc = int.from_bytes(data[offset + 8 + length:end], "big")
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
            if chunk_index == 0:
                if chunk_type != b"IHDR" or length != 13:
                    raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
                width, height = struct.unpack(">II", chunk_data[:8])
            elif chunk_type == b"IDAT":
                saw_idat = True
            elif chunk_type == b"IEND":
                if length != 0 or end != len(data):
                    raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
                saw_iend = True
                break
            offset = end
            chunk_index += 1
        if not saw_idat or not saw_iend:
            raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
    else:
        if not data.endswith(b"\xff\xd9"):
            raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
        width, height = _jpeg_dimensions(data)
    if (
        width <= 0 or height <= 0 or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS
    ):
        raise AttachmentRejected("AI_ATTACHMENT_IMAGE_TOO_LARGE")


def create_attachment(
    db: Session, *, user: User, conversation: AiConversation, filename: str | None,
    declared_type: str | None, data: bytes,
) -> AiAttachment:
    if len(data) == 0 or len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentRejected("AI_ATTACHMENT_SIZE_INVALID")
    if db.query(AiAttachment).filter(
        AiAttachment.conversation_id == conversation.id,
        AiAttachment.status.in_(["quarantined", "ready"]),
    ).count() >= MAX_ATTACHMENTS_PER_CONVERSATION:
        raise AttachmentRejected("AI_ATTACHMENT_LIMIT_REACHED")
    safe_name, suffix = _safe_name(filename)
    if any(data.startswith(signature) for signature in BLOCKED_SIGNATURES) or (
        len(data) > 262 and data[257:262] == b"ustar"
    ):
        raise AttachmentRejected("AI_ATTACHMENT_ARCHIVE_BLOCKED")
    expected_type = TEXT_TYPES.get(suffix) or IMAGE_TYPES[suffix]
    if declared_type and declared_type not in {expected_type, "application/octet-stream"}:
        raise AttachmentRejected("AI_ATTACHMENT_TYPE_MISMATCH")
    attachment_id = str(uuid4())
    extracted: str | None = None
    if suffix in TEXT_TYPES:
        if b"\x00" in data:
            raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID")
        try:
            extracted = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentRejected("AI_ATTACHMENT_CONTENT_INVALID") from exc
        if redact_sensitive_text(extracted) != extracted:
            raise AttachmentRejected("AI_ATTACHMENT_SECRET_DETECTED")
    else:
        _validate_image(data, suffix)
    row = AiAttachment(
        id=attachment_id, conversation_id=conversation.id, user_id=user.id,
        original_name=safe_name, media_type=expected_type, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_encrypted=DisClient.encrypt(
            base64.b64encode(data).decode("ascii"), aad=_aad(attachment_id, "content")
        ),
        extracted_text_encrypted=(
            DisClient.encrypt(extracted, aad=_aad(attachment_id, "text"))
            if extracted is not None else None
        ),
        status="ready",
    )
    db.add(row)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.attachment.accepted", target_type="ai_attachment",
        target_id=row.id, details={"media_type": expected_type, "size_bytes": len(data)},
        origin="direct",
    )
    db.commit()
    db.refresh(row)
    return row


def list_owned(db: Session, user: User, conversation_id: str) -> list[AiAttachment]:
    conversation = get_owned_conversation(db, conversation_id, user)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden")
    return db.query(AiAttachment).filter(
        AiAttachment.conversation_id == conversation.id, AiAttachment.user_id == user.id
    ).order_by(AiAttachment.created_at).all()


def delete_owned(db: Session, user: User, attachment_id: str) -> None:
    try:
        canonical = str(UUID(attachment_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Anhang nicht gefunden") from exc
    row = db.query(AiAttachment).filter(
        AiAttachment.id == canonical, AiAttachment.user_id == user.id
    ).first()
    if row is None or get_owned_conversation(db, row.conversation_id, user) is None:
        raise HTTPException(status_code=404, detail="Anhang nicht gefunden")
    db.delete(row)
    db.commit()


def provider_attachment_messages(
    db: Session, conversation_id: str, user_id: int
) -> list[dict]:
    rows = db.query(AiAttachment).filter(
        AiAttachment.conversation_id == conversation_id,
        AiAttachment.user_id == user_id,
        AiAttachment.status == "ready",
    ).order_by(AiAttachment.created_at.desc()).limit(MAX_PROVIDER_ATTACHMENTS).all()
    messages: list[dict] = []
    remaining_text_chars = MAX_PROVIDER_TEXT_CHARS
    for row in reversed(rows):
        if row.extracted_text_encrypted is not None:
            if remaining_text_chars <= 0:
                continue
            text = DisClient.decrypt(row.extracted_text_encrypted, aad=_aad(row.id, "text"))
            text = text[:remaining_text_chars]
            remaining_text_chars -= len(text)
            # Bewusst role="user": ein Anhang ist Benutzerdaten, keine
            # Systemanweisung. Mit role="system" haette hochgeladener Text
            # dieselbe Autoritaet wie der MSM-Systemprompt und waere damit ein
            # direkter Prompt-Injection-Pfad.
            messages.append({
                "role": "user",
                "content": f"Unvertrauenswuerdiger Textanhang {row.original_name}:\n{text}",
            })
        elif row.media_type in {"image/png", "image/jpeg"}:
            encoded = DisClient.decrypt(row.content_encrypted, aad=_aad(row.id, "content"))
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Unvertrauenswuerdiger Bildanhang: {row.original_name}"},
                    {"type": "image_url", "image_url": {"url": f"data:{row.media_type};base64,{encoded}"}},
                ],
            })
    return messages
