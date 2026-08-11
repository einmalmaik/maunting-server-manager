"""Begrenzte API-Vertraege fuer einsehbares AI-Memory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


#: Die fuenf Schubladen des Gedaechtnisses. Zwei davon haengen an einem Server
#: und sind trotzdem verschieden:
#:
#: * ``server``        — *meine* Notiz zu dieser Anlage. Sieht nur ich.
#: * ``server_shared`` — Betriebswissen der Anlage selbst. Sieht jeder, der den
#:   Server sehen darf; sie ueberlebt den Kollegen, der sie aufschrieb, und
#:   verschwindet mit dem Server.
#:
#: Die Aufzaehlung ist Teil des API-Vertrags: fehlt ein Wert hier, weist
#: FastAPI die Anfrage mit 422 ab, bevor irgendein Dienst sie sieht.
MemoryScope = Literal["user", "server", "server_shared", "team", "panel"]


class AiMemoryWrite(BaseModel):
    scope: MemoryScope
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=2000)


class AiMemoryPreferenceWrite(BaseModel):
    enabled: bool


class AiMemoryNoticeAnswer(BaseModel):
    """Antwort auf den Hinweis vor der ersten Nachricht.

    Zwei unabhaengige Angaben statt dreier Knoepfe: "Nein, nicht mehr anzeigen"
    ist `enable=False, hide_future=True`. So bleibt auch "Ja, und frag mich nie
    wieder" darstellbar, ohne dass die API eine vierte Variante braucht.
    """

    enable: bool
    hide_future: bool = False


class AiMemoryResponse(BaseModel):
    id: str
    scope: MemoryScope
    server_id: int | None
    team_id: int | None = None
    key: str
    value: str
    # "user" = du hast es hinterlegt, "ai" = die KI hat es sich gemerkt.
    # Sichtbar, damit niemand raten muss, woher ein Eintrag stammt.
    origin: Literal["user", "ai"] = "user"
    use_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AiMemoryPreferenceResponse(BaseModel):
    enabled: bool
    # Ob die Oberflaeche den Hinweis vor der naechsten Nachricht zeigen soll.
    # Die Entscheidung faellt im Backend, damit die 24-Stunden-Regel nicht in
    # jedem Client noch einmal nachgebaut werden muss.
    notice_due: bool = False
    notice_hidden: bool = False


class AiMemoryClearResponse(BaseModel):
    """Wieviele Eintraege das Leeren eines Bereichs entfernt hat.

    Eine Zahl statt eines leeren 204: wer gerade sein Gedaechtnis geloescht hat,
    soll sehen, was verschwunden ist — und ob ueberhaupt etwas da war.
    """

    removed: int
