"""Was zwischen Panel und Desktop-App ueber Auftraege gesprochen wird."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DesktopJobResponse(BaseModel):
    """Ein Auftrag, so wie die App ihn bekommt."""

    id: str
    tool_name: str
    arguments: dict


class DesktopJobResultRequest(BaseModel):
    """Das Ergebnis, so wie die App es meldet.

    ``ergebnis`` geht als Werkzeugergebnis an das Modell und ist deshalb
    gedeckelt: eine Datei mit 40 MB Text gehoert nicht in einen Prompt. Die
    App kuerzt selbst und sagt im Ergebnis, dass sie gekuerzt hat — hier steht
    nur die harte Grenze, damit ein Fehler in der App keinen Lauf sprengt.
    """

    ok: bool
    ergebnis: dict = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=64)
