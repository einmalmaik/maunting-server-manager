"""API-Vertraege fuer die eine persistente AI-Unterhaltung."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AiQuestionOption(BaseModel):
    label: str
    hint: str | None = None


class AiQuestionPayload(BaseModel):
    """Eine Rueckfrage der KI, so wie sie im Chat erscheint.

    Dieselbe Form, die `question_payload()` erzeugt und das SSE-Ereignis
    `question` traegt — hier nur fuer den Weg ueber den Verlauf, damit die Frage
    ein Neuladen der Seite ueberlebt.
    """

    question: str
    options: list[AiQuestionOption] = Field(default_factory=list)


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
    # Die Rueckfrage, die diese Nachricht gestellt hat. Sie ist Teil der
    # Nachricht und keine eigene Blase — im Chat erscheint sie unter dem Text
    # derselben Antwort, so wie ein Mensch eine Frage an das Gesagte anhaengt.
    question: AiQuestionPayload | None = None
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


class AiRunResponse(BaseModel):
    """Ein Lauf der KI, so wie die Oberflaeche ihn sieht.

    `live` ist die ehrliche Auskunft, ob dieser Prozess dem Lauf gerade beim
    Arbeiten zusehen kann. Nach einem Neustart des Panels steht ein geparkter
    Lauf weiterhin in der Datenbank, aber niemand haelt ihn im Speicher — die
    Oberflaeche soll dann keinen Ladebalken zeigen, der sich nie bewegt.
    """

    id: str
    status: str
    stop_reason: str | None = None
    message_id: str | None = None
    live: bool = False
    created_at: datetime
