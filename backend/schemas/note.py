"""API-Verträge für Notizen, Checklisten und Team-Notizen."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class NoteCreate(BaseModel):
    note_uid: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=65536)
    content: str = Field(default="")
    category: str = Field(default="personal", max_length=64)
    color: str | None = Field(default="primary", max_length=32)
    is_pinned: bool = False
    note_type: str = Field(default="personal", max_length=32)
    team_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=65536)
    content: str | None = None
    category: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    is_pinned: bool | None = None
    is_archived: bool | None = None
    note_type: str | None = Field(default=None, max_length=32)
    team_id: int | None = None


_ABDRUCK = r"^[A-Za-z0-9+/]{22}$"


class Kontoschluessel(BaseModel):
    """Der Fingerabdruck des Notizschluessels, der fuer alle Geraete gilt — nie der Schluessel.

    Mit der Unterschrift des Geraets, das ihn gesetzt hat: die Geraete pruefen
    sie selbst und glauben dem Server den Abdruck nicht ohne sie.
    """

    abdruck: str | None = None
    stand: int = 0
    geraet: str | None = None
    signatur: str | None = None


class KontoschluesselSetzen(BaseModel):
    abdruck: str = Field(pattern=_ABDRUCK)
    # Der naechste Stand: genau einer mehr als der geltende. Zwei Geraete, die
    # zugleich ihren Schluessel melden, ueberschreiben einander so nicht, und
    # eine alte Unterschrift passt auf keinen spaeteren Stand.
    stand: int = Field(ge=1)
    geraet: str = Field(min_length=1, max_length=64)
    signatur: str = Field(min_length=1, max_length=128)


# Ein Chiffrat ist groesser als sein Klartext (Base64, Nonce, Tag). Die Grenze
# haelt diesen Weg nur davon ab, mehr anzunehmen als die gewohnten.
_CHIFFRAT_MAX = 1_000_000
_SAMMEL_MAX = 200


class Umschluesselung(BaseModel):
    """Ein Feld, neu verschluesselt: gilt nur, solange ``alt`` noch dort steht."""

    alt: str = Field(min_length=1, max_length=_CHIFFRAT_MAX)
    neu: str = Field(min_length=1, max_length=_CHIFFRAT_MAX)


class NotizUmschluesselung(BaseModel):
    note_uid: str = Field(min_length=1, max_length=64)
    title: Umschluesselung | None = None
    content: Umschluesselung | None = None


class NotizenNeuVerschluesseln(BaseModel):
    eintraege: list[NotizUmschluesselung] = Field(min_length=1, max_length=_SAMMEL_MAX)


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_uid: str
    user_id: int
    creator_name: str | None = None
    title: str
    content: str
    category: str
    color: str | None = "primary"
    is_pinned: bool
    is_archived: bool
    note_type: str
    team_id: int | None = None
    team_name: str | None = None
    can_edit: bool = True
    created_at: datetime
    updated_at: datetime
