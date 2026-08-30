"""Azure — der Zugang, dessen Adresse dem Betreiber gehört.

Der erste Anbieter in dieser Registry ohne feste Adresse. Bei OpenRouter,
OpenAI und ElevenLabs gibt es **einen** Host für alle Kunden; bei Azure ist
jede Ressource ihr eigener
(``https://mein-ai-hub.services.ai.azure.com/openai/v1``). Ohne den
Ressourcennamen ist der Anbieter nicht erreichbar — deshalb ``ressource_noetig``
und die Vorlage in `base_url`. Was das für die alte Zusage „keine Eingabe wird
zu einer Adresse" bedeutet, steht ausführlich am Feld in `basis`.

**Ein Eintrag für alles, was Azure über OpenAIs Wortschatz anbietet.** Der
vorgelegte Plan trennte Azure OpenAI (GPT) von „Azure AI Foundry" (Llama,
Mistral, DeepSeek) und gab letzterem ``…services.ai.azure.com/models``. Dieser
Weg ist am 2026-08-20 nachgesehen worden und trägt auf Microsoft Learn wörtlich
„**Deprecated**: This API is deprecated. Use the OpenAI API instead", die
Konzeptseite dazu „will be retired on **August 26, 2026**" — sechs Tage später.
Der Migrationsleitfaden nennt den Ersatz in einer Tabellenzeile: dieselbe
Ressource, Pfad ``/openai/v1/`` statt ``/models``. Ein zweiter Eintrag hätte
also einen Anbieter beschrieben, der eine Woche nach seiner Geburt 4xx liefert,
und nebenbei ein Feld für einen Pflicht-Query-Parameter (``api-version``)
verlangt, das es hier nicht gibt und das der neue Weg nicht braucht
(„implicit versioning").

**Warum ``services.ai.azure.com`` und nicht ``openai.azure.com``.** Beide
Formen sind belegt gleichwertig — Microsoft schreibt: „``base_url`` accepts
both ``https://…openai.azure.com/openai/v1/`` and
``https://…services.ai.azure.com/openai/v1/`` formats". Genommen wird die
zweite, weil Claude auf Azure **nur** unter ihr liegt (`azure_anthropic`).
Damit ist es für den Betreiber derselbe Ressourcenname an beiden Zugängen und
nicht zwei Schreibweisen für dieselbe Sache.

**``model`` ist der Deployment-Name, nicht der Modellname.** Microsoft sagt es
zweimal ausdrücklich: „pass the deployment name in the ``model`` field" und
„Azure OpenAI always requires deployment name, even when using the ``model``
parameter". Wie das Deployment heißt, entscheidet der Betreiber beim Anlegen.

**Deshalb kein Katalog** (``catalog_url=None``). ``GET /openai/v1/models``
gibt es, und es antwortet auf den Schlüssel — aber ob darin die Deployments
dieser Ressource stehen oder die Basismodelle der Region, sagt weder die
v1-Referenz noch eine der beiden REST-Spezifikationen. Die einzige klare
Aussage dieser Art betrifft die **alte** Route
(``/openai/models?api-version=2024-10-21``: „models that are accessible by the
resource") und ist nicht übertragbar. Eine Auswahl anzubieten, die vielleicht
Basismodelle zeigt, hätte zwei Fehler auf einmal: der Betreiber wählt einen
Eintrag, den Azure mit „404: Confirm ``model`` matches your deployment name"
beantwortet, **und** er kann seinen eigenen Deployment-Namen nicht mehr
eintippen, weil eine nicht-leere Liste im Formular das Textfeld verdrängt.

**Die Fähigkeiten sind trotzdem nicht verloren.** ``faehigkeiten_aus`` greift
bei einem Anbieter ohne eigenen Katalog beim Nachschlagen des einzelnen
Modells (`ai_model_catalog.finde`): heißt das Deployment wie das Modell — die
Konvention, die Microsoft in seinen eigenen Beispielen verwendet („we often
have examples where deployment names are represented as identical to model
names") —, kennt MSM Kontextfenster und Denkstufen. Heißt es ``prod-chat``,
bleibt beides unbekannt, und „unbekannt" heißt hier wie überall nie „klein"
oder „kann er nicht".

**Die erste offene Frage ist beantwortet — vom Betrieb.** Hier stand, es sei
ungemessen, ob Azures ``/chat/completions`` wie OpenAIs eigenes eine Anfrage
mit ``tools`` *und* echter Denkstufe ablehnt. Es tut es: ein Zugang mit
``gpt-5.6-luna`` lief ohne Stufe und ging mit jeder Stufe nicht mehr durch.
Azures Referenz behauptet für ``gpt-5.1`` das Gegenteil („Tool calls are
supported for all reasoning values") — sie stimmt für diesen Endpunkt nicht.
Der Eintrag steht deshalb auf ``protokoll_chat="responses"``.

Der damalige Vorbehalt gegen ``/responses`` — es fehle womöglich bei den
Nicht-OpenAI-Modellen derselben Ressource — ist ebenfalls ausgeräumt, und zwar
von Microsoft selbst: „Responses API also works with Foundry Models sold by
Azure, such as Microsoft AI, DeepSeek, and Grok models." Ein Eintrag deckt
weiterhin beide Familien.

**Ungemessen bleibt eines**, und es steht hier, damit der nächste Leser es
nicht für geprüft hält:

* Ob eine aus OpenRouter geerbte Stufe ``max`` durchgeht. Azures Enum ist
  ``none|minimal|low|medium|high|xhigh``. Getroffen wird der Fall nur, wenn das
  Deployment wie das Modell heißt, OpenRouter für dieses Modell ``max`` führt
  **und** jemand ``max`` wählt; der Anbieter antwortet dann mit einer Meldung,
  die das Feld benennt. Eine Stufenliste im Programm wäre die gepflegte
  Tabelle, gegen die diese Registry gebaut ist — und ``xhigh`` liefert laut
  Microsofts eigener Anmerkung ohnehin dasselbe Ergebnis wie ``max``.
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell


ANBIETER = Anbieter(
    kind="azure_openai",
    label="Azure OpenAI",
    # Die Vorlage. ``{ressource}`` füllt `ai_provider_service.base_url` aus der
    # Zeile, nachdem `_assert_ressource` den Namen geprüft hat.
    base_url="https://{ressource}.services.ai.azure.com/openai/v1",
    # Kein Katalog — die Begründung steht oben und ist keine Bequemlichkeit.
    catalog_url=None,
    key_url="https://ai.azure.com/",
    # Azure-Schlüssel haben kein Präfix, an dem sich ein Vertipper erkennen
    # liesse. ``None`` heisst hier „keine Plausibilitätsprüfung möglich", nicht
    # „egal": der Testknopf bleibt der Beweis, und beim Wechsel der Ressource
    # wird der gespeicherte Schlüssel gelöscht, statt an die neue zu gehen.
    key_prefix=None,
    ressource_noetig=True,
    # ``api-key: <schluessel>`` — so steht es in Microsofts eigenem
    # cURL-Beispiel. ``Authorization: Bearer`` nähme Azure zwar auch an
    # (``ApiKeyAuth_``, „Endpoints accept any one of the following"), aber
    # `azure_anthropic` daneben tut es nicht: dort ist ``Bearer`` für
    # Entra-ID-Token reserviert. Ein Kopf für beide Azure-Zugänge.
    schluessel_kopf="api-key",
    schluessel_praefix="",
    # Woher Denkstufen und Kontextfenster kommen, wenn der Deployment-Name dem
    # Modellnamen entspricht. Siehe Moduldoku; greift über
    # `ai_model_catalog.finde`, weil es hier keine eigene Liste zum Anreichern
    # gibt.
    faehigkeiten_aus="openrouter",
    faehigkeiten_praefix="openai/",
    # OpenAIs Wortschatz fürs Nachdenken, und Azure führt ihn unverändert:
    # ``reasoning_effort`` als blosse Zeichenkette auf oberster Ebene,
    # nachgesehen in der v1-Referenz (``OpenAI.ReasoningEffort``).
    # OpenRouters ``reasoning`` und ``cache_control`` stehen hier bewusst
    # **nicht** — das sind deren Erweiterungen.
    anfrage_erweiterungen=frozenset({"reasoning_effort"}),
    # **Der Chatweg ist `/responses`**, aus demselben gemessenen Grund wie bei
    # `openai`. Hier stand bis zum 20.08.2026 ``chat_completions`` mit dem
    # ausdrücklichen Vermerk, es sei ungemessen, ob Azure sich wie OpenAI
    # verhält. Es tut es: der Betreiber hat einen Azure-Zugang mit
    # ``gpt-5.6-luna`` eingerichtet, und **sobald im Chat eine Denkstufe
    # gewählt war, ging keine Anfrage mehr durch**. Ohne Stufe lief derselbe
    # Zugang. Das ist genau die Grenze, die OpenAIs eigene Ablehnung benennt:
    #
    #     Function tools with reasoning_effort are not supported for <modell>
    #     in /v1/chat/completions. To use function tools, use /v1/responses
    #     or set reasoning_effort to 'none'.
    #
    # Die Grenze gehört dem Endpunkt und nicht dem Anbieter — Azure führt
    # denselben Code. Der zweite Ausweg (``reasoning_effort`` auf ``none``
    # festnageln) ist hier so verworfen wie dort: MSM schickt im Chat immer
    # Werkzeuge mit, und ein Zugang, an dem Nachdenken und Werkzeuge einander
    # ausschliessen, ist für den Hintergrund-Worker wertlos.
    #
    # Die Adresse passt ohne Zutun: `openai_responses_adapter` hängt
    # ``/responses`` an, was ``…/openai/v1/responses`` ergibt — wortgleich mit
    # Microsofts cURL-Beispiel zur v1-API.
    protokoll_chat="responses",
    realtime_tauglich=True,
    # **Kein Gehör.** Azure hat ``/openai/v1/audio/transcriptions``, aber nur
    # unter ``?api-version=preview`` — und für einen Query-Parameter hat weder
    # diese Registry ein Feld noch `ai_stt_endpunkt` einen Weg. Leer heisst
    # „kann es nicht", und das ist hier die ehrliche Auskunft: ein Sprachmodus,
    # der an einer Vorschau hängt, wäre ein Versprechen auf Widerruf.
    gehoer_wege=(),
    # **Keine Empfehlung**, und das ist keine Nachlässigkeit. Sie wäre eine
    # Meinung über eine Kennung, die es nur im Konto des Betreibers gibt: wie
    # sein Deployment heisst, weiss MSM nicht, und geraten wäre sie im besten
    # Fall wirkungslos.
    empfehlung=None,
)


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Wird nie gerufen — dieser Anbieter hat keinen Katalog.

    Die Funktion steht hier, weil `ai_provider_registry` sie aus **derselben
    Quelle** wie den Anbietereintrag sammelt: ein Modul ohne ``katalog_lesen``
    fiele beim Import auf, und ein Anbieter ohne Leser ist ein Zustand, den die
    Bauart ausschliesst. Bei ``catalog_url=None`` kommt `ai_model_catalog` gar
    nicht bis zum Abruf.

    ``None`` und keine Ausnahme: käme jemals ein Eintrag hier an, wäre das ein
    Fehler im Katalogmodul und nicht in den Daten — und eine Ausnahme aus einer
    Hintergrundauffrischung nähme die ganze Liste mit statt nur diesen Eintrag.
    """
    del rohdaten
    return None
