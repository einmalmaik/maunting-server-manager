"""API-Vertraege fuer Prosa-Skills.

Ein Skill ist Text, kein Programm. Deshalb gibt es hier keinen Schritt-Typ
mehr: die Allowlist, die frueher jeden Tool-Aufruf einzeln pruefen musste, ist
mit dem Makro entfallen. Was bleibt, sind Laengengrenzen — und die
Beschreibung, die das Modell zur Auswahl braucht.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SkillScope = Literal["shipped", "global", "team"]
SkillOrigin = Literal["shipped", "operator", "ai"]
SkillStatus = Literal["active", "pending"]


class AiSkillWrite(BaseModel):
    skill_key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    # Sie entscheidet, ob das Modell den Skill ueberhaupt anfasst — nur sie und
    # der Name stehen dauerhaft im Prompt. Deshalb Pflicht und nicht optional.
    description: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=12_000)
    # NULL bedeutet global. Ein Team-Skill braucht die Nummer seines Teams.
    team_id: int | None = Field(default=None, ge=1)
    enabled: bool = True


class AiSkillToggle(BaseModel):
    enabled: bool


class AiSkillSummary(BaseModel):
    """Verzeichniseintrag ohne Text — das, was auch das Modell zuerst sieht."""

    id: str | None = None
    skill_key: str
    name: str
    description: str
    scope: SkillScope
    origin: SkillOrigin
    team_id: int | None = None
    status: SkillStatus = "active"
    enabled: bool = True
    # Mitgelieferte Skills sind nicht aenderbar, nur ueberschreibbar.
    editable: bool = False


class AiSkillDetail(AiSkillSummary):
    body: str


class AiSkillManaged(BaseModel):
    """Eine Datenbankzeile in der Verwaltung, einschliesslich abgeschalteter."""

    id: str
    skill_key: str
    name: str
    description: str
    body: str
    scope: Literal["global", "team"]
    origin: Literal["operator", "ai"]
    team_id: int | None
    status: SkillStatus
    enabled: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    # Abdruck ueber Name+Beschreibung+Text. Die Freigabe schickt ihn zurueck
    # und bestaetigt damit den gelesenen Inhalt, nicht bloss die Zeile —
    # sonst liesse sich der Text im Zeitfenster zwischen Lesen und Klicken
    # unbemerkt austauschen (TOCTOU).
    fingerprint: str


class AiSkillApprove(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
