from __future__ import annotations

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import User, PanelPopup, UserPopupState
from schemas.popup import (
    PanelPopupResponse,
    PanelPopupCreate,
    PanelPopupUpdate,
    PopupDismissRequest,
)

router = APIRouter(prefix="/api/popups", tags=["popups"])


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/active", response_model=PanelPopupResponse | None)
def get_active_popup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PanelPopup | None:
    """Ermittelt das aktuell aktive Pop-up für den angemeldeten Benutzer.

    Berücksichtigt Gültigkeitszeitraum, Deaktivierung, permanente Ausblendung
    sowie die 24-Stunden-Schlummerfrist ("snooze").
    """
    now = datetime.now(timezone.utc)
    twenty_four_hours_ago = now - timedelta(hours=24)

    # 1. Alle aktiven Popups holen, deren Zeitfenster passt
    popups = (
        db.query(PanelPopup)
        .filter(
            PanelPopup.is_active == True,
            or_(PanelPopup.start_at.is_(None), PanelPopup.start_at <= now),
            or_(PanelPopup.end_at.is_(None), PanelPopup.end_at >= now),
        )
        .order_by(desc(PanelPopup.id))
        .all()
    )

    if not popups:
        return None

    # 2. Dismiss-Zustände des Nutzers abfragen
    popup_ids = [p.id for p in popups]
    user_states = {
        s.popup_id: s
        for s in db.query(UserPopupState)
        .filter(UserPopupState.user_id == user.id, UserPopupState.popup_id.in_(popup_ids))
        .all()
    }

    # 3. Erstes nicht ausgeblendetes Pop-up finden
    for p in popups:
        state = user_states.get(p.id)
        if state:
            if state.dismissed_permanently:
                continue
            last_dismissed = _as_utc(state.last_dismissed_at)
            if last_dismissed and last_dismissed > twenty_four_hours_ago:
                continue
        return p

    return None


@router.post("/{popup_id}/dismiss")
def dismiss_popup(
    popup_id: int,
    req: PopupDismissRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _=Depends(verify_csrf),
) -> dict:
    """Schließt oder blendet ein Pop-up für den Benutzer aus.

    - "snooze": Wird nach 24 Stunden erneut angezeigt (sofern noch aktiv).
    - "permanent": Wird für diesen Benutzer dauerhaft nicht mehr angezeigt.
    """
    popup = db.query(PanelPopup).filter(PanelPopup.id == popup_id).first()
    if not popup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pop-up nicht gefunden")

    now = datetime.now(timezone.utc)
    is_permanent = req.mode == "permanent"

    state = (
        db.query(UserPopupState)
        .filter(UserPopupState.user_id == user.id, UserPopupState.popup_id == popup_id)
        .first()
    )

    if state:
        state.dismissed_permanently = is_permanent
        state.last_dismissed_at = now
    else:
        state = UserPopupState(
            user_id=user.id,
            popup_id=popup_id,
            dismissed_permanently=is_permanent,
            last_dismissed_at=now,
        )
        db.add(state)

    db.commit()
    return {"ok": True, "mode": req.mode}


# ── Admin-Verwaltung ─────────────────────────────────────────────────────────

@router.get("/admin/list", response_model=list[PanelPopupResponse])
def list_admin_popups(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.read")),
) -> list[PanelPopup]:
    """Listet alle Pop-ups für Administratoren auf."""
    return db.query(PanelPopup).order_by(desc(PanelPopup.id)).all()


@router.post("/admin", response_model=PanelPopupResponse, status_code=status.HTTP_201_CREATED)
def create_admin_popup(
    req: PanelPopupCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> PanelPopup:
    """Erstellt ein neues Pop-up / eine Ankündigung."""
    popup = PanelPopup(
        title=req.title.strip(),
        content_markdown=req.content_markdown.strip(),
        is_active=req.is_active,
        start_at=req.start_at,
        end_at=req.end_at,
        button_text=req.button_text.strip() if req.button_text else None,
        button_url=req.button_url.strip() if req.button_url else None,
        created_by_user_id=user.id,
    )
    db.add(popup)
    db.commit()
    db.refresh(popup)
    return popup


@router.put("/admin/{popup_id}", response_model=PanelPopupResponse)
def update_admin_popup(
    popup_id: int,
    req: PanelPopupUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> PanelPopup:
    """Aktualisiert ein bestehendes Pop-up."""
    popup = db.query(PanelPopup).filter(PanelPopup.id == popup_id).first()
    if not popup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pop-up nicht gefunden")

    if req.title is not None:
        popup.title = req.title.strip()
    if req.content_markdown is not None:
        popup.content_markdown = req.content_markdown.strip()
    if req.is_active is not None:
        popup.is_active = req.is_active
    if req.start_at is not None or "start_at" in req.model_fields_set:
        popup.start_at = req.start_at
    if req.end_at is not None or "end_at" in req.model_fields_set:
        popup.end_at = req.end_at
    if req.button_text is not None or "button_text" in req.model_fields_set:
        popup.button_text = req.button_text.strip() if req.button_text else None
    if req.button_url is not None or "button_url" in req.model_fields_set:
        popup.button_url = req.button_url.strip() if req.button_url else None

    popup.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(popup)
    return popup


@router.delete("/admin/{popup_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_admin_popup(
    popup_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> Response:
    """Löscht ein Pop-up samt Zuständen."""
    popup = db.query(PanelPopup).filter(PanelPopup.id == popup_id).first()
    if not popup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pop-up nicht gefunden")

    db.delete(popup)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
