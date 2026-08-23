"""OpenRouter — ein Vermittler vor hunderten Modellen fremder Anbieter.

Alles, was MSM über OpenRouter weiß, steht in dieser Datei: seine Adressen, sein
Wortschatz und wie sein Modellkatalog zu lesen ist. Wer OpenRouter entfernen
will, löscht diese Datei und ihre Zeile in `__init__._MODULE` — es bleibt nichts
zurück.

Der Katalog ist hier der Grund, warum MSM überhaupt keine Fähigkeitsliste
pflegen muss. OpenRouter führt je Modell die Denkstufen, das Kontextfenster und
die Cache-Preise mit und gibt das alles **ohne Schlüssel** heraus. Gemessen am
2026-08-11 über alle 402 Einträge:

* 272 Modelle können nachdenken.
* Davon nennen **127** eine Stufenliste — in **20 verschiedenen**
  Zusammenstellungen, von ``['high']`` bis
  ``['max','xhigh','high','medium','low','none']``.
* **145** nennen keine, können also nur an oder aus.
* **82** können Nachdenken gar nicht abschalten (``mandatory``).

Die zweite Zahl ist der ganze Grund, warum die Auswahl aus dem Katalog kommt und
nicht aus einer Konstante: eine feste Stufenliste wäre bei der Mehrheit der
Modelle falsch — sie zeigte Stufen an, die es nicht gibt, und verschwiege
welche, die es gibt.
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell, positive_zahl


ANBIETER = Anbieter(
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
    # Endpunkt wird aus **Guthaben** bezahlt und nicht ueber den hinterlegten
    # Fremdschluessel. Ein Konto ohne Guthaben chattet also weiter und hoert
    # nicht mehr. Siehe `ai_stt_chat`.
    gehoer_wege=("endpunkt", "chat"),
    gehoer_form="json",
    # Beides ist OpenRouters eigener Wortschatz — hier ist es zu Hause.
    anfrage_erweiterungen=frozenset({"reasoning", "cache_control"}),
)


def _fenster(rohdaten: dict) -> tuple[int | None, int | None]:
    """Kontextfenster und Ausgabegrenze eines Katalogeintrags.

    OpenRouter nennt das Fenster **zweimal**: einmal oben als groesstes Fenster
    ueber alle Anbieter dieses Modells, und einmal in ``top_provider`` fuer den
    Anbieter, zu dem im Standardfall geroutet wird. Vorrang hat ``top_provider``
    — das ist das Fenster, das man tatsaechlich bekommt. Der obere Wert kann
    hoeher liegen, und danach zu rechnen hiesse, eine Anfrage zu bauen, die beim
    tatsaechlichen Anbieter nicht mehr hineinpasst.

    ``None`` ist ein regulaeres Ergebnis und kein Fehler: der Auto Router fuehrt
    ``top_provider.context_length: null`` ohne oberen Wert, weil er erst zur
    Laufzeit entscheidet, wohin er geht. Ein solcher Eintrag bleibt gueltig — er
    faellt spaeter nur auf das Rueckfallfenster zurueck.
    """
    top = rohdaten.get("top_provider")
    top = top if isinstance(top, dict) else {}
    kontext = positive_zahl(top.get("context_length"))
    if kontext is None:
        kontext = positive_zahl(rohdaten.get("context_length"))
    return kontext, positive_zahl(top.get("max_completion_tokens"))


def _cache_marke_noetig(rohdaten: dict) -> bool:
    """Verlangt dieses Modell eine ausdrueckliche Cache-Marke?

    Der Katalog sagt es nicht mit einem Schalter, sondern mit einem **Preis**:
    wer einen Schreibpreis fuehrt, rechnet das Anlegen des Zwischenspeichers
    gesondert ab — und rechnet es nur ab, wenn man es verlangt. Wer nur einen
    Lesepreis fuehrt, speichert von selbst zwischen; das Anlegen ist dort
    kostenlos und deshalb nicht aufgefuehrt.

    Gemessen am 2026-08-12 ueber alle 406 Eintraege: 240 fuehren einen Lesepreis
    und koennen ueberhaupt zwischenspeichern, davon nennen **71** zusaetzlich
    einen Schreibpreis. Diese 71 sind genau die Familien, die OpenRouter in
    seiner Doku als „explizit" auffuehrt — Anthropic (28), Google (17), Alibaba
    Qwen (13), OpenAI ab GPT-5.6 (6). Deckungsgleich, ohne Ausreisser in beide
    Richtungen. Die uebrigen 174 speichern von selbst zwischen; dort waere eine
    Marke bestenfalls wirkungslos.

    Geprueft wird nur auf **Vorhandensein**, nicht auf den Wert. Der Katalog
    fuehrt beide Felder durchweg als Zeichenkette und nie als ``"0"``; eine
    Umrechnung in eine Zahl waere eine Genauigkeit, die hier niemand braucht,
    und ein ``float()`` ueber Fremddaten ein Fehlerfall mehr.
    """
    preise = rohdaten.get("pricing")
    if not isinstance(preise, dict):
        return False
    schreibpreis = preise.get("input_cache_write")
    return isinstance(schreibpreis, str) and bool(schreibpreis.strip())


def _sieht(rohdaten: dict) -> bool | None:
    """Nimmt dieses Modell Bilder entgegen?

    OpenRouter fuehrt das unter ``architecture.input_modalities`` — eine Liste
    wie ``["text", "image", "file"]``. Fehlt das Feld oder ist es keine Liste,
    ist die Antwort ``None`` (unbekannt) und nicht ``False``: ein stilles
    „blind“ waere eine Behauptung ueber ein Modell, ueber das der Katalog hier
    nichts sagt, und sie kostete das Bildschirmfoto.
    """
    architektur = rohdaten.get("architecture")
    if not isinstance(architektur, dict):
        return None
    modalitaeten = architektur.get("input_modalities")
    if not isinstance(modalitaeten, list):
        return None
    return any(eintrag == "image" for eintrag in modalitaeten)


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Liest einen Katalogeintrag von OpenRouter.

    Gibt ``None`` zurück, wenn der Eintrag keine brauchbare Kennung hat. Ein
    einzelner kaputter Eintrag darf den ganzen Katalog nicht verwerfen — bei 400
    Einträgen von einem fremden Dienst ist mit genau so etwas zu rechnen.
    """
    model_id = rohdaten.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None

    kontext, ausgabe = _fenster(rohdaten)
    cache_marke = _cache_marke_noetig(rohdaten)
    blick = _sieht(rohdaten)
    reasoning = rohdaten.get("reasoning")
    if not isinstance(reasoning, dict):
        # Kein Denk-Objekt heißt: dieses Modell denkt nicht. Der Katalog führt
        # das Feld bei allen 272 denkenden Modellen; sein Fehlen ist eine
        # Aussage und keine Lücke.
        return Modell(
            model_id=model_id,
            name=str(rohdaten.get("name") or model_id),
            denkt=False,
            kontext_tokens=kontext,
            max_ausgabe_tokens=ausgabe,
            cache_marke_noetig=cache_marke,
            sieht=blick,
        )

    rohe_stufen = reasoning.get("supported_efforts")
    stufen = (
        tuple(item for item in rohe_stufen if isinstance(item, str) and item)
        if isinstance(rohe_stufen, list)
        else ()
    )
    standard = reasoning.get("default_effort")
    return Modell(
        model_id=model_id,
        name=str(rohdaten.get("name") or model_id),
        denkt=True,
        stufen=stufen,
        standard_stufe=standard if isinstance(standard, str) and standard else None,
        # ``default_enabled`` liefert der Anbieter mit, MSM uebernimmt es
        # bewusst **nicht**: der Sendepfad nennt ``enabled`` immer ausdruecklich
        # (siehe openai_compatible_adapter), fragt also nie nach der
        # Voreinstellung des Modells. Ein gefuelltes, aber nie gelesenes Feld
        # verspricht eine Faehigkeit, die es nicht gibt — und der naechste Leser
        # haelt es fuer eine benutzte Quelle.
        zwingend=bool(reasoning.get("mandatory")),
        kontext_tokens=kontext,
        max_ausgabe_tokens=ausgabe,
        cache_marke_noetig=cache_marke,
        sieht=blick,
    )
