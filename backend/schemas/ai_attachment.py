"""Metadaten-only API fuer isolierte AI-Anhaenge."""

from datetime import datetime

from pydantic import BaseModel


class AiAttachmentResponse(BaseModel):
    id: str
    conversation_id: str
    # Die Nachricht, mit der dieser Anhang abgeschickt wurde. `None` heisst:
    # hochgeladen, aber noch nicht gesendet — die Oberflaeche zeigt ihn dann als
    # Chip ueber dem Eingabefeld, alle anderen stehen in ihrer Nachricht.
    message_id: str | None = None
    original_name: str
    media_type: str
    size_bytes: int
    status: str
    rejection_code: str | None
    # Wieviele Stellen beim Aufnehmen unkenntlich gemacht wurden. Sichtbar,
    # damit niemand sich wundert, warum im hochgeladenen Log ploetzlich
    # `[REDACTED]` steht — und damit klar ist, dass das Geheimnis den Anbieter
    # nie erreicht hat.
    redacted_spans: int | None = None
    created_at: datetime
