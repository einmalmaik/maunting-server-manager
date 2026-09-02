"""REST-Router für Notizen, Checklisten und Team-Notizen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, verify_csrf
from models.user import User
from schemas.note import NoteCreate, NoteResponse, NoteUpdate
from services.notes_service import NotesService
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _check_notes_enabled() -> None:
    """Prüft, ob das Notizmodul in den Panel-Einstellungen aktiv ist."""
    if PanelSettingsService.get("notes_enabled", "true") == "false":
        raise HTTPException(
            status_code=403,
            detail="Das Notizmodul ist in diesem Panel deaktiviert.",
        )


@router.get("/status")
def get_notes_status(
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Liefert den Aktivierungsstatus des Notizmoduls."""
    enabled = PanelSettingsService.get("notes_enabled", "true") != "false"
    return {"enabled": enabled}


@router.get("", response_model=list[NoteResponse])
def list_notes(
    category: str | None = Query(None, description="Kategorie-Filter"),
    team_id: int | None = Query(None, description="Team-Filter (0 = nur persönlich)"),
    search: str | None = Query(None, description="Suchbegriff in Titel und Inhalt"),
    is_pinned: bool | None = Query(None, description="Nur angepinnte"),
    is_archived: bool | None = Query(False, description="Archivierte Notizen anzeigen"),
    sort_by: str = Query("updated_at", description="updated_at | created_at | title"),
    order: str = Query("desc", description="desc | asc"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Gibt Notizen des Benutzers zurück."""
    _check_notes_enabled()
    return NotesService.get_notes(
        db=db,
        user=user,
        category=category,
        team_id=team_id,
        search=search,
        is_pinned=is_pinned,
        is_archived=is_archived,
        sort_by=sort_by,
        order=order,
    )


@router.post("", response_model=NoteResponse, status_code=201)
def create_note(
    req: NoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    """Erstellt eine neue Notiz."""
    _check_notes_enabled()
    try:
        return NotesService.create_note(
            db=db,
            user=user,
            title=req.title,
            content=req.content,
            category=req.category,
            color=req.color,
            is_pinned=req.is_pinned,
            note_type=req.note_type,
            team_id=req.team_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{id_or_uid}", response_model=NoteResponse)
def get_note(
    id_or_uid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Liest eine einzelne Notiz."""
    _check_notes_enabled()
    try:
        return NotesService.get_note(db=db, user=user, note_id_or_uid=id_or_uid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{id_or_uid}", response_model=NoteResponse)
@router.patch("/{id_or_uid}", response_model=NoteResponse)
def update_note(
    id_or_uid: str,
    req: NoteUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    """Aktualisiert eine Notiz."""
    _check_notes_enabled()
    try:
        return NotesService.update_note(
            db=db,
            user=user,
            note_id_or_uid=id_or_uid,
            title=req.title,
            content=req.content,
            category=req.category,
            color=req.color,
            is_pinned=req.is_pinned,
            is_archived=req.is_archived,
            note_type=req.note_type,
            team_id=req.team_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{id_or_uid}")
def delete_note(
    id_or_uid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    """Löscht eine Notiz."""
    _check_notes_enabled()
    try:
        return NotesService.delete_note(db=db, user=user, note_id_or_uid=id_or_uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id_or_uid}/pin", response_model=NoteResponse)
def toggle_note_pin(
    id_or_uid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    """Schaltet den Pin-Status einer Notiz um."""
    _check_notes_enabled()
    try:
        return NotesService.toggle_pin(db=db, user=user, note_id_or_uid=id_or_uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id_or_uid}/archive", response_model=NoteResponse)
def toggle_note_archive(
    id_or_uid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    """Schaltet den Archiv-Status einer Notiz um."""
    _check_notes_enabled()
    try:
        return NotesService.toggle_archive(db=db, user=user, note_id_or_uid=id_or_uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
