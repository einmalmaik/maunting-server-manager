"""API-Vertrag der Autonomie-Freigaben."""

from datetime import datetime

from pydantic import BaseModel, Field

from models.ai_autonomy_grant import (
    DEFAULT_MAX_ACTIONS_PER_HOUR,
    MAX_ACTIONS_PER_HOUR_LIMIT,
)


class AiAutonomyGrantWrite(BaseModel):
    # NULL = panelweit. Eine panelweite Freigabe ist ausdruecklich moeglich,
    # aber sie ist die groessere Entscheidung — deshalb muss sie hier bewusst
    # als `null` gesendet werden und entsteht nicht als Nebenwirkung.
    server_id: int | None = Field(default=None, ge=1)
    enabled: bool = True
    max_actions_per_hour: int = Field(
        default=DEFAULT_MAX_ACTIONS_PER_HOUR, ge=0, le=MAX_ACTIONS_PER_HOUR_LIMIT
    )


class AiAutonomyGrantResponse(BaseModel):
    id: int
    server_id: int | None
    enabled: bool
    max_actions_per_hour: int
    # Wieviel des Stundenbudgets in den letzten 60 Minuten verbraucht wurde.
    # Ohne diese Zahl waere fuer den Benutzer nicht erklaerbar, warum eine
    # Aktion ploetzlich wieder eine Bestaetigung verlangt.
    used_last_hour: int
    created_at: datetime
    updated_at: datetime
