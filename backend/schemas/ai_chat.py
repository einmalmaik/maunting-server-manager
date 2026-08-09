"""API-Vertraege fuer die eine persistente AI-Unterhaltung."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AiConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class AiMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    # Denkschritte des Modells, sofern es welche geliefert hat. Getrennt von
    # `content`, damit die Oberflaeche sie einklappen kann und niemand sie fuer
    # die Antwort haelt.
    reasoning: str | None = None
    status: str
    provider_id: int | None
    model: str | None
    created_at: datetime


class AiConversationDetail(AiConversationResponse):
    messages: list[AiMessageResponse] = Field(default_factory=list)


class AiChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16_000)
    provider_id: int = Field(ge=1)
    request_id: UUID
    # Bittet das Modell, seine Denkschritte mitzuliefern. Ein Anbieter oder
    # Modell, das damit nichts anfangen kann, ignoriert das Feld — dann kommt
    # schlicht kein Denkschritt zurueck und die Antwort bleibt unveraendert.
    reasoning: bool = False


class AiMessageEdit(BaseModel):
    """Eine bereits gesendete eigene Nachricht neu formulieren.

    Der neue Text ersetzt die alte Nachricht nicht — sie und alles Spaetere
    verschwinden, und der Text wird als neue Nachricht gesendet. Anders waere
    der Verlauf widerspruechlich: die verworfene Fassung stuende weiter im
    Kontext und das Modell wuerde sie beruecksichtigen.
    """

    content: str = Field(min_length=1, max_length=16_000)


class AiMessageEditResponse(BaseModel):
    # Wie viele Zeilen der Schnitt entfernt hat. Die Oberflaeche zeigt es an,
    # damit niemand raetselt, wohin der halbe Verlauf verschwunden ist.
    removed: int
