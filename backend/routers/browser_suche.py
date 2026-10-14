"""Die Maunting-Search-Fläche des Secure Browsers (`routers/browser_suche.py`).

Ein gekoppelter Browser sucht über die SearXNG-Instanz des eigenen Servers,
dieselbe, die auch Singra benutzt (`ai_web_search_service.searxng_treffer`).
Die Suchbegriffe gehen per POST, damit sie in keinem Zugriffslog stehen, und
werden hier nicht geloggt.

Zusätzlich zur Trefferliste kennt diese Fläche:
- `/suche/autocomplete`: Live-Vorschläge der eigenen Instanz (kein Drittanbieter
  im Browser);
- `/suche/wissen`: die Wikipedia-Zusammenfassung samt Eckdaten, wenn SearXNG
  keine Infobox geliefert hat;
- `/suche/bild`: Vorschauen und Artikelbilder über den eigenen Server-Proxy.
  Geladen wird nur eine Adresse, die eine Suche oder das Wissens-Panel zuvor
  freigegeben hat — der Browser spricht mit keinem fremden Server.

Die Nachrichten (`services/browser_nachrichten_service.py`) sind für alle
Konten gleich; Themen und Schlagworte filtert der Browser selbst.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from limits import parse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints
from sqlalchemy.orm import Session

from dependencies import get_current_user, get_db, verify_csrf
from middleware.rate_limit import limiter
from models.user import User
from services import ai_search_summary_service, ai_web_search_service, browser_nachrichten_service
from services.ai_web_search_service import WebSearchUnavailable

router = APIRouter(prefix="/api/browser", tags=["browser"])

TREFFER_JE_SEITE = 10
_grenze = parse("60/minute")
_zusammenfassung_grenze = parse("30/minute")
_nachrichten_grenze = parse("30/minute")


class SucheAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    seite: StrictInt = Field(default=1, ge=1, le=20)
    sicher: StrictBool = False
    sprache: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None
    kategorie: Literal["general", "images", "videos", "news"] | None = None


class AutocompleteAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    sprache: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None


class WissenAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    sprache: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None


class SuchTrefferInhalt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index: StrictInt = Field(default=1, ge=1, le=50)
    titel: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    inhalt: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""


class SuchVerlaufEintrag(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Annotated[str, StringConstraints(pattern=r"^(user|assistant)$")]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class SucheZusammenfassungAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    treffer: list[SuchTrefferInhalt] = Field(default_factory=list, max_length=50)
    verlauf: list[SuchVerlaufEintrag] = Field(default_factory=list, max_length=50)
    provider_id: StrictInt | None = None
    denkstufe: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None


def _mit_code(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


@router.get("/suche/konfig")
def suchkonfig(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ai_search_summary_service.suchzusammenfassung_konfig(db=db, user=user)


@router.post("/suche")
def suchen(
    req: SucheAnfrage,
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-suche:{user.id}"):
        raise _mit_code(429, "BROWSER_SUCHE_ZU_VIELE")
    try:
        ergebnis = ai_web_search_service.searxng_ergebnis(
            req.q,
            limit=TREFFER_JE_SEITE,
            seite=req.seite,
            sicher=req.sicher,
            sprache=req.sprache,
            kategorie=req.kategorie,
        )
    except WebSearchUnavailable as exc:
        if exc.code == "AI_WEB_SEARCH_NOT_CONFIGURED":
            raise _mit_code(503, "BROWSER_SUCHE_NICHT_EINGERICHTET") from exc
        raise _mit_code(502, "BROWSER_SUCHE_NICHT_ERREICHBAR") from exc
    antwort: dict = {
        "treffer": [
            {
                "titel": t["title"],
                "url": t["url"],
                "inhalt": t["snippet"],
                **({"bild": t["bild"]} if "bild" in t else {}),
                **({"published": t["published"]} if "published" in t else {}),
            }
            for t in ergebnis.get("treffer", [])
        ],
        "vorschlaege": ergebnis.get("vorschlaege", []),
    }
    if "infobox" in ergebnis:
        antwort["infobox"] = ergebnis["infobox"]
    return antwort


@router.post("/suche/autocomplete")
def suche_autocomplete(
    req: AutocompleteAnfrage,
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-suche-ac:{user.id}"):
        return {"vorschlaege": []}
    lang = (req.sprache or "de")[:2]
    return {"vorschlaege": ai_web_search_service.searxng_autocomplete(req.q, sprache=lang)}


@router.post("/suche/wissen")
def suche_wissen(
    req: WissenAnfrage,
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-suche-wissen:{user.id}"):
        return {"wissen": None}
    lang = (req.sprache or "de")[:2]
    return {"wissen": ai_web_search_service.wikipedia_wissen(req.q, sprache=lang)}


@router.get("/suche/bild")
def suche_bild_proxy(
    u: Annotated[str, Query(min_length=8, max_length=300)],
    user: User = Depends(get_current_user),
) -> Response:
    if not limiter.limiter.hit(_grenze, f"browser-suche-bild:{user.id}"):
        raise _mit_code(429, "BROWSER_SUCHE_ZU_VIELE")
    erg = ai_web_search_service.hole_such_bild(u)
    if not erg:
        raise HTTPException(status_code=404, detail={"code": "BILD_NICHT_VERFUEGBAR"})
    daten, media_type = erg
    return Response(content=daten, media_type=media_type, headers={"Cache-Control": "private, max-age=900"})


@router.post("/suche/zusammenfassung")
async def suchzusammenfassung_stream(
    req: SucheZusammenfassungAnfrage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    __=Depends(verify_csrf),
) -> StreamingResponse:
    if not limiter.limiter.hit(_zusammenfassung_grenze, f"browser-suche-zusammenfassung:{user.id}"):
        raise _mit_code(429, "BROWSER_SUCHE_ZU_VIELE")

    treffer_dict = [t.model_dump() for t in req.treffer]
    verlauf_dict = [v.model_dump() for v in req.verlauf]

    return StreamingResponse(
        ai_search_summary_service.stream_suchzusammenfassung(
            db=db,
            user=user,
            q=req.q,
            treffer=treffer_dict,
            verlauf=verlauf_dict,
            bevorzugte_id=req.provider_id,
            denkstufe=req.denkstufe,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/nachrichten")
def nachrichten(
    sprache: Annotated[str, Query(pattern=r"^[a-z]{2}$")] = "en",
    user: User = Depends(get_current_user),
) -> dict:
    if not limiter.limiter.hit(_nachrichten_grenze, f"browser-nachrichten:{user.id}"):
        raise _mit_code(429, "BROWSER_NACHRICHTEN_ZU_VIELE")
    try:
        return browser_nachrichten_service.nachrichten(sprache)
    except WebSearchUnavailable as exc:
        if exc.code == "AI_WEB_SEARCH_NOT_CONFIGURED":
            raise _mit_code(503, "BROWSER_NACHRICHTEN_NICHT_EINGERICHTET") from exc
        raise _mit_code(502, "BROWSER_NACHRICHTEN_NICHT_ERREICHBAR") from exc
