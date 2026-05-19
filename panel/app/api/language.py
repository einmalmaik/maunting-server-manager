from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models import User
from ..permissions import P_FILES_WRITE, require_perm
from ..shell import PanelCommandError, fetch_core_status, invoke_core_action
from .deps import get_current_user, require_server

router = APIRouter()
logger = logging.getLogger(__name__)

_SUPPORTED = {"en", "de"}


class LanguageBody(BaseModel):
    language: Literal["en", "de"]


@router.get("/language")
def get_language(
    server: str = Depends(require_server),
    _: User = Depends(get_current_user),
) -> dict:
    """Return the current language for the active server."""
    try:
        status = fetch_core_status(server_name=server)
        return {"language": (status or {}).get("language", "en")}
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("language get failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to fetch language.") from exc


@router.post("/language")
def set_language(
    body: LanguageBody,
    server: str = Depends(require_server),
    _: User = require_perm(P_FILES_WRITE),
) -> dict:
    """Change the interface language for the active server's config."""
    try:
        invoke_core_action("language", body.language, server_name=server)
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("language set failed: lang=%s error=%s", body.language, detail)
        raise HTTPException(status_code=500, detail="Failed to set language.") from exc
    return {"ok": True, "language": body.language}
