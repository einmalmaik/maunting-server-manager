"""Blueprint-Router — Listing, Template-Download, Import + Loeschen.

RBAC: Import + Loeschen erfordern ``blueprints.manage``. Das war frueher
``panel.settings.write`` mit der Begruendung "wer Settings darf, darf auch
Blueprints managen" — im Hoster-Betrieb zu grob: wer eine Spielversion pflegen
soll, bekam damit auch Steam-Zugangsdaten und E-Mail-Versand. Listing + Template
sind fuer alle eingeloggten User offen, damit die UI Doku/Server-Erstellen
darstellen kann.

Geschrieben wird in `services/blueprint_service.py` — die eine Implementierung,
die auch die KI nutzt, wenn sie einen Blueprint ableitet.

CSRF-Schutz auf allen state-changing Endpunkten (Import + Delete).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from services import blueprint_service

from blueprints import (
    Blueprint,
    COMMENTED_TEMPLATE_DE,
    COMMENTED_TEMPLATE_EN,
    get_registry,
)
from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import User

router = APIRouter(prefix="/api/blueprints", tags=["blueprints"])

logger = logging.getLogger(__name__)


def _serialize_entry(entry) -> dict:
    bp: Blueprint = entry.blueprint
    bp_mods = bp.effective_mods()
    return {
        "id": bp.meta.id,
        "name": bp.meta.name,
        "category": bp.meta.category.value,
        "author": bp.meta.author,
        "description": bp.meta.description,
        "origin": entry.origin.value,
        "version": bp.version,
        "image": bp.runtime.image,
        "source_type": bp.source.type.value,
        "supports_mods": bp_mods.supportsMods,
        "supports_steam_workshop": bp_mods.supportsSteamWorkshop,
        "mod_injection": bp_mods.modInjection.value,
        "ports": [
            {"name": p.name.value, "protocol": p.protocol.value} for p in bp.ports
        ],
    }


@router.get("")
def list_blueprints(
    _user: User = Depends(get_current_user),
) -> dict:
    """Listet native + community Blueprints (knappe Felder fuer UI)."""
    registry = get_registry()
    return {"blueprints": [_serialize_entry(e) for e in registry.list()]}


@router.get("/template")
def download_template(
    lang: str = "en",
    _user: User = Depends(get_current_user),
) -> Response:
    """Liefert das kommentierte Template als JSON-Download."""
    body = COMMENTED_TEMPLATE_DE if lang == "de" else COMMENTED_TEMPLATE_EN
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                'attachment; filename="msm-blueprint-template.json"'
            ),
        },
    )


@router.get("/{blueprint_id}")
def get_blueprint(
    blueprint_id: str,
    _user: User = Depends(get_current_user),
) -> Response:
    """Liefert eine Blueprint inkl. ``runtime.startup``-Original als JSON-Datei.

    Genutzt fuer Export (Download-Button im Detail / Doku). Keine Secrets — die
    Datei enthaelt nur das, was die User selbst geschrieben haben.
    """
    entry = get_registry().get(blueprint_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Blueprint nicht gefunden")
    # Pydantic dump → reines JSON-Objekt (Enums als Strings)
    data = entry.blueprint.model_dump(mode="json", by_alias=True)
    body = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{blueprint_id}.blueprint.json"'
            ),
        },
    )


@router.post("/import", status_code=201)
async def import_blueprint(
    request: Request,
    _user: User = Depends(require_global("blueprints.manage")),
    __=Depends(verify_csrf),
) -> JSONResponse:
    """Importiert eine Community-Blueprint via Roh-JSON-Body.

    HTTP-Rand: Body lesen und dekodieren. Pruefen und Speichern stehen in
    `services/blueprint_service.py`, weil auch die KI Blueprints ableiten kann
    und dabei denselben Weg nehmen muss — Schemapruefung, Native-Kollision und
    Registry-Neuladen inklusive.

    Das Recht ist seit dieser Aenderung `blueprints.manage` statt
    `panel.settings.write`. Wer eine Spielversion pflegen darf, musste dafuer
    nicht auch Steam-Zugangsdaten und E-Mail-Versand bekommen.
    """
    try:
        raw_bytes = await request.body()
        raw_str = raw_bytes.decode("utf-8")
        from blueprints.schema import _strip_json_comments

        clean_str = _strip_json_comments(raw_str)
        raw = json.loads(clean_str)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Body ist kein gueltiges JSON (oder falsches Encoding): {str(exc)}",
        ) from exc

    blueprint_id = blueprint_service.save_community_blueprint(raw)
    return JSONResponse(
        status_code=201,
        content={"message": "Blueprint importiert", "id": blueprint_id},
    )


@router.delete("/{blueprint_id}", status_code=204)
def delete_blueprint(
    blueprint_id: str,
    _user: User = Depends(require_global("blueprints.manage")),
    __=Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> Response:
    """Loescht eine Community-Blueprint.

    Native-IDs sind hart geschuetzt (400), und ein Blueprint, auf dem noch
    Server liegen, ebenso (409). Die Session dieses Requests geht mit, weil der
    Dienst diese Zaehlung selbst macht — sie soll denselben Stand sehen, auf dem
    dieser Request arbeitet, statt auf einer eigenen Verbindung eine andere
    Frage zu beantworten.
    """
    blueprint_service.delete_community_blueprint(blueprint_id, db=db)
    return Response(status_code=204)
