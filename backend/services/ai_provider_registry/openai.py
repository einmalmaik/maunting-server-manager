"""OpenAI direkt, ohne Vermittler — ein gewöhnlicher Chatzugang.

Der Eintrag stand schon einmal im Programm und ist am 2026-08-16 geflogen —
damals allerdings als **Realtime**-Zugang mit eigenem Protokoll, eigenem
Werkzeuglauf und eigenem Gedächtnis neben dem des Chats. Das ist der ganze
Unterschied zu jetzt: hier spricht OpenAI dasselbe `chat_completions` wie jeder
andere, und es gibt keine Zeile Code außerhalb dieser Datei, die nur ihm gehört.
Was damals flog, war nicht der Anbieter, es war sein Sonderweg. Ein Anbieter
ohne Sonderweg sammelt auch keinen toten Code an — das ist die Bedingung, unter
der die Liste wachsen darf.

**OpenAIs Katalog schweigt über das Wesentliche.** ``/v1/models`` liefert je
Modell nur ``id``, ``object``, ``created``, ``owned_by`` und ``shutdown_date`` —
nachgesehen am 2026-08-17 in deren offizieller ``openapi.json``. Keine
Modalitäten, kein Kontextfenster, keine Preise, keine Denkstufen. Der Katalog
ist bei MSM aber die einzige erlaubte Quelle für genau diese Angaben (siehe
`ai_model_catalog`), und eine Tabelle im Code wäre die gepflegte Liste, gegen
die dieses Paket gebaut wurde.

Bis zum 17.08.2026 endete der Absatz hier, mit dem Ergebnis „dann eben
unbekannt". Das war ehrlich und trotzdem der schlechtere Zugang: wer OpenRouter
nicht will, bekam bei OpenAI keine Denkstufe und kein Fenster — nicht weil es
die Angaben nicht gibt, sondern weil **dieser** Katalog sie nicht führt.

Jetzt steht die dritte Möglichkeit da: ``faehigkeiten_aus="openrouter"``. Ein
anderer Katalog beschreibt dieselben Modelle, unter ``openai/…`` und mit
demselben Wortschatz für die Denkstufen, den OpenAI selbst verwendet
(``minimal``/``low``/``medium``/``high``/``xhigh``/``max``, dazu ``none`` fürs
Abschalten — beide Listen nachgesehen, keine Übersetzung nötig). Damit bleibt
die Tatsache eine Tatsache aus einem Katalog und wandert nicht in den Code.
Nachschlagen kostet nichts extra: OpenRouters Liste liegt ohne Schlüssel offen
und wird ohnehin bei jedem Start geholt.

Was der fremde Katalog nicht führt, bleibt unbekannt — er ist eine Ergänzung
und keine Bedingung. Fällt er aus, verhält sich der Zugang wieder wie oben
beschrieben, und „unbekannt" heißt weiterhin nie „klein" oder „kann er nicht".
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell


ANBIETER = Anbieter(
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
    # (`input_audio` ist OpenAIs eigene Form), waere aber sinnlos: es gibt bei
    # OpenAI kein kostenloses hoerfaehiges Modell, und damit faellt der einzige
    # Grund weg, ein Chatmodell abschreiben zu lassen.
    gehoer_wege=("endpunkt",),
    # `multipart/form-data` mit `file` und `model` — OpenAIs Form. Nicht
    # OpenRouters JSON mit Base64.
    gehoer_form="multipart",
    # Woher die Denkstufen und das Kontextfenster kommen, wenn OpenAIs eigener
    # Katalog sie verschweigt — was er immer tut. Siehe Moduldoku oben und
    # `basis.Anbieter.faehigkeiten_aus`.
    faehigkeiten_aus="openrouter",
    faehigkeiten_praefix="openai/",
    # OpenAIs eigene Form fuers Nachdenken: `reasoning_effort` als **blosse
    # Zeichenkette** auf oberster Ebene, kein `{enabled, effort}`. Nachgesehen
    # in deren `openapi.json`: `CreateChatCompletionRequest.reasoning_effort`,
    # Werte `none|minimal|low|medium|high|xhigh|max`, mit dem ausdruecklichen
    # Zusatz „Not all reasoning models support every value".
    #
    # OpenRouters `reasoning` und `cache_control` stehen hier bewusst **nicht**:
    # das sind deren Erweiterungen, OpenAI antwortet darauf mit `400
    # Unrecognized request argument supplied` und lehnt damit **jede** Anfrage
    # ab. Genau das hat den Zugang vom ersten Tag an unbrauchbar gemacht.
    #
    # Bis zum 17.08.2026 stand hier deshalb gar nichts, mit einer Begruendung,
    # die stimmte: `reasoning_effort` an einem nicht denkfaehigen Modell ist
    # wieder ein 400, und welche denkfaehig sind, verriet der Katalog nicht.
    # Genau diese Luecke schliesst `faehigkeiten_aus` — jetzt weiss MSM es, und
    # `openai_compatible_adapter` sendet die Marke nur dann, wenn eine Stufe
    # feststeht. Kein Wissen, keine Stufe, keine Marke.
    anfrage_erweiterungen=frozenset({
        "reasoning_effort", "websocket", "background", "file_inputs", "compaction"
    }),
    # **Der Chatweg ist `/responses` und nicht `/chat/completions`.**
    #
    # Gemessen am 2026-08-18 gegen OpenAI direkt, jeweils mit Werkzeugkatalog
    # auf `/chat/completions`:
    #
    #   gpt-5.6-luna   none=OK   low=400  medium=400
    #   gpt-5.2        none=OK   low=OK   medium=OK
    #   gpt-5.1        none=OK   low=OK   medium=OK
    #   gpt-5-mini     none=400  low=OK   medium=OK
    #
    # Die Ablehnung nennt den Ausweg selbst: „use /v1/responses or set
    # reasoning_effort to 'none'". Der zweite Teil waere die billigere
    # Aenderung gewesen und ist verworfen — MSM schickt im Chat immer
    # Werkzeuge mit, und ein Zugang, an dem Nachdenken und Werkzeuge einander
    # ausschliessen, taugt nicht fuer den Hintergrund-Worker: der soll gerade
    # denken duerfen, waehrend er arbeitet.
    #
    # Auf `/responses` kommt beides in derselben Runde. Nachgemessen mit
    # `effort: high`: 424 Zeichen Denkzusammenfassung **und** ein
    # `function_call read_server_status`, dazu `reasoning_tokens: 32` in der
    # Abrechnung. Eine zweite Runde mit `function_call_output` fuehrt den Lauf
    # sauber fort (145 Eingabe-, 147 Ausgabetokens).
    protokoll_chat="responses",
    realtime_tauglich=True,
    # **Keine Empfehlung fuer den Chat**, und das ist keine Nachlaessigkeit.
    # Die Empfehlung wird gegen den Katalog geprueft und faellt weg, wenn die
    # Kennung dort nicht steht — sie waere hier also im besten Fall wirkungslos.
    # Vor allem aber weiss MSM ueber OpenAIs Katalog nichts ausser Kennungen:
    # welches Modell sich im Betrieb bewaehrt, laesst sich von hier aus nicht
    # sagen, und eine geratene Empfehlung ist eine Meinung ohne Grundlage. Fuer
    # das Gehoer nennt der Feldhinweis in der Oberflaeche `gpt-transcribe` und
    # `whisper-1`; die stehen so in OpenAIs `openapi.json` und sind damit belegt
    # und keine Meinung.
)


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Liest einen Katalogeintrag von OpenAI — und weiss dabei fast nichts.

    Der Eintrag hat laut OpenAIs offizieller ``openapi.json`` genau fünf Felder:
    ``id``, ``object``, ``created``, ``owned_by`` und ``shutdown_date``. Keine
    Modalitäten, kein Kontextfenster, keine Preise, keine Denkstufen. Das ist
    kein Fehler in dieser Funktion, sondern der Umfang der Auskunft.

    Was hier herauskommt, ist deshalb **das Gerüst und nicht das fertige
    Modell**: eine Kennung, sonst nichts. Die Fähigkeiten trägt
    `ai_model_catalog` anschliessend aus OpenRouters Katalog nach
    (``faehigkeiten_aus`` oben). ``denkt=False`` und ``kontext_tokens=None``
    sind hier also Ausgangswerte und keine Behauptungen — sie bedeuten
    „von hier aus unbekannt", nie „kann er nicht" oder „klein"
    (`ai_context_window.ermitteln`). Bleibt es dabei, weil der fremde Katalog
    das Modell nicht führt oder gerade nicht erreichbar ist, sendet MSM zum
    Nachdenken **gar nichts** und das Modell arbeitet in OpenAIs Voreinstellung.

    Nachgetragen wird nur, was OpenAI selbst nicht sagt. Nichts hiervon
    überschreibt eine Angabe aus dieser Funktion — der eigene Katalog eines
    Anbieters hat immer recht, wenn er redet.

    **Es wird nicht gefiltert**, obwohl die Liste auch ``whisper-1``,
    ``tts-1``, ``dall-e-3`` und Einbettungsmodelle führt — Einträge also, die
    als Chatmodell beim ersten Satz scheitern. Bei ElevenLabs entscheidet
    darüber ein Feld des Anbieters (``can_do_text_to_speech``); hier gibt es
    keines. Bliebe eine Liste bekannter Präfixe im Code — und gegen genau die
    ist dieses Paket gebaut worden. MSM reicht die Liste des Anbieters
    unverändert durch und behauptet nichts über sie. Was hier stünde, wäre eine
    Vermutung im Gewand einer Tatsache, und die erste umbenannte Modellreihe
    würde sie still falsch machen.
    """
    model_id = rohdaten.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    return Modell(model_id=model_id, name=model_id, denkt=False)
