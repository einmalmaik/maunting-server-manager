"""Die Aufgabenliste: stehende Aufträge lesen und verwalten — auch ohne Chat.

Bis zum 20.08.2026 gab es diesen Router bewusst nicht; Aufgaben wurden
ausschließlich im Chat über die KI-Werkzeuge verwaltet. Der Betreiber hat das
umgedreht: die Aufgabenliste ist jetzt eine eigene Ansicht neben dem Chat, und
alles, was die KI kann (anlegen, ändern, pausieren, löschen), kann der
Benutzer dort auch — manuell, mit drei Angaben: Name, Auftragstext, Zeitplan.

Kein zweiter Fachweg: jede Route ruft dieselben Funktionen aus
`ai_task_service`, durch die auch die Chat-Werkzeuge gehen. Damit gelten
dieselben Prüfungen an derselben Stelle — Mengengrenze, Zeitzonenpflicht,
Redaktion des Auftragstexts, und `kind='act'` nur mit autonomer Freigabe.

`ai.tasks.manage` ist dasselbe Recht wie an den Werkzeugen. Wer es nicht hat,
sieht die Liste nicht — es gibt hier nichts, das man mit weniger dürfte.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import User
from services import ai_task_service
from services.ai_action_errors import AiActionValidationError


router = APIRouter(prefix="/api/ai/tasks", tags=["ai-tasks"])


class AiTaskWrite(BaseModel):
    """Die Felder der Aufgabenliste — Teilangaben sind erlaubt.

    Die eigentliche Prüfung (Formate, Grenzen, Pflichtfelder beim Anlegen)
    liegt im Dienst; hier stehen nur grobe Typen und Längen, damit offenkundig
    kaputte Anfragen gar nicht erst dort ankommen. `exclude_unset` unten
    erhält die Teilangaben-Semantik des Dienstes: nur was genannt ist, wird
    angefasst.
    """

    title: str | None = Field(default=None, max_length=ai_task_service.MAX_TITEL_ZEICHEN)
    instruction: str | None = Field(
        default=None, max_length=ai_task_service.MAX_AUFTRAG_ZEICHEN
    )
    kind: str | None = None
    plan_kind: str | None = None
    time_of_day: str | None = Field(default=None, max_length=8)
    weekdays: list[int] | None = None
    interval_hours: int | None = None
    once_at: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    channel: str | None = None
    enabled: bool | None = None


def _felder(body: AiTaskWrite) -> dict:
    return body.model_dump(exclude_unset=True)


@router.get("", response_model=list[dict])
def list_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.tasks.manage")),
) -> list[dict]:
    """Alle Aufgaben dieses Benutzers — dieselbe Form wie das Chat-Werkzeug."""
    return ai_task_service.auflisten(db, user=user)


@router.post("", response_model=dict, status_code=201)
def create_task(
    body: AiTaskWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.tasks.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    try:
        aufgabe = ai_task_service.anlegen(db, user=user, felder=_felder(body))
    except AiActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return ai_task_service.eintrag(aufgabe)


@router.patch("/{task_id}", response_model=dict)
def update_task(
    task_id: str,
    body: AiTaskWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.tasks.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    try:
        aufgabe = ai_task_service.aendern(
            db, user=user, task_id=task_id, felder=_felder(body)
        )
    except AiActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return ai_task_service.eintrag(aufgabe)


@router.delete("/{task_id}", response_model=dict)
def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.tasks.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    try:
        titel = ai_task_service.loeschen(db, user=user, task_id=task_id)
    except AiActionValidationError as exc:
        # Auch "gibt es nicht" kommt als Prüfungsfehler aus dem Dienst — die
        # Besitzprüfung steckt in der Abfrage, eine fremde Aufgabe sieht aus
        # wie keine (kein Existenzorakel über fremde Kennungen).
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"deleted": True, "title": titel}
