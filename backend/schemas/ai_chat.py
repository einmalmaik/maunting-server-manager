"""API-Vertraege fuer die eine persistente AI-Unterhaltung."""

from datetime import datetime
from typing import Literal
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


class AiSection(BaseModel):
    """Ein Abschnitt einer Antwort — entweder Text oder ein Werkzeugaufruf.

    Zwei Formen in einem Typ statt zweier Listen: die **Reihenfolge zwischen
    ihnen** ist die Information, um die es geht. "Ich sehe mir den Status an" —
    Werkzeug — "der laeuft, jetzt die Logs" — Werkzeug ist etwas anderes als
    derselbe Text mit denselben Werkzeugen in beliebiger Anordnung, und aus zwei
    getrennten Listen laesst sich das nicht wiederherstellen.

    ``werkzeug`` traegt dieselbe Nutzlast wie das `tool`-Ereignis im Stream
    (`_anzeigeeintrag`): Name, Server, Gruppe, Fehlschlag, Skillangaben. Bewusst
    ohne Ergebnis — ein Logausschnitt gehoert nicht ungefragt in den sichtbaren
    Verlauf.
    """

    art: Literal["text", "tool"]
    inhalt: str | None = None
    werkzeug: dict | None = None


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
    # Die Gliederung dieser Antwort: Text und Werkzeuge in der Reihenfolge, in
    # der sie entstanden sind. Damit zeigt der nachgeladene Verlauf dasselbe wie
    # der Live-Strom — vorher endeten die Werkzeuge mit der Verbindung.
    #
    # `None` heisst "aus der Zeit vor dieser Spalte". Die Oberflaeche zeigt
    # solche Nachrichten dann wie immer: als reinen Text aus `content`.
    sections: list[AiSection] | None = None
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
    # Ob das Modell nachdenken soll. Bei 145 der 272 denkenden Modelle bei
    # OpenRouter ist das die einzige Wahl, die es gibt — sie kennen keine Stufen.
    reasoning: bool = False
    # Wie *tief*, falls das Modell Stufen kennt: "minimal" bis "max". Der Wert
    # ist ein Wunsch, keine Anweisung — `ai_reasoning.vorgabe` klemmt ihn auf
    # das, was Modell und Rolle hergeben, bevor er den Server verlaesst.
    reasoning_effort: str | None = Field(default=None, max_length=16)


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


class AiContextStatus(BaseModel):
    """Wie voll der Kontext dieses Gespraechs ist — fuer den Ring beim Absenden.

    ``known`` trennt „das Modell hat ein kleines Fenster“ von „ueber das Modell
    ist nichts bekannt“. Im zweiten Fall zeigt die Oberflaeche ausdruecklich
    keinen Prozentwert: ein geschaetzter saehe genauso aus wie ein gemessener,
    und man wuerde ihm glauben.

    Alle Zahlen sind **Schaetzungen**. MSM rechnet vier Zeichen je Token, statt
    fuer jede Anbieterfamilie einen eigenen Tokenizer mitzuschleppen und ihn mit
    jedem neuen Modell nachzuziehen. Fuer eine Anzeige, deren Zweck „noch viel
    Platz“ oder „gleich wird zusammengefasst“ ist, reicht das; die Oberflaeche
    sagt deshalb „etwa“.
    """

    known: bool
    #: Das volle Fenster des Modells. ``None``, wenn unbekannt.
    window_tokens: int | None = None
    #: Was die Eingabe davon fuellen darf — Antwort und Sicherheit sind ab.
    usable_tokens: int
    #: Was das Gespraech davon gerade belegt. Kann ``usable_tokens``
    #: ueberschreiten: dann wird beim naechsten Zug gekuerzt und gefaltet.
    used_tokens: int
    #: Ab wieviel belegten Tokens zusammengefasst wird. Die Marke wird eher
    #: erreicht als das Falten stattfindet: gefaltet wird nur der **aeltere**
    #: Teil, und die letzten zwoelf Nachrichten zaehlen nie dazu
    #: (`ai_compaction_service.KEEP_RECENT_MESSAGES`). Der Ring darf also kurz
    #: an der Marke stehen, bevor sich etwas tut — das ist richtig so und nicht
    #: die haeufigere Sorte Fehler, naemlich eine Marke, die vorgibt, das Falten
    #: sei schon passiert.
    compaction_at_tokens: int
    #: Dieselbe Marke als Prozentsatz — die Einstellung des Betreibers.
    compaction_percent: int
    #: Ob bereits eine Zusammenfassung im Kontext steckt.
    summarized: bool
