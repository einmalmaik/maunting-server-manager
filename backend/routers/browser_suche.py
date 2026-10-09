"""Die MSM-Suche des Secure Browsers.

Ein gekoppelter Browser sucht über die SearXNG-Instanz des eigenen Servers,
dieselbe, die auch Singra benutzt (`ai_web_search_service.searxng_treffer`).
Die Suchbegriffe gehen per POST, damit sie in keinem Zugriffslog stehen, und
werden hier nicht geloggt. Zurück kommen nur Titel, Adresse und Text, keine
Bilder: die Trefferseite lädt nichts von fremden Servern.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from limits import parse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from dependencies import get_current_user, verify_csrf
from middleware.rate_limit import limiter
from models.user import User
from services import ai_web_search_service
from services.ai_web_search_service import WebSearchUnavailable

router = APIRouter(prefix="/api/browser", tags=["browser"])

TREFFER_JE_SEITE = 10
_grenze = parse("60/minute")


class SucheAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    seite: StrictInt = Field(default=1, ge=1, le=20)
    sicher: StrictBool = False
    sprache: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")] | None = None


def _mit_code(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


@router.post("/suche")
def suchen(
    req: SucheAnfrage,
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-suche:{user.id}"):
        raise _mit_code(429, "BROWSER_SUCHE_ZU_VIELE")
    try:
        treffer = ai_web_search_service.searxng_treffer(
            req.q,
            limit=TREFFER_JE_SEITE,
            seite=req.seite,
            sicher=req.sicher,
            sprache=req.sprache,
        )
    except WebSearchUnavailable as exc:
        if exc.code == "AI_WEB_SEARCH_NOT_CONFIGURED":
            raise _mit_code(503, "BROWSER_SUCHE_NICHT_EINGERICHTET") from exc
        raise _mit_code(502, "BROWSER_SUCHE_NICHT_ERREICHBAR") from exc
    return {
        "treffer": [{"titel": t["title"], "url": t["url"], "inhalt": t["snippet"]} for t in treffer],
    }
