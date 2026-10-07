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


#: Was fuer eine Aussage eine Erinnerung ist (`ai_memory_service.ARTEN`).
MemoryArt = Literal["fakt", "vorliebe", "anweisung", "ereignis", "plan", "beziehung", "wissen"]
#: Woher sie kommt (`ai_memory_service.QUELLEN`).
MemoryQuelle = Literal["eingetragen", "gespraech", "import", "pflege"]


class AiMemoryCreate(BaseModel):
    """Eine Erinnerung, wie ein Mensch sie eintraegt: ein Satz, mehr nicht.

    Titel und Thema sind freiwillig. Einen Namen gibt es nicht mehr; er war
    bis Gedaechtnis v2 (06.10.2026) die Identitaet einer Erinnerung, und
    dieselbe Sache unter zwei Namen stand zweimal da.
    """

    scope: MemoryScope
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=2000)
    titel: str | None = Field(default=None, max_length=120)
    thema: str | None = Field(default=None, max_length=60)


class AiMemoryUpdate(BaseModel):
    """Aenderung an einer Erinnerung. Was fehlt, bleibt, wie es ist.

    ``titel`` und ``thema`` unterscheiden "nicht genannt" (bleibt) von
    ``null`` (entfernen); deshalb liest der Router `model_fields_set`.
    ``fassung`` ist die Fassung, die der Aendernde gesehen hat: hat
    inzwischen jemand anderes geschrieben, lehnt der Server mit 409 ab.
    """

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    titel: str | None = Field(default=None, max_length=120)
    thema: str | None = Field(default=None, max_length=60)
    fassung: int = Field(ge=1)


class AiMemoryAnheften(BaseModel):
    """Anheften oder lösen: angeheftet steht eine Erinnerung immer im Kopf."""

    angeheftet: bool


class AiMemoryFassungZurueck(BaseModel):
    """Zurueck auf eine fruehere Fassung — mit der Fassung, die man gesehen hat."""

    fassung: int = Field(ge=1)


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


class AiMemoryThemaRef(BaseModel):
    id: str
    name: str


class AiMemoryResponse(BaseModel):
    id: str
    scope: MemoryScope
    server_id: int | None
    team_id: int | None = None
    #: Der alte Name, nur bei Altbestand. Neue Erinnerungen haben keinen.
    key: str | None = None
    #: Der Text der Erinnerung (der Feldname stammt aus der Zeit von Name und Wert).
    value: str
    titel: str | None = None
    thema: AiMemoryThemaRef | None = None
    art: MemoryArt | None = None
    quelle: MemoryQuelle = "eingetragen"
    wichtigkeit: int = 3
    # "user" = du hast es hinterlegt, "ai" = die KI hat es sich gemerkt.
    # Sichtbar, damit niemand raten muss, woher ein Eintrag stammt.
    origin: Literal["user", "ai"] = "user"
    #: ``vergessen`` heisst: die KI hat sie auf Wunsch vergessen, sie laesst
    #: sich bis `vergessen_am` plus 30 Tage zurueckholen.
    status: Literal["aktiv", "vergessen"] = "aktiv"
    vergessen_am: datetime | None = None
    #: Die Fassung, die eine Aenderung als gesehen mitschickt.
    fassung: int = 1
    #: Steht immer im Kopf jedes Gesprächs (`ai_gedaechtnis_abruf`).
    angeheftet: bool = False
    use_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AiMemoryThema(BaseModel):
    """Ein Thema einer Ansicht und wieviele geltende Erinnerungen darunter stehen."""

    id: str
    name: str
    anzahl: int


class AiMemoryFassung(BaseModel):
    """Eine fruehere Fassung einer Erinnerung."""

    id: str
    text: str
    titel: str | None = None
    #: ``bearbeitet``, ``aktualisiert``, ``zusammengefuehrt``, ``aufgenommen``,
    #: ``umgeschrieben`` oder ``wiederhergestellt``.
    grund: str
    #: Wer die Aenderung gemacht hat, die diese Fassung abgeloest hat.
    von: Literal["user", "ai"]
    erstellt: datetime


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


#: Wieviele Erinnerungen ein Import höchstens trägt. Jede kostet beim
#: Übernehmen eine Verschlüsselung im DIS-Sidecar und eine Einbettung.
MAX_IMPORT_ITEMS = 200
#: Wie viele bestehende Einträge eine importierte Erinnerung höchstens
#: ersetzen darf. So viele bietet der Bestand je Teil an (40 je Bereich), und
#: dieselbe Zahl kappt die Vorschau — sonst scheiterte die Übernahme eines
#: großen Zusammenführens am Schema.
MAX_IMPORT_ERSETZT = 40


class AiMemoryImportPreviewRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=100_000)
    scope: MemoryScope = "user"
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    source_provider: str | None = Field(default=None, max_length=40)


class AiMemoryImportBisher(BaseModel):
    """Ein Bestandseintrag, den ein Vorschlag ersetzen würde.

    ``fassung`` geht beim Übernehmen zurück: hat ihn inzwischen jemand
    geändert, wird nicht still überschrieben.
    """

    id: str
    fassung: int
    text: str
    titel: str | None = None


class AiMemoryImportPreviewItem(BaseModel):
    """Eine Erinnerung, wie das Gedächtnismodell sie aus dem Text gelesen hat."""

    text: str
    titel: str | None = None
    thema: str | None = None
    art: MemoryArt | None = None
    wichtigkeit: int = 3
    #: Was darin aufgeht — der erste Eintrag bekommt den neuen Text, die
    #: übrigen werden in ihn aufgenommen. Leer heißt: eine neue Erinnerung.
    ersetzt: list[AiMemoryImportBisher] = []


class AiMemoryImportPreviewResponse(BaseModel):
    detected_source: str | None
    items: list[AiMemoryImportPreviewItem]
    #: Alles, was das Modell vorschlug — auch jenseits von ``MAX_IMPORT_ITEMS``.
    total_detected: int
    #: Was schon genau so dasteht; es erscheint nicht in ``items``.
    total_known: int = 0
    #: Was nach Zugangsdaten aussah; es wird nie gespeichert und nie gezeigt.
    total_secrets_blocked: int = 0
    #: Teile des Textes, die das Modell nicht gelesen hat (Anbieter, Kontingent).
    unread_parts: int = 0
    #: Wieviele **neue** Erinnerungen der Bereich noch fasst. Ersetzen kostet
    #: keinen Platz. ``None`` heißt unbegrenzt.
    available_slots: int | None
    #: Ob die KI persönliche Einträge heute überhaupt liest. Ein Import in ein
    #: ausgeschaltetes Gedächtnis ist erlaubt — er bleibt nur liegen, bis der
    #: Schalter umgelegt wird, und das soll niemand erst hinterher merken.
    memory_enabled: bool = True


class AiMemoryImportErsetzt(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    fassung: int = Field(ge=1)


class AiMemoryImportItem(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    titel: str | None = Field(default=None, max_length=120)
    thema: str | None = Field(default=None, max_length=60)
    art: MemoryArt | None = None
    wichtigkeit: int = Field(default=3, ge=1, le=5)
    ersetzt: list[AiMemoryImportErsetzt] = Field(default_factory=list, max_length=MAX_IMPORT_ERSETZT)


class AiMemoryImportRequest(BaseModel):
    items: list[AiMemoryImportItem] = Field(min_length=1, max_length=MAX_IMPORT_ITEMS)
    scope: MemoryScope = "user"
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    source_provider: str | None = Field(default=None, max_length=40)


class AiMemoryImportSkipped(BaseModel):
    #: Die Stelle in ``items`` der Anfrage.
    index: int
    #: ``full`` (Bereich voll oder gesperrt), ``rejected`` (ungültig,
    #: Zugangsdaten, fremder Eintrag), ``duplicate`` (derselbe Eintrag zweimal
    #: ersetzt), ``conflict`` (inzwischen geändert oder vergessen).
    reason: Literal["full", "rejected", "duplicate", "conflict"]


class AiMemoryImportResponse(BaseModel):
    imported_count: int
    updated_count: int
    skipped_count: int
    skipped: list[AiMemoryImportSkipped] = []
