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

Der zweite Eintrag war einmal OpenAIs Realtime-API und ist es nicht mehr. Was
sie gekostet hat, stand am Ende in zwei Zahlen: ein zweiter Werkzeuglauf mit
eigener Bestätigung, eigener Autonomie und eigenem Gedächtnis neben dem des
Chats — und ein Protokoll, das MSM nur für sie sprach. Der Sprachmodus macht
seitdem dasselbe wie der getippte Chat und hängt nur zwei Wandler davor: Gehör
davor, Stimme dahinter. `elevenlabs` ist die Stimme.

**OpenAI steht seit dem 17.08.2026 wieder hier, und der Unterschied ist der
ganze Punkt:** als gewöhnlicher `chat_completions`-Zugang, ohne eine einzige
Zeile Code, die nur ihm gehört. Was damals flog, war nicht der Anbieter, es war
sein Sonderweg. Ein Eintrag ohne Sonderweg kostet nichts, wenn ihn niemand
benutzt — und das ist die Bedingung, unter der die Liste wachsen darf.

**Das Gehör hat keinen eigenen Eintrag.** Es hängt an einem Chatzugang, weil es
entweder dessen ``/audio/transcriptions`` benutzt oder dessen
``/chat/completions`` (`ai_stt`). Welche Wege ein Anbieter dafür kennt, sagt
`gehoer_wege` — und dass das ein zusätzliches Feld ist und keine Fähigkeitsmenge
neben `protokoll`, ist am Feld selbst begründet.
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
    #:   Bedient von `openai_compatible_adapter`. An einem solchen Zugang hängt
    #:   auch das Gehör — auf welchem Weg, sagt `gehoer_wege`.
    #: * ``tts`` — Text hinein, Ton heraus. Eine WebSocket-Sitzung je Antwort
    #:   gegen ``/text-to-speech/{voice}/stream-input``, bedient von
    #:   `ai_tts_elevenlabs`. Kennt weder Nachrichten noch Werkzeuge und taucht
    #:   deshalb in keiner Modellauswahl des Chats auf.
    #:
    #: Die beiden sind **nicht** ineinander überführbar. Ein Zugang im falschen
    #: Router endete nicht in einer Fehlermeldung, sondern in einem 404 vom
    #: Anbieter — deshalb prüfen beide Wege dieses Feld, bevor sie einen Zugang
    #: annehmen.
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
    #: Liste ohne heraus, ElevenLabs nicht.
    #:
    #: Das Feld steht hier, weil `vorwaermen_anstossen()` beim Start der
    #: Anwendung ausdrücklich **ohne** Datenbank läuft — es kennt also keinen
    #: Schlüssel und darf einen solchen Katalog gar nicht erst versuchen. Ohne
    #: die Unterscheidung liefe bei jedem Start ein Abruf in ein 401, würde als
    #: Fehlversuch vermerkt, und die Ruhefrist verzögerte den ersten echten
    #: Abruf um eine Minute — für einen Fehler, der keiner war.
    katalog_braucht_schluessel: bool = False
    #: Wie der Schlüssel an den Anbieter geht. ``Authorization: Bearer …`` ist
    #: verbreitet genug, um Standard zu sein, aber es ist keine Regel:
    #: ElevenLabs erwartet ihn roh in ``xi-api-key`` und antwortet auf ein
    #: ``Bearer`` mit 401 — einem 401, das wie ein falscher Schlüssel aussieht
    #: und keiner ist. Genau solche Fehlersuchen kostet ein fest verdrahteter
    #: Kopf.
    schluessel_kopf: str = "Authorization"
    schluessel_praefix: str = "Bearer "
    #: Unter welchem Feld die Modellliste in der Antwort steht. ``None`` heißt:
    #: die Antwort **ist** die Liste. Auch das ist keine Marotte eines einzelnen
    #: Anbieters, sondern die Stelle, an der ein sonst fehlerfreier Katalogabruf
    #: als „hat kein data-Feld" endet.
    katalog_liste_feld: str | None = "data"
    #: Auf welchen Wegen dieser Anbieter zuhören kann, **nach Güte sortiert**.
    #: Leer heisst: er kann es nicht.
    #:
    #: Das Feld ist der Grund, warum `protokoll` hier nicht zu einer Menge von
    #: Fähigkeiten ausgebaut wurde. Der Anlass wäre da: OpenRouter steht als
    #: ``chat_completions`` und schreibt trotzdem ab — ein Anbieter kann also
    #: mehr als eine Sache. Ein solcher Umbau berührt aber drei Router, ein
    #: Schema, die Provider-Einstellungen und beide Sprachdateien. Solange die
    #: einzige Zusatzfähigkeit das Zuhören ist, ist ein Feld dafür die
    #: ehrlichere Antwort als eine Abstraktion, die genau einen Fall kennt.
    #:
    #: Welcher Weg gilt, entscheidet `ai_stt.weg_fuer` — ohne Zutun der erste
    #: hier, mit ``MSM_AI_STT_WEG`` der gewählte. Die Reihenfolge ist deshalb
    #: eine Aussage: vorne steht, was MSM empfehlen würde, wenn das Konto des
    #: Betreibers es hergibt.
    gehoer_wege: tuple[str, ...] = ()
    #: Wie der Transkriptionsendpunkt seine Nutzlast will — ``"json"`` mit dem
    #: Ton als Base64 (OpenRouter) oder ``"multipart"`` mit einer Datei
    #: (OpenAI). Ohne ``"endpunkt"`` in `gehoer_wege` bedeutungslos.
    #:
    #: Beides ist Standard, nur nicht derselbe, und die falsche Form endet in
    #: einem ``400``, das wie ein kaputter Ton aussieht und keiner ist.
    gehoer_form: str = "json"
    #: Ob dieser Anbieter das ``reasoning``-Objekt in ``/chat/completions``
    #: versteht. Es ist eine OpenRouter-Erweiterung des Protokolls, kein Teil
    #: davon: OpenAI weist unbekannte Top-Level-Felder mit einem 400 ab — jede
    #: Anfrage scheiterte dort, bevor das Modell sie je sah. Das ist eine
    #: Eigenschaft des **Dialekts**, nicht eines Modells, deshalb steht sie hier
    #: und nicht im Katalog; welche Stufen ein Modell kennt, sagt weiterhin nur
    #: der Katalog.
    reasoning_feld: bool = False


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
        # Beide Hoerwege, Endpunkt zuerst — er ist der billigere. Dass der
        # Chatweg ueberhaupt danebensteht, hat einen Grund aus dem Betrieb: der
        # Endpunkt wird aus **Guthaben** bezahlt und nicht ueber den
        # hinterlegten Fremdschluessel. Ein Konto ohne Guthaben chattet also
        # weiter und hoert nicht mehr. Siehe `ai_stt_chat`.
        gehoer_wege=("endpunkt", "chat"),
        gehoer_form="json",
        # OpenRouter hat das Feld erfunden; nur hier geht es mit.
        reasoning_feld=True,
    ),
    # OpenAI direkt, ohne Vermittler. Der Eintrag stand hier schon einmal und
    # ist am 2026-08-16 geflogen — damals allerdings als **Realtime**-Zugang mit
    # eigenem Protokoll, eigenem Werkzeuglauf und eigenem Gedaechtnis. Das ist
    # der ganze Unterschied zu jetzt: hier spricht OpenAI dasselbe
    # `chat_completions` wie jeder andere, es gibt keine Zeile Code, die nur
    # ihm gehoert. Ein Anbieter, der nichts eigenes braucht, sammelt auch
    # keinen toten Code an.
    #
    # **Was der Betreiber hier verliert, und es steht bewusst offen:** OpenAIs
    # `/v1/models` liefert je Modell nur `id`, `object`, `created`, `owned_by`
    # und `shutdown_date` — nachgesehen in deren offizieller `openapi.yaml`.
    # Keine Modalitaeten, kein Kontextfenster, keine Preise, keine Denkstufen.
    # Der Katalog ist bei MSM aber die einzige erlaubte Quelle fuer genau diese
    # Angaben (siehe `ai_model_catalog`), und eine Tabelle im Code waere die
    # gepflegte Liste, gegen die diese Datei gebaut wurde. Also: an einem
    # OpenAI-Zugang gibt es keine Denkstufenauswahl und kein bekanntes
    # Kontextfenster. Beides heisst "unbekannt", nie "klein" oder "kann er
    # nicht".
    "openai": Anbieter(
        kind="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        catalog_url="https://api.openai.com/v1/models",
        key_url="https://platform.openai.com/api-keys",
        key_prefix="sk-",
        # Anders als OpenRouter gibt OpenAI seine Modellliste nur gegen einen
        # Schluessel heraus.
        katalog_braucht_schluessel=True,
        # Nur der Endpunkt. Der Chatweg funktionierte technisch auch hier
        # (`input_audio` ist OpenAIs eigene Form), waere aber sinnlos: es gibt
        # bei OpenAI kein kostenloses hoerfaehiges Modell, und damit faellt der
        # einzige Grund weg, ein Chatmodell abschreiben zu lassen.
        gehoer_wege=("endpunkt",),
        # `multipart/form-data` mit `file` und `model` — OpenAIs Form. Nicht
        # OpenRouters JSON mit Base64.
        gehoer_form="multipart",
        # **Keine Empfehlung fuer den Chat**, und das ist keine Nachlaessigkeit.
        # Die Empfehlung wird gegen den Katalog geprueft und faellt weg, wenn
        # die Kennung dort nicht steht — sie waere hier also im besten Fall
        # wirkungslos. Vor allem aber weiss MSM ueber OpenAIs Katalog nichts
        # ausser Kennungen: welches Modell sich im Betrieb bewaehrt, laesst sich
        # von hier aus nicht sagen, und eine geratene Empfehlung ist eine
        # Meinung ohne Grundlage. Fuer das Gehoer nennt der Feldhinweis in der
        # Oberflaeche `gpt-transcribe` und `whisper-1`; die stehen so in OpenAIs
        # `openapi.yaml` und sind damit belegt und keine Meinung.
    ),
    # Die Stimme. Sie **antwortet nicht** — sie liest vor, was das Chatmodell
    # oben geschrieben hat. Das ist der ganze Unterschied zum Realtime-Zugang,
    # der hier bis zum 2026-08-16 stand: der sprach selbst, mit eigenem
    # Werkzeuglauf, eigener Bestaetigung und eigenem Gedaechtnis neben dem des
    # Chats. Zwei Modelle, die beide dasselbe Panel bedienen durften, waren
    # zweimal dieselbe Arbeit und zweimal dieselbe Angriffsflaeche.
    #
    # Der Betreiber waehlt hier zweierlei: `default_model` ist das Sprachmodell
    # (Flash rechnet in etwa 75 ms und ist damit das einzige, das sich wie ein
    # Gespraech anfuehlt), `default_voice` die Stimm-Kennung aus seinem Konto.
    # Beides steht am Zugang und nicht im Programm, weil beides eine Wahl ist
    # und keine Tatsache — welche Stimme zu einem Panel passt, weiss nur er.
    "elevenlabs": Anbieter(
        kind="elevenlabs",
        label="ElevenLabs (Stimme)",
        base_url="https://api.elevenlabs.io/v1",
        catalog_url="https://api.elevenlabs.io/v1/models",
        key_url="https://elevenlabs.io/app/settings/api-keys",
        # Ausdruecklich **keine** Praefixpruefung. Neue Schluessel beginnen mit
        # `sk_`, aeltere Konten tragen noch blanke Hex-Ketten ohne jedes
        # Praefix. Eine Pruefung wuerde hier also gueltige Schluessel abweisen —
        # und der Zweck des Feldes ist, dem Betreiber einen Umweg zu ersparen,
        # nicht ihm einen zu bauen. Der echte Testaufruf faengt den Rest.
        key_prefix=None,
        protokoll="tts",
        # ElevenLabs gibt seine Modellliste nur gegen einen Schluessel heraus …
        katalog_braucht_schluessel=True,
        # … und zwar in `xi-api-key`, nicht als Bearer-Token.
        schluessel_kopf="xi-api-key",
        schluessel_praefix="",
        # `GET /v1/models` antwortet mit einer nackten Liste. Kein `data`.
        katalog_liste_feld=None,
        # Am 2026-08-16 nachgesehen: rund 75 ms Rechenzeit, in Europa 100 bis
        # 150 ms bis zum ersten Ton. Die hoeherwertigen Modelle klingen besser
        # und kosten genau das, was ein Gespraech nicht hat. Es bleibt eine
        # Empfehlung — fuehrt der Katalog die Kennung nicht mehr, zeigt die
        # Oberflaeche keine an, nie eine erfundene.
        empfehlung="eleven_flash_v2_5",
    ),
}

#: Die Protokolle als Konstante. Ein vertipptes ``"chat_completion"`` (ohne s)
#: fiele sonst nirgends auf: `spricht()` gäbe schlicht ``False`` zurück, und die
#: Providerauswahl wäre leer statt kaputt.
CHAT = "chat_completions"
TTS = "tts"

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
    Chat-Router verlangt ``chat_completions``, die Stimme ``tts``. Ein
    unbekannter Schlüssel ist hier ausdrücklich **kein** Fehler, sondern ein
    „nein" — anders als bei `anbieter()`. Der Unterschied hat einen Grund: hier
    wird gefiltert, dort aufgelöst. Eine Filterfunktion, die bei einer
    unerwarteten Zeile eine Ausnahme wirft, nimmt die ganze Liste mit statt nur
    den einen Eintrag.
    """
    spec = ANBIETER.get(kind)
    return spec is not None and spec.protokoll == protokoll


#: Hier stand ``mit_protokoll(protokoll)`` — „alle Anbieter, die dieses
#: Protokoll sprechen". Geschrieben für einen Aufrufer, der nie kam: gefiltert
#: wird überall über `spricht()` an einer bereits vorliegenden Zeile, nicht über
#: eine Vorauswahl der Liste. Entfernt beim Einbau des dritten Anbieters, weil
#: eine ungenutzte Funktion beim nächsten Umbau mitwandert und dabei aussieht,
#: als hinge etwas an ihr.
