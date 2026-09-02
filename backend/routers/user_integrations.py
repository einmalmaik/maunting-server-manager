"""Router zur Verwaltung von verknüpften Postfächern und Kalendern des Benutzers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.user_calendar import UserCalendar
from models.user_mailbox import UserMailbox
from services.calendar_service import CalendarService
from services.mailbox_service import MailboxService

router = APIRouter(prefix="/api/user/integrations", tags=["user_integrations"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class MailboxCreate(BaseModel):
    name: str = Field(..., max_length=128)
    email: EmailStr
    provider_type: str = Field("imap_smtp", max_length=32)
    is_default: bool = False
    imap_host: str | None = None
    imap_port: int | None = 993
    imap_use_ssl: bool = True
    imap_username: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = 587
    smtp_use_tls: bool = True
    smtp_username: str | None = None
    password_or_token: str | None = None
    sync_enabled: bool = False
    notify_filter_rules: list[dict[str, Any]] | None = None


class MailboxUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    is_default: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_use_ssl: bool | None = None
    imap_username: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool | None = None
    smtp_username: str | None = None
    password_or_token: str | None = None
    sync_enabled: bool | None = None
    notify_filter_rules: list[dict[str, Any]] | None = None


class MailboxOut(BaseModel):
    id: int
    name: str
    email: str
    provider_type: str
    is_default: bool
    imap_host: str | None
    imap_port: int | None
    imap_use_ssl: bool
    imap_username: str | None
    smtp_host: str | None
    smtp_port: int | None
    smtp_use_tls: bool
    smtp_username: str | None
    has_credentials: bool
    sync_enabled: bool
    notify_filter_rules: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class CalendarCreate(BaseModel):
    name: str = Field(..., max_length=128)
    provider_type: str = Field("caldav", max_length=32)
    is_default: bool = False
    caldav_url: str | None = None
    caldav_username: str | None = None
    password_or_token: str | None = None


class CalendarUpdate(BaseModel):
    name: str | None = None
    is_default: bool | None = None
    caldav_url: str | None = None
    caldav_username: str | None = None
    password_or_token: str | None = None


class CalendarOut(BaseModel):
    id: int
    name: str
    provider_type: str
    is_default: bool
    caldav_url: str | None
    caldav_username: str | None
    has_credentials: bool
    created_at: datetime
    updated_at: datetime


# ── Mailbox Endpunkte ─────────────────────────────────────────────────────────


@router.get("/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MailboxOut]:
    rows = db.scalars(
        select(UserMailbox).where(UserMailbox.user_id == user.id).order_by(UserMailbox.id.asc())
    ).all()
    return [
        MailboxOut(
            id=r.id,
            name=r.name,
            email=r.email,
            provider_type=r.provider_type,
            is_default=r.is_default,
            imap_host=r.imap_host,
            imap_port=r.imap_port,
            imap_use_ssl=r.imap_use_ssl,
            imap_username=r.imap_username,
            smtp_host=r.smtp_host,
            smtp_port=r.smtp_port,
            smtp_use_tls=r.smtp_use_tls,
            smtp_username=r.smtp_username,
            has_credentials=bool(r.credentials_encrypted),
            sync_enabled=r.sync_enabled,
            notify_filter_rules=r.notify_filter_rules,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/mailboxes", response_model=MailboxOut, status_code=status.HTTP_201_CREATED)
def create_mailbox(
    payload: MailboxCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MailboxOut:
    if payload.is_default:
        # Andere Default-Flags für diesen User zurücksetzen
        db.query(UserMailbox).filter(UserMailbox.user_id == user.id).update({"is_default": False})

    row = UserMailbox(
        user_id=user.id,
        name=payload.name,
        email=str(payload.email),
        provider_type=payload.provider_type,
        is_default=payload.is_default,
        imap_host=payload.imap_host,
        imap_port=payload.imap_port,
        imap_use_ssl=payload.imap_use_ssl,
        imap_username=payload.imap_username,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_use_tls=payload.smtp_use_tls,
        smtp_username=payload.smtp_username,
        sync_enabled=payload.sync_enabled,
    )
    if payload.notify_filter_rules is not None:
        row.notify_filter_rules = payload.notify_filter_rules

    db.add(row)
    db.flush()

    if payload.password_or_token:
        row.set_credentials(payload.password_or_token)

    db.commit()
    db.refresh(row)

    return MailboxOut(
        id=row.id,
        name=row.name,
        email=row.email,
        provider_type=row.provider_type,
        is_default=row.is_default,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        imap_use_ssl=row.imap_use_ssl,
        imap_username=row.imap_username,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_use_tls=row.smtp_use_tls,
        smtp_username=row.smtp_username,
        has_credentials=bool(row.credentials_encrypted),
        sync_enabled=row.sync_enabled,
        notify_filter_rules=row.notify_filter_rules,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put("/mailboxes/{mailbox_id}", response_model=MailboxOut)
@router.patch("/mailboxes/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(
    mailbox_id: int,
    payload: MailboxUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MailboxOut:
    row = db.scalar(
        select(UserMailbox).where(UserMailbox.id == mailbox_id, UserMailbox.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden")

    if payload.is_default:
        db.query(UserMailbox).filter(UserMailbox.user_id == user.id).update({"is_default": False})
        row.is_default = True

    if payload.name is not None:
        row.name = payload.name
    if payload.email is not None:
        row.email = str(payload.email)
    if payload.imap_host is not None:
        row.imap_host = payload.imap_host
    if payload.imap_port is not None:
        row.imap_port = payload.imap_port
    if payload.imap_use_ssl is not None:
        row.imap_use_ssl = payload.imap_use_ssl
    if payload.imap_username is not None:
        row.imap_username = payload.imap_username
    if payload.smtp_host is not None:
        row.smtp_host = payload.smtp_host
    if payload.smtp_port is not None:
        row.smtp_port = payload.smtp_port
    if payload.smtp_use_tls is not None:
        row.smtp_use_tls = payload.smtp_use_tls
    if payload.smtp_username is not None:
        row.smtp_username = payload.smtp_username
    if payload.sync_enabled is not None:
        row.sync_enabled = payload.sync_enabled
    if payload.notify_filter_rules is not None:
        row.notify_filter_rules = payload.notify_filter_rules
    if payload.password_or_token:
        row.set_credentials(payload.password_or_token)

    db.commit()
    db.refresh(row)

    return MailboxOut(
        id=row.id,
        name=row.name,
        email=row.email,
        provider_type=row.provider_type,
        is_default=row.is_default,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        imap_use_ssl=row.imap_use_ssl,
        imap_username=row.imap_username,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_use_tls=row.smtp_use_tls,
        smtp_username=row.smtp_username,
        has_credentials=bool(row.credentials_encrypted),
        sync_enabled=row.sync_enabled,
        notify_filter_rules=row.notify_filter_rules,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete("/mailboxes/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mailbox(
    mailbox_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.scalar(
        select(UserMailbox).where(UserMailbox.id == mailbox_id, UserMailbox.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mailboxes/{mailbox_id}/test")
def test_mailbox(
    mailbox_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.scalar(
        select(UserMailbox).where(UserMailbox.id == mailbox_id, UserMailbox.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden")

    ok, msg = MailboxService.test_connection(row)
    return {"ok": ok, "message": msg}


# ── Calendar Endpunkte ────────────────────────────────────────────────────────


@router.get("/calendars", response_model=list[CalendarOut])
def list_calendars(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CalendarOut]:
    rows = db.scalars(
        select(UserCalendar).where(UserCalendar.user_id == user.id).order_by(UserCalendar.id.asc())
    ).all()
    return [
        CalendarOut(
            id=r.id,
            name=r.name,
            provider_type=r.provider_type,
            is_default=r.is_default,
            caldav_url=r.caldav_url,
            caldav_username=r.caldav_username,
            has_credentials=bool(r.credentials_encrypted),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/calendars", response_model=CalendarOut, status_code=status.HTTP_201_CREATED)
def create_calendar(
    payload: CalendarCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarOut:
    if payload.is_default:
        db.query(UserCalendar).filter(UserCalendar.user_id == user.id).update({"is_default": False})

    row = UserCalendar(
        user_id=user.id,
        name=payload.name,
        provider_type=payload.provider_type,
        is_default=payload.is_default,
        caldav_url=payload.caldav_url,
        caldav_username=payload.caldav_username,
    )
    db.add(row)
    db.flush()

    if payload.password_or_token:
        row.set_credentials(payload.password_or_token)

    db.commit()
    db.refresh(row)

    return CalendarOut(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        is_default=row.is_default,
        caldav_url=row.caldav_url,
        caldav_username=row.caldav_username,
        has_credentials=bool(row.credentials_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put("/calendars/{calendar_id}", response_model=CalendarOut)
@router.patch("/calendars/{calendar_id}", response_model=CalendarOut)
def update_calendar(
    calendar_id: int,
    payload: CalendarUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarOut:
    row = db.scalar(
        select(UserCalendar).where(UserCalendar.id == calendar_id, UserCalendar.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Kalender nicht gefunden")

    if payload.is_default:
        db.query(UserCalendar).filter(UserCalendar.user_id == user.id).update({"is_default": False})
        row.is_default = True

    if payload.name is not None:
        row.name = payload.name
    if payload.caldav_url is not None:
        row.caldav_url = payload.caldav_url
    if payload.caldav_username is not None:
        row.caldav_username = payload.caldav_username
    if payload.password_or_token:
        row.set_credentials(payload.password_or_token)

    db.commit()
    db.refresh(row)

    return CalendarOut(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        is_default=row.is_default,
        caldav_url=row.caldav_url,
        caldav_username=row.caldav_username,
        has_credentials=bool(row.credentials_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete("/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar(
    calendar_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.scalar(
        select(UserCalendar).where(UserCalendar.id == calendar_id, UserCalendar.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Kalender nicht gefunden")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/calendars/{calendar_id}/test")
def test_calendar(
    calendar_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.scalar(
        select(UserCalendar).where(UserCalendar.id == calendar_id, UserCalendar.user_id == user.id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Kalender nicht gefunden")

    ok, msg = CalendarService.test_connection(row)
    return {"ok": ok, "message": msg}
