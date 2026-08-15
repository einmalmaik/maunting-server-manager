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

Der zweite Eintrag ist jetzt da, und er hat genau eine Sache gezeigt: **nicht
jeder Anbieter spricht dasselbe Protokoll.** Deshalb gibt es `protokoll` — und
nach wie vor keine Klassenhierarchie. Ein Feld genügt, um die eine Frage zu
beantworten, die zwei verschiedene Endpunkte auseinanderhält.
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
    #: Welche API hinter dieser Adresse steht. Das ist **keine** Feinheit,
    #: sondern die Grenze zwischen zwei Adaptern:
    #:
    #: * ``chat_completions`` — ``POST /chat/completions``, SSE je Anfrage,
    #:   ``reasoning:{enabled,effort}``, ``cache_control``, ``usage.cost``.
    #:   Bedient von `openai_compatible_adapter`.
    #: * ``realtime`` — eine stehende WebSocket-Sitzung gegen ``/realtime`` mit
    #:   Ereignissen statt Nachrichten. Bedient von `ai_voice_session`.
    #:
    #: Die beiden sind **nicht** ineinander überführbar. Ein Realtime-Zugang im
    #: Chat-Router endete nicht in einer Fehlermeldung, sondern in einem
    #: 404 vom Anbieter — deshalb prüfen beide Wege dieses Feld, bevor sie einen
    #: Zugang annehmen.
    protokoll: str = "chat_completions"
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
    #: Ob der Katalogabruf den Betreiberschlüssel braucht. OpenRouter gibt seine
    #: Liste ohne heraus, OpenAI nicht.
    #:
    #: Das Feld steht hier, weil `vorwaermen_anstossen()` beim Start der
    #: Anwendung ausdrücklich **ohne** Datenbank läuft — es kennt also keinen
    #: Schlüssel und darf einen solchen Katalog gar nicht erst versuchen. Ohne
    #: die Unterscheidung liefe bei jedem Start ein Abruf in ein 401, würde als
    #: Fehlversuch vermerkt, und die Ruhefrist verzögerte den ersten echten
    #: Abruf um eine Minute — für einen Fehler, der keiner war.
    katalog_braucht_schluessel: bool = False


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
    # Der Sprachweg. **Nicht** derselbe Anbieter wie oben, obwohl OpenRouter
    # OpenAI-Modelle vermittelt: OpenRouter hat keine Realtime-API. Am
    # 2026-08-15 nachgesehen — `POST /api/v1/realtime` antwortet mit 404
    # (Kontrolle: `/chat/completions` antwortet mit 401), und die vollstaendige
    # OpenAPI-Spezifikation kennt weder `websocket` noch `webrtc`, `realtime`
    # oder `session.update`. Es gibt dort ausschliesslich HTTP mit SSE
    # innerhalb *einer* Anfrage. Eine stehende Sitzung, in die man
    # hineinreden kann, ist damit nicht vermittelbar, und `gpt-realtime` liefert
    # ueber OpenRouter folgerichtig ebenfalls ein 404.
    #
    # Sprache gaebe es dort trotzdem — rundenbasiert ueber `openai/gpt-audio`.
    # Dagegen sprach nicht nur das fehlende Reinreden: dieses Modell steht
    # selbst auf der Abkuendigungsliste vom 2026-07-20, Abschaltung am
    # 2027-01-20, und seinen Nachfolger `gpt-audio-1.5` fuehrt OpenRouter nicht.
    # Es waere eine Frist gewesen, keine Architektur.
    "openai_realtime": Anbieter(
        kind="openai_realtime",
        label="OpenAI (Sprache)",
        base_url="https://api.openai.com/v1",
        catalog_url="https://api.openai.com/v1/models",
        key_url="https://platform.openai.com/api-keys",
        key_prefix="sk-",
        protokoll="realtime",
        # OpenAI gibt seine Modellliste nur gegen einen Schluessel heraus.
        katalog_braucht_schluessel=True,
        # Nur `-2.1` und `-2.1-mini` haben kein Ablaufdatum. `gpt-realtime`,
        # `gpt-realtime-mini` und alle `gpt-4o-realtime` sterben am 2027-01-20.
        # Genau deshalb steht hier eine Empfehlung und keine gepflegte Liste:
        # die Auswahl kommt aus dem Katalog, und eine abgeschaltete Kennung
        # verschwindet dort von selbst.
        empfehlung="gpt-realtime-2.1",
    ),
}

#: Die beiden Protokolle als Konstante. Ein vertipptes ``"chat_completion"``
#: (ohne s) fiele sonst nirgends auf: `spricht()` gäbe schlicht ``False``
#: zurück, und die Providerauswahl wäre leer statt kaputt.
CHAT = "chat_completions"
REALTIME = "realtime"

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


def spricht(kind: str, protokoll: str) -> bool:
    """Spricht dieser Anbieter das verlangte Protokoll?

    Die Frage stellen beide Wege, bevor sie einen Zugang annehmen: der
    Chat-Router verlangt ``chat_completions``, der Sprachweg ``realtime``. Ein
    unbekannter Schlüssel ist hier ausdrücklich **kein** Fehler, sondern ein
    „nein" — anders als bei `anbieter()`. Der Unterschied hat einen Grund: hier
    wird gefiltert, dort aufgelöst. Eine Filterfunktion, die bei einer
    unerwarteten Zeile eine Ausnahme wirft, nimmt die ganze Liste mit statt nur
    den einen Eintrag.
    """
    spec = ANBIETER.get(kind)
    return spec is not None and spec.protokoll == protokoll


def mit_protokoll(protokoll: str) -> list[Anbieter]:
    """Alle Anbieter, die dieses Protokoll sprechen."""
    return [spec for spec in alle() if spec.protokoll == protokoll]
