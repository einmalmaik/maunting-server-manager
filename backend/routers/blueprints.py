"""Blueprint-Router — Listing, Template-Download, Import + Loeschen.

RBAC: Import + Loeschen erfordern ``panel.settings.write`` (KISS: keine neue
Permission, wer Settings darf, darf auch Blueprints managen). Listing + Template
sind fuer alle eingeloggten User offen, damit die UI Doku/Server-Erstellen
darstellen kann.

CSRF-Schutz auf allen state-changing Endpunkten (Import + Delete).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from blueprints import (
    Blueprint,
    BlueprintValidationError,
    COMMENTED_TEMPLATE_DE,
    COMMENTED_TEMPLATE_EN,
    get_registry,
    load_blueprint_dict,
    reload_registry,
)
from blueprints.registry import (
    BlueprintSourceOrigin,
    community_blueprint_path,
    ensure_community_dir,
)
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
    _user: User = Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> JSONResponse:
    """Importiert eine Community-Blueprint via Roh-JSON-Body.

    Sicherheitsregeln:
    - Body wird strikt ueber das Pydantic-Schema validiert (kein ``extra``).
    - ID darf nicht mit nativer Blueprint kollidieren — native gewinnt immer.
    - Ueberschreiben vorhandener Community-Blueprint ist erlaubt (Update-Workflow).
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

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400, detail="Body muss ein JSON-Objekt sein."
        )

    try:
        blueprint = load_blueprint_dict(raw)
    except BlueprintValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": "Blueprint-Validierung fehlgeschlagen", "errors": exc.errors},
        ) from exc

    # Native-Kollision -> 409: Wir ueberschreiben niemals native Blueprints.
    registry = get_registry()
    existing = registry.get(blueprint.meta.id)
    if existing is not None and existing.origin == BlueprintSourceOrigin.NATIVE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Blueprint-ID '{blueprint.meta.id}' kollidiert mit einer "
                "nativen Blueprint und darf nicht ueberschrieben werden."
            ),
        )

    ensure_community_dir()
    try:
        target = community_blueprint_path(blueprint.meta.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = blueprint.model_dump(mode="json", by_alias=True)
    try:
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Konnte Blueprint %s nicht schreiben: %s", target.name, exc)
        raise HTTPException(
            status_code=500, detail="Blueprint konnte nicht gespeichert werden."
        ) from exc

    reload_registry()
    return JSONResponse(
        status_code=201,
        content={
            "message": "Blueprint importiert",
            "id": blueprint.meta.id,
        },
    )


@router.delete("/{blueprint_id}", status_code=204)
def delete_blueprint(
    blueprint_id: str,
    _user: User = Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> Response:
    """Loescht eine Community-Blueprint. Native-IDs sind hart geschuetzt (400)."""
    entry = get_registry().get(blueprint_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Blueprint nicht gefunden")
    if entry.origin == BlueprintSourceOrigin.NATIVE:
        raise HTTPException(
            status_code=400,
            detail="Native Blueprints sind read-only und koennen nicht geloescht werden.",
        )

    try:
        target = community_blueprint_path(blueprint_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Konnte Blueprint %s nicht loeschen: %s", target.name, exc)
        raise HTTPException(
            status_code=500, detail="Blueprint konnte nicht geloescht werden."
        ) from exc

    reload_registry()
    return Response(status_code=204)
