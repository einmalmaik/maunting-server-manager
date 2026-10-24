"""Sammelrouter der Social-API.

Die Pfade bleiben unter ``/api/social`` unverändert. Jede Domäne bringt ihren
eigenen Router mit; hier werden sie in derselben Reihenfolge eingebunden, in
der die Routen zuvor in einer Datei standen.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import devices, friends, groups, mailbox, media, messages, profiles, stories, ws
from .basis import mailbox_token
from .mailbox import subscribe_mailbox_push

router = APIRouter(prefix="/api/social", tags=["social"])

router.include_router(messages.router)
router.include_router(friends.router)
router.include_router(profiles.router)
router.include_router(devices.router)
router.include_router(mailbox.router)
router.include_router(media.router)
router.include_router(groups.router)
router.include_router(stories.router)
router.include_router(ws.router)

__all__ = ["mailbox_token", "router", "subscribe_mailbox_push"]
