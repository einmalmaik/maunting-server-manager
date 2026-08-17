"""Die gemeinsame Sprache aller Anbieterdateien — Datenklassen, keine Logik.

Hier steht nur, **worüber** die Anbieterdateien reden: was ein Anbieter ist
(`Anbieter`) und was aus seinem Katalog herauskommt (`Modell`). Kein Anbieter
wird hier genannt, keine Adresse steht hier, und diese Datei importiert nichts
aus dem Paket.

Das ist der Grund, warum das Paket keinen Importzyklus hat: die Anbieterdateien
importieren `basis`, der Controller importiert die Anbieterdateien, und
`ai_model_catalog` importiert den Controller. Jeder Pfeil zeigt in dieselbe
Richtung.

Bewusst **keine** Basisklasse mit einer Ableitung je Anbieter. Ein Anbieter
unterscheidet sich von den anderen in Werten, nicht in Verhalten — Adresse,
Kopfzeile, Wortschatz. Werte gehören in Felder. Das einzige echte Verhalten je
Anbieter ist das Lesen seines Katalogs, und dafür genügt eine Funktion im
jeweiligen Modul; eine Klassenhierarchie darum herum wäre Zeremonie ohne
Gegenwert.
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
    #: Welches Modell MSM empfiehlt. Das ist die **einzige** Aussage in einer
    #: Anbieterdatei, die eine Meinung ist und keine Tatsache — deshalb steht
    #: sie dort und nicht im Katalog. Der Katalog sagt, was ein Modell kann; was
    #: sich im Betrieb bewährt hat, weiß er nicht.
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
    #: Wessen Katalog die **Fähigkeiten** liefert, wenn der eigene sie nicht
    #: kennt — der ``kind`` eines anderen Anbieters, oder ``None``.
    #:
    #: Der Anlass ist OpenAI. Sein ``/v1/models`` gibt je Modell vier Felder
    #: heraus: ``id``, ``created``, ``owned_by``, ``shutdown_date``. Kein
    #: Kontextfenster, keine Denkstufen. Wer nur diesen Katalog liest, weiß von
    #: ``gpt-5.5`` nicht mehr als den Namen — und muss dann entweder raten oder
    #: dem Betreiber die Denkstufe und das Fenster vorenthalten, die er bei
    #: OpenRouter selbstverständlich bekommt.
    #:
    #: Die naheliegende Antwort wäre eine Tabelle im Programm. Sie wäre der
    #: Rückfall in genau das, wogegen es diesen Katalog gibt: eine Liste, die am
    #: Tag ihrer Niederschrift stimmt und danach jeden neuen Modellnamen als
    #: „kann nichts" führt. Die zweite Antwort ist ein **anderer Katalog**, der
    #: dieselben Modelle beschreibt — OpenRouter führt sie unter ``openai/…``
    #: mit demselben Wortschatz für die Denkstufen, den OpenAI selbst verwendet.
    #: Damit bleibt die Tatsache eine Tatsache und wandert nicht in den Code.
    #:
    #: Es entsteht dadurch **keine neue Abhängigkeit nach draußen**:
    #: `ai_model_catalog.vorwaermen_anstossen()` holt OpenRouters Katalog ohnehin
    #: bei jedem Start jeder Installation, weil er ohne Schlüssel offen liegt.
    #: Und es wird nichts verlangt: fällt der fremde Katalog aus, bleibt es beim
    #: eigenen Wissen — also bei „unbekannt". Ein Anbieter, der seine
    #: Fähigkeiten selbst herausgibt, lässt das Feld leer.
    faehigkeiten_aus: str | None = None
    #: Wie die Kennungen des fremden Katalogs von den eigenen abweichen.
    #: OpenRouter stellt jedem OpenAI-Modell ein ``openai/`` voran; ``gpt-5.5``
    #: heißt dort ``openai/gpt-5.5``. Ohne `faehigkeiten_aus` bedeutungslos.
    faehigkeiten_praefix: str = ""
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
    #: Welche **Zusatzfelder** dieser Anbieter in einer Chatanfrage verträgt.
    #:
    #: „OpenAI-kompatibel" heißt gleicher Endpunkt und gleiches Grundgerüst —
    #: es heißt nicht gleicher Wortschatz. `reasoning: {enabled, effort}` und
    #: `cache_control` sind **OpenRouter-Erweiterungen**. OpenAI kennt sie nicht,
    #: und OpenAI ignoriert Unbekanntes nicht, sondern antwortet mit
    #: ``400 Unrecognized request argument supplied``.
    #:
    #: Das hat den OpenAI-Zugang vom ersten Tag an vollständig unbrauchbar
    #: gemacht: **jede** Chatanfrage trug `reasoning` und wurde abgelehnt, egal
    #: welches Modell, egal ob Nachdenken an oder aus war. Sichtbar war davon
    #: nichts. Der Adapter las den Grund durchaus — ein ``400`` ist kein
    #: ``200``, also ging er durch `openai_compatible_adapter._error_detail` und
    #: hing als ``detail`` an der `AiProviderRequestError`. Gestorben ist er
    #: eine Schicht später: der Ausnahmezweig in `ai_stream_service`, der den
    #: Lauf abschließt, nahm sich nur ``exc.code`` und ließ ``exc.detail``
    #: fallen. Übrig blieb `AI_PROVIDER_REQUEST_REJECTED` ohne ein Wort dazu.
    #:
    #: Warum das Wissen in der **Anbieterdatei** steht und nicht als
    #: ``if kind == "openai"`` im Adapter: der Adapter bedient alle Anbieter und
    #: darf keinen einzelnen kennen. Sonst wächst mit jedem Eintrag eine
    #: Verzweigung in einer Datei, die niemandem gehört — und genau das ist die
    #: Bauart, die dieses Paket vermeiden soll. Ein Anbieter bringt seinen
    #: Wortschatz selbst mit.
    #:
    #: Bekannte Marken:
    #:
    #: * ``"reasoning"`` — ``{"enabled": bool, "effort": str}`` auf oberster
    #:   Ebene. Ohne diese Marke sendet MSM **gar nichts** zum Nachdenken, statt
    #:   es anders zu formulieren.
    #: * ``"reasoning_effort"`` — dasselbe Anliegen in OpenAIs Wortschatz: eine
    #:   **Zeichenkette** auf oberster Ebene, ohne Schalter daneben. Ein „aus"
    #:   gibt es dort nicht als ``false``, sondern als Stufe ``"none"`` — und
    #:   nur bei den Modellen, die sie führen. Deshalb entscheidet auch hier der
    #:   Katalog und nicht der Adapter, was gesendet wird; siehe
    #:   `ai_reasoning.klemmen`.
    #: * ``"cache_control"`` — die Marke fürs Prompt-Caching. Sie ginge ohnehin
    #:   nur mit, wenn der Katalog `input_cache_write` führt; hier steht sie,
    #:   damit die Bedingung nicht an zwei Stellen halb gilt.
    anfrage_erweiterungen: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Modell:
    """Ein Modell des Anbieters, so wie MSM es braucht.

    Das ist die **Ausgabe** jedes ``katalog_lesen`` in diesem Paket: egal wie
    verschieden die Anbieter ihre Kataloge schreiben, hier kommen sie alle in
    derselben Form heraus. Deshalb steht die Klasse in `basis` und nicht in
    `ai_model_catalog` — sie ist die gemeinsame Sprache der Anbieterdateien, und
    der Katalog ist nur ihr erster Leser.

    ``stufen`` ist leer, wenn das Modell keine kennt. Das ist **nicht**
    dasselbe wie „kann nicht nachdenken“ — die Mehrheit der denkenden Modelle
    landet genau hier und wird nur an- oder ausgeschaltet.

    ``zwingend`` heißt, dass Nachdenken nicht abschaltbar ist. Für diese
    Modelle darf die Oberfläche kein „aus“ anbieten, sonst verspricht sie
    etwas, das der Anbieter ablehnt.

    ``kontext_tokens`` ist das Kontextfenster — wieviel Text das Modell auf
    einmal lesen kann. Es steht aus demselben Grund hier wie die Denkstufen:
    die Werte gehen von 4.000 bis 1.000.000 und ändern sich mit jedem neuen
    Modell. Eine Zahl im Programm wäre bei fast jedem Modell falsch, und die
    Folge sähe man nicht — der Chat vergäße nur früher als nötig.

    ``max_ausgabe_tokens`` ist, was das Modell antworten darf. Der Platz dafür
    geht vom Fenster ab; wer ihn nicht abzieht, schickt eine Anfrage, die
    hineinpasst, und bekommt trotzdem eine Absage.

    Beides darf ``None`` sein. Der Auto Router führt gar kein Fenster, manche
    Modelle keine Ausgabegrenze.

    ``cache_marke_noetig`` heißt: dieses Modell speichert den Prompt nur dann
    zwischen, wenn die Anfrage es ausdrücklich verlangt. ``False`` deckt **zwei**
    Fälle ab, die für den Sendepfad dasselbe bedeuten — das Modell speichert von
    selbst zwischen, oder es kann es gar nicht. Beide Male ist nichts zu tun,
    und beide Male wäre eine Marke falsch: dort wirkungslos, hier eine Bitte um
    etwas, das nicht angeboten wird.
    """

    model_id: str
    name: str
    denkt: bool
    stufen: tuple[str, ...] = ()
    standard_stufe: str | None = None
    zwingend: bool = False
    kontext_tokens: int | None = None
    max_ausgabe_tokens: int | None = None
    cache_marke_noetig: bool = False


def positive_zahl(wert: object) -> int | None:
    """Eine Tokenzahl aus fremden Daten, oder ``None``.

    ``bool`` wird ausdruecklich abgewiesen: in Python ist ``True`` eine 1, und
    ein Fenster von einem Token waere schlimmer als gar keine Angabe — es
    schluege nicht fehl, sondern kuerzte den Kontext auf nichts.

    Steht hier und nicht in einer Anbieterdatei, weil die Falle keinem Anbieter
    gehoert: jeder Katalog ist fremde Eingabe, und jeder naechste Leser einer
    Tokenzahl braucht dieselbe Pruefung.
    """
    if isinstance(wert, bool) or not isinstance(wert, int):
        return None
    return wert if wert > 0 else None
