"""Die Anbieter, die MSM nativ unterstützt — einer je Eintrag.

Vorher trug der Betreiber eine beliebige Basis-URL ein. Das war flexibel und
teuer: MSM wusste nichts über das Ziel, brauchte deshalb eine SSRF-Prüfung mit
IP-Pinning gegen umgeschriebene DNS-Antworten, und konnte über das Modell
dahinter keine einzige Aussage treffen. Welche Denkstufen ein Modell kennt, ob
es überhaupt nachdenkt, ob man das abschalten kann — alles unbekannt, weil
hinter der URL alles Mögliche stehen konnte.

Jetzt wählt der Betreiber einen **Anbieter aus dieser Datei**. Die Adresse
gehört damit MSM und nicht der Eingabe, und mit dem Anbieter steht auch fest,
wo sein Modellkatalog liegt. Aus dem Katalog kommen die Fähigkeiten des
gewählten Modells — statt sie zu raten oder eine Liste von Hand zu pflegen.

**Ein weiterer Anbieter ist ein Eintrag in ``ANBIETER`` plus ein Leser in
``ai_model_catalog``.** Beides sind wenige Zeilen. Genau dafür ist das hier eine
eigene Datei: die Anbieterliste wächst, der Rest des Codes nicht.

Bewusst **keine** Klassenhierarchie mit einer Basisklasse je Anbieter. Bei einem
Eintrag wäre das vorauseilende Abstraktion, und der zweite Eintrag zeigt besser
als jede Vorwegnahme, welche Unterschiede tatsächlich abstrahiert gehören.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Anbieter:
    """Ein von MSM unterstützter KI-Anbieter.

    ``kind`` ist der in der Datenbank gespeicherte Schlüssel und darf sich nie
    ändern — er steht in ``ai_providers.provider_kind``.

    ``base_url`` ist fest. Das ist der eigentliche Gewinn gegenüber der freien
    Eingabe: die Adresse stammt aus dem Programm, nicht aus einem Formular, und
    kann deshalb weder auf ein internes Netz noch auf einen umgeschriebenen
    Host zeigen.

    ``key_prefix`` prüft nur die Plausibilität. Ein Schlüssel mit falschem
    Präfix ist mit Sicherheit falsch; einer mit richtigem ist damit noch nicht
    gültig. Die Prüfung erspart dem Betreiber den Umweg über eine
    Fehlermeldung des Anbieters, sie ersetzt sie nicht — deshalb bleibt der
    echte Testaufruf bestehen.
    """

    kind: str
    label: str
    base_url: str
    #: Woher die Modellliste kommt. Ohne Katalog gibt es keine Modellauswahl —
    #: einen solchen Anbieter nimmt MSM derzeit nicht auf.
    catalog_url: str
    #: Wo der Betreiber seinen Schlüssel bekommt. Steht in der Oberfläche.
    key_url: str
    key_prefix: str | None = None
    #: Welches Modell MSM empfiehlt. Das ist die **einzige** Aussage in dieser
    #: Datei, die eine Meinung ist und keine Tatsache — deshalb steht sie hier
    #: und nicht im Katalog. Der Katalog sagt, was ein Modell kann; was sich im
    #: Betrieb bewährt hat, weiß er nicht.
    #:
    #: Die Empfehlung **erfindet nichts**. Führt der Katalog diese Kennung nicht
    #: (umbenannt, abgekündigt), zeigt die Oberfläche einfach keine Empfehlung
    #: an — nie einen Eintrag, den es beim Anbieter nicht gibt. Das ist dieselbe
    #: Regel wie überall sonst hier, und sie ist der Grund, warum die Empfehlung
    #: eine Modellkennung ist und kein eigener Listeneintrag.
    empfehlung: str | None = None


ANBIETER: dict[str, Anbieter] = {
    "openrouter": Anbieter(
        kind="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        catalog_url="https://openrouter.ai/api/v1/models",
        key_url="https://openrouter.ai/keys",
        key_prefix="sk-or-",
        # Im Betrieb von MSM erprobt: schnell genug fuer den Chat, kennt
        # Denkstufen, und der Werkzeugkatalog geht ihm nicht verloren. Wer
        # etwas anderes waehlt, bekommt keine Warnung — es ist ein Vorschlag,
        # keine Bedingung.
        empfehlung="openai/gpt-5.6-luna",
    ),
}

#: Der Anbieter, den die Oberfläche vorschlägt. Solange es genau einen gibt,
#: ist die Auswahl eine Formalität — aber sie steht schon da, wo sie später
#: gebraucht wird.
STANDARD_KIND = "openrouter"


def anbieter(kind: str) -> Anbieter:
    """Löst einen gespeicherten Schlüssel auf.

    Ein unbekannter Schlüssel ist kein Benutzerfehler, sondern ein Datenstand,
    den es nicht geben dürfte — etwa eine Zeile aus einer Zukunftsversion nach
    einem Downgrade. Deshalb ``KeyError`` und keine leise Rückgabe von ``None``:
    ein Provider ohne Adresse darf nicht bis zum HTTP-Aufruf durchkommen.
    """
    try:
        return ANBIETER[kind]
    except KeyError as exc:
        raise KeyError(f"Unbekannter KI-Anbieter: {kind!r}") from exc


def bekannt(kind: str) -> bool:
    return kind in ANBIETER


def alle() -> list[Anbieter]:
    return sorted(ANBIETER.values(), key=lambda item: item.label)
