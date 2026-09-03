"""Router für Server-Sent-Events (SSE) Echtzeit-Synchronisation.

Streamt autorisierte Mutations-Signale für Notizen, Checklisten und Kalendertermine
an verbundene Browser, Mobile- und Desktop-Clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.user import User
from services import team_service
from services.sync_event_service import SyncEventService

_log = logging.getLogger("msm.sync_events_router")

router = APIRouter(prefix="/api/events", tags=["sync-events"])


async def _event_stream(
    request: Request,
    user_id: int,
    team_ids: list[int],
    is_admin: bool,
) -> AsyncIterator[str]:
    """Generiert einen asynchronen SSE-Datenstrom für den authentifizierten Benutzer."""
    conn_id, queue = SyncEventService.subscribe(
        user_id=user_id,
        team_ids=team_ids,
        is_admin=is_admin,
    )
    try:
        # 1. Initiales Begrüßungs-Signal
        yield SyncEventService.format_sse(
            "ready",
            {"status": "connected", "user_id": user_id, "conn_id": conn_id},
        )

        while True:
            if await request.is_disconnected():
                break

            try:
                # Warte bis zu 15 Sekunden auf ein neues Signal
                event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield SyncEventService.format_sse("sync", event_data)
            except asyncio.TimeoutError:
                # Keepalive Ping gegen Verbindungstimeouts bei Proxies/Firewalls
                yield SyncEventService.ping_sse()
            except asyncio.CancelledError:
                break
    except Exception as e:
        _log.debug("SSE-Stream beendet für %s: %s", conn_id, e)
    finally:
        SyncEventService.unsubscribe(conn_id)


@router.get("/live")
@router.get("/stream")
async def live_events(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Authentifizierter SSE-Endpunkt für Echtzeit-Synchronisation von Notizen & Kalender."""
    user_teams = team_service.list_user_teams(db, user)
    team_ids = [t.id for t in user_teams]
    is_admin = bool(user.is_owner)

    return StreamingResponse(
        _event_stream(
            request=request,
            user_id=user.id,
            team_ids=team_ids,
            is_admin=is_admin,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# Zusätzlicher Alias-Router unter /api/sync/events zur maximalen Kompatibilität
sync_alias_router = APIRouter(prefix="/api/sync", tags=["sync-events"])


@sync_alias_router.get("/events")
async def sync_events_alias(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Alias für den SSE-Live-Event-Kanal."""
    return await live_events(request=request, db=db, user=user)
