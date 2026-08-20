"""Der Controller über den Anbietern — sammelt sie ein und löst sie auf.

Vorher trug der Betreiber eine beliebige Basis-URL ein. Das war flexibel und
teuer: MSM wusste nichts über das Ziel, brauchte deshalb eine SSRF-Prüfung mit
IP-Pinning gegen umgeschriebene DNS-Antworten, und konnte über das Modell
dahinter keine einzige Aussage treffen. Welche Denkstufen ein Modell kennt, ob
es überhaupt nachdenkt, ob man das abschalten kann — alles unbekannt, weil
hinter der URL alles Mögliche stehen konnte.

Jetzt wählt der Betreiber einen **Anbieter aus diesem Paket**. Die Adresse
gehört damit MSM und nicht der Eingabe, und mit dem Anbieter steht auch fest, wo
sein Modellkatalog liegt. Aus dem Katalog kommen die Fähigkeiten des gewählten
Modells — statt sie zu raten oder eine Liste von Hand zu pflegen.

**Mit einer Ausnahme, seit es Azure gibt.** Dort hat jede Ressource ihren
eigenen Host, und ohne dessen Namen ist der Anbieter nicht erreichbar. Diese
Anbieter tragen ``ressource_noetig`` und in `base_url` eine **Vorlage** mit
genau einer Lücke; Schema, Suffix und Pfad bleiben im Programm, der Betreiber
steuert ein einzelnes DNS-Label bei. Das ist ausdrücklich nicht die Rückkehr
der freien Basis-URL — was der Unterschied wert ist und welches Restrisiko
bleibt, steht am Feld `basis.Anbieter.ressource_noetig`. Ein Anbieter ohne
diese Marke hat weiterhin gar keine Eingabe in seiner Adresse.

Ein Anbieter **ohne Katalog** (``catalog_url=None``) ist die zweite Neuerung
aus derselben Ecke: bei Azure heisst ein Modell so, wie der Betreiber sein
Deployment genannt hat, und eine Liste dafür gibt es nicht. Die Fähigkeiten
kommen dann über `faehigkeiten_aus` aus einem fremden Katalog — oder gar nicht,
und „gar nicht" heisst wie immer „unbekannt" und nie „klein".

**Die Bauart, und sie ist der eigentliche Inhalt dieser Datei:**

* `basis` — was ein Anbieter ist (`Anbieter`) und was aus seinem Katalog
  herauskommt (`Modell`). Keine Logik, kein Anbietername.
* Je Anbieter **eine Datei** (`openrouter`, `openai`, `elevenlabs`,
  `azure_openai`, `azure_anthropic`). Darin sein ``ANBIETER``-Eintrag und sein
  ``katalog_lesen``. Alles, was MSM über diesen Anbieter weiß, steht dort und
  nirgends sonst.
* Diese Datei — der Controller. Sie sammelt die Module ein und beantwortet die
  vier Fragen, die der Rest des Programms stellt: *welche gibt es*, *wer ist
  das*, *kennst du den*, *spricht der mein Protokoll*.

**Ein weiterer Anbieter ist eine neue Datei und eine Zeile in ``_MODULE``.** Ein
entfernter Anbieter ist eine gelöschte Datei und eine gelöschte Zeile — es
bleibt nichts zurück, weil es außerhalb seiner Datei nichts gab. Genau dafür ist
das ein Paket und keine Datei mit allen Anbietern darin: bei zehn Anbietern wäre
das eine Datei, die niemand mehr liest, und jede Änderung an einem Anbieter
stünde im selben Diff wie alle anderen.

**Der Controller wächst dabei nicht mit.** Er nennt keinen Anbieter beim Namen
außer in ``_MODULE``, hat keine Verzweigung über ``kind`` und kennt keine
Adresse. Was einen Anbieter vom anderen unterscheidet, ist ein **Feld** in
`basis.Anbieter` — Adresse, Kopfzeile, Wortschatz, Hörwege —, und ein Feld
kostet den Controller nichts. Ein ``if kind == …`` hier wäre der Anfang der
Datei, die dieses Paket ersetzt hat.

Bewusst **keine** Klassenhierarchie mit einer Basisklasse je Anbieter. Ein
Anbieter unterscheidet sich in Werten, nicht in Verhalten; das einzige Verhalten
ist ``katalog_lesen``, und dafür genügt eine Funktion je Modul.

**Das Gehör hat keinen eigenen Eintrag.** Es hängt an einem Chatzugang, weil es
entweder dessen ``/audio/transcriptions`` benutzt oder dessen
``/chat/completions`` (`ai_stt`). Welche Wege ein Anbieter dafür kennt, sagt
`gehoer_wege` — und dass das ein zusätzliches Feld ist und keine Fähigkeitsmenge
neben `protokoll`, ist am Feld selbst begründet.
"""

from __future__ import annotations

from typing import Callable

from . import azure_anthropic, azure_openai, elevenlabs, openai, openrouter
from .basis import Anbieter, Modell, positive_zahl


__all__ = [
    "ANBIETER",
    "Anbieter",
    "CHAT",
    "Modell",
    "TTS",
    "alle",
    "anbieter",
    "bekannt",
    "katalog_leser",
    "positive_zahl",
    "spricht",
]

#: **Die eine Verkabelungszeile.** Ein neuer Anbieter kommt hier dazu, ein alter
#: fällt hier heraus — sonst nichts. Die Reihenfolge ist die Reihenfolge der
#: Einführung und hat keine Bedeutung; ausgeliefert wird nach Beschriftung
#: sortiert (`alle`).
_MODULE = (openrouter, openai, elevenlabs, azure_openai, azure_anthropic)

#: Alle Anbieter, nach ihrem gespeicherten Schlüssel. Gebaut aus den Modulen,
#: nicht von Hand geführt: eine zweite Liste wäre eine zweite Wahrheit, und die
#: erste vergessene Zeile darin ein Anbieter, den es zur Hälfte gibt.
ANBIETER: dict[str, Anbieter] = {modul.ANBIETER.kind: modul.ANBIETER for modul in _MODULE}

#: Je Anbieter sein Katalogleser, aus derselben Quelle. Deshalb kann es einen
#: Anbieter ohne Leser gar nicht geben — der Fall, den `ai_model_catalog` früher
#: mit einem ``KeyError`` mitten im Abruf bemerkt hätte.
_LESER: dict[str, Callable[[dict], Modell | None]] = {
    modul.ANBIETER.kind: modul.katalog_lesen for modul in _MODULE
}

#: Die Protokolle als Konstante. Ein vertipptes ``"chat_completion"`` (ohne s)
#: fiele sonst nirgends auf: `spricht()` gäbe schlicht ``False`` zurück, und die
#: Providerauswahl wäre leer statt kaputt.
CHAT = "chat_completions"
TTS = "tts"


def anbieter(kind: str) -> Anbieter:
    """Löst einen gespeicherten Schlüssel auf.

    Ein unbekannter Schlüssel ist kein Benutzerfehler, sondern ein Datenstand,
    den es nicht geben dürfte — etwa eine Zeile aus einer Zukunftsversion nach
    einem Downgrade, oder ein Anbieter, dessen Datei entfernt wurde, während
    seine Zugänge in der Datenbank stehen blieben. Deshalb ``KeyError`` und
    keine leise Rückgabe von ``None``: ein Provider ohne Adresse darf nicht bis
    zum HTTP-Aufruf durchkommen.
    """
    try:
        return ANBIETER[kind]
    except KeyError as exc:
        raise KeyError(f"Unbekannter KI-Anbieter: {kind!r}") from exc


def bekannt(kind: str) -> bool:
    return kind in ANBIETER


def alle() -> list[Anbieter]:
    return sorted(ANBIETER.values(), key=lambda item: item.label)


def spricht(kind: str, protokoll: str) -> bool:
    """Spricht dieser Anbieter das verlangte Protokoll?

    Die Frage stellen beide Wege, bevor sie einen Zugang annehmen: der
    Chat-Router verlangt ``chat_completions``, die Stimme ``tts``. Ein
    unbekannter Schlüssel ist hier ausdrücklich **kein** Fehler, sondern ein
    „nein" — anders als bei `anbieter()`. Der Unterschied hat einen Grund: hier
    wird gefiltert, dort aufgelöst. Eine Filterfunktion, die bei einer
    unerwarteten Zeile eine Ausnahme wirft, nimmt die ganze Liste mit statt nur
    den einen Eintrag.
    """
    spec = ANBIETER.get(kind)
    return spec is not None and spec.protokoll == protokoll


def katalog_leser(kind: str) -> Callable[[dict], Modell | None]:
    """Die Lesefunktion für den Katalog dieses Anbieters.

    Der Controller **leitet hier nur weiter** und liest selbst nichts: was ein
    Katalogeintrag bedeutet, weiß nur der Anbieter, der ihn geschrieben hat.
    `ai_model_catalog` holt, prüft die Größe, packt die Liste aus und speichert
    — die Übersetzung eines einzelnen Eintrags in ein `Modell` macht diese
    Funktion hier ausfindig.

    ``KeyError`` aus demselben Grund wie bei `anbieter()`: ein Katalog ohne
    Leser ist kein Sonderfall, den man leise überspringt, sondern ein Zustand,
    den die Bauart ausschließt.
    """
    try:
        return _LESER[kind]
    except KeyError as exc:
        raise KeyError(f"Unbekannter KI-Anbieter: {kind!r}") from exc


#: Hier stand ``mit_protokoll(protokoll)`` — „alle Anbieter, die dieses
#: Protokoll sprechen". Geschrieben für einen Aufrufer, der nie kam: gefiltert
#: wird überall über `spricht()` an einer bereits vorliegenden Zeile, nicht über
#: eine Vorauswahl der Liste. Entfernt beim Einbau des dritten Anbieters, weil
#: eine ungenutzte Funktion beim nächsten Umbau mitwandert und dabei aussieht,
#: als hinge etwas an ihr.
