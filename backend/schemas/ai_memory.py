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


class AiMemoryPage(BaseModel):
    """Ein Ausschnitt einer Erinnerungsliste — und was daneben steht.

    Eine nackte Liste hätte hier gereicht, solange ein Bereich hundert Einträge
    fasste. Bei 5.000 nicht mehr: jede Zeile kostet einen eigenen Roundtrip zum
    DIS-Sidecar, und die Seite wäre nach zehn Sekunden noch nicht da. Sie kommt
    deshalb in Stücken — und weil ein Stück für sich genommen lügen würde ("das
    ist alles"), tragen die drei Zahlen die Wahrheit daneben.

    Eine Form für beide Seitenansichten, das eigene Profil und einen einzelnen
    Bereich. Zwei Formen wären zwei Rechnungen für Seitenzahl und nächsten
    Offset, und die Oberfläche müsste beide führen.
    """

    entries: list[AiMemoryResponse]
    #: Alle Zeilen dieser Ansicht zusammen. Steht sichtbar über der Liste, damit
    #: die Seitenweise kein stiller Deckel ist.
    total: int
    #: Davon das, was "Alle löschen" wirklich mitnimmt. Im Profil sind das nur
    #: die allgemeinen Einträge (``scope='user'``) — die Notizen zu einzelnen
    #: Servern stehen in derselben Liste und bleiben stehen. In der Ansicht
    #: eines Bereichs sind es alle. Die Bestätigungsfrage nennt diese Zahl.
    clearable: int
    #: Wie groß eine Seite ist. Bestimmt der Server, weil er sie in
    #: Sidecar-Roundtrips bezahlt; die Oberfläche rechnet daraus ihre Seitenzahl
    #: und den nächsten Offset.
    limit: int


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


#: Was die Vorschau über einen erkannten Fakt sagt.
#:
#: * ``new``              — der Bereich kennt nichts Vergleichbares.
#: * ``exact_duplicate``  — unter diesem Schlüssel steht schon etwas; übernommen
#:   wird nur mit ``replace_existing``.
#: * ``similar_existing`` — ein anderer Schlüssel sagt vermutlich dasselbe.
#: * ``has_secret``       — sieht nach Zugangsdaten aus und wird nie gespeichert.
MemoryImportStatus = Literal["new", "exact_duplicate", "similar_existing", "has_secret"]

#: Wieviele Fakten ein Import höchstens trägt. Jeder kostet beim Übernehmen
#: eine Verschlüsselung im DIS-Sidecar und eine Einbettung.
MAX_IMPORT_ITEMS = 200


class AiMemoryImportPreviewItem(BaseModel):
    key: str
    value: str
    category: str
    evidence: str | None = None
    status: MemoryImportStatus
    existing_key: str | None = None
    #: ``None`` auch dann, wenn der Schlüssel belegt, sein Inhalt aber nicht
    #: mehr lesbar ist.
    existing_value: str | None = None
    similarity: float | None = None


class AiMemoryImportPreviewRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=100_000)
    scope: MemoryScope = "user"
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    source_provider: str | None = Field(default=None, max_length=40)


class AiMemoryImportPreviewResponse(BaseModel):
    detected_source: str | None
    items: list[AiMemoryImportPreviewItem]
    #: Alles, was der Text hergab — auch jenseits von ``MAX_IMPORT_ITEMS``.
    total_detected: int
    total_valid: int
    total_conflicts: int
    total_secrets_blocked: int
    #: Wieviele **neue** Schlüssel der Bereich noch fasst. Überschreiben kostet
    #: keinen Platz.
    available_slots: int
    #: Ob die KI persönliche Einträge heute überhaupt liest. Ein Import in ein
    #: ausgeschaltetes Gedächtnis ist erlaubt — er bleibt nur liegen, bis der
    #: Schalter umgelegt wird, und das soll niemand erst hinterher merken.
    memory_enabled: bool = True


class AiMemoryImportItem(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=2000)
    replace_existing: bool = False


class AiMemoryImportRequest(BaseModel):
    items: list[AiMemoryImportItem] = Field(min_length=1, max_length=MAX_IMPORT_ITEMS)
    scope: MemoryScope = "user"
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    source_provider: str | None = Field(default=None, max_length=40)


class AiMemoryImportSkipped(BaseModel):
    key: str
    #: ``exists`` (Schlüssel belegt, Ersetzen nicht gewählt), ``full``
    #: (Bereich voll), ``rejected`` (ungültig oder Zugangsdaten), ``duplicate``
    #: (derselbe Schlüssel zweimal in einer Anfrage), ``conflict`` (gleichzeitig
    #: geändert).
    reason: Literal["exists", "full", "rejected", "duplicate", "conflict"]


class AiMemoryImportResponse(BaseModel):
    imported_count: int
    updated_count: int
    skipped_count: int
    skipped: list[AiMemoryImportSkipped] = []
