"""Hörweg über ``POST /audio/transcriptions`` — ein Dienst, der nur abschreibt.

Der sachlich bessere Weg, wo er bezahlbar ist: ein Abschreibmodell kostet einen
Bruchteil eines Chatmodells und denkt nicht nach.

**Hier stand bis zum 17.08.2026, es gebe diesen Endpunkt bei OpenRouter nicht**,
und der Irrtum ist lehrreich genug, um ihn aufzuschreiben: geprüft worden war
der **Modellkatalog** (``/models``), und der führt bis heute kein ``whisper``
und kein ``gpt-transcribe`` — er listet Chatmodelle. Daraus wurde geschlossen,
es gebe den Endpunkt nicht. Die Lehre ist nicht „besser suchen", sondern: **ein
leerer Katalog ist kein fehlender Endpunkt.** Ein ``404`` auf den Pfad wäre der
Beweis gewesen; ein ``401`` ist er nicht, und genau den liefert dieser Pfad
ohne Schlüssel.

**Zwei Nutzlastformen, und beide sind Standard — nur nicht derselbe.**

* ``json`` — OpenRouters Form: ``{"model": …, "input_audio": {"data": …,
  "format": "wav"}}``, der Ton als Base64 im Körper.
* ``multipart`` — OpenAIs Form aus deren offizieller ``openapi.yaml``:
  ``multipart/form-data`` mit ``file`` und ``model``.

Welche gilt, sagt `Anbieter.gehoer_form`. Die beiden ineinander umzurechnen geht
nicht, und zu raten lohnt sich nicht: die falsche Form endet in einem ``400``,
das wie ein kaputter Ton aussieht und keiner ist.

**Der Aufruf geht nicht durch `openai_compatible_adapter`** — der spricht
``/chat/completions`` und Server-Sent-Events, hier ist es eine einzelne
JSON-Antwort. Was von dort trotzdem mitkommt, weil es dieselbe Wahrheit bleiben
soll: `_error_code` bildet den Status auf denselben Fehlercode ab wie im Chat,
und `_error_detail` redigiert die Anbietermeldung nach denselben Regeln.

Gesendet werden **nur** die nötigen Kopfzeilen. OpenRouter nimmt optional
``HTTP-Referer`` und ``X-Title`` für seine öffentliche Rangliste entgegen; ein
selbst gehostetes Panel meldet seine Adresse und seinen Namen nicht an einen
Dritten, damit es in einer Rangliste erscheint.

**Kein Prompt, und das ist mehr Sicherheit und nicht weniger.** Der Chatweg
braucht einen (`ai_stt_chat.ANWEISUNG`), der das Modell bittet abzuschreiben
statt zu befolgen — denn in fremder Rede kann eine Anweisung stehen. Hier gibt
es keinen Prompt, in den sich etwas hineinschmuggeln liesse: der Endpunkt nimmt
Ton entgegen und gibt Text zurück, er befolgt nichts. Der Schutz ist die
Bauform statt einer Bitte.

Das Feld ``prompt`` des Endpunkts bleibt aus demselben Grund leer, obwohl
OpenAI es anbietet. Es würde die Erkennung von Fachwörtern verbessern und
dieselbe Tür wieder öffnen.
"""

from __future__ import annotations

import base64
import logging

import httpx

from models import AiProvider
from services.ai_provider_service import base_url as anbieter_adresse
from services.ai_provider_registry import anbieter as anbieter_spec
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    _error_code,
    _error_detail,
)

logger = logging.getLogger(__name__)

#: Die Nutzlastformen. Als Konstanten, damit ein vertipptes ``"multipar"`` am
#: Anbieter sofort auffällt statt still in den JSON-Zweig zu fallen.
FORM_JSON = "json"
FORM_MULTIPART = "multipart"


async def abschrift(
    client: httpx.AsyncClient,
    *,
    provider: AiProvider,
    api_key: str | None,
    modell: str,
    wav: bytes,
    usage: StreamUsage | None = None,
) -> str:
    """Schickt das WAV an den Transkriptionsendpunkt und gibt den Wortlaut roh zurück.

    Roh heisst: ungesäubert und ungeprüft. Ob daraus „nichts verstanden" wird,
    entscheidet `ai_stt.hoeren` — für beide Hörwege an einer Stelle.
    """
    from services.ai_stt import ZEITGRENZE, messwerte_uebernehmen

    spec = anbieter_spec(provider.provider_kind)
    adresse = anbieter_adresse(provider).rstrip("/") + "/audio/transcriptions"

    kopf: dict[str, str] = {}
    if api_key:
        kopf[spec.schluessel_kopf] = f"{spec.schluessel_praefix}{api_key}"

    if spec.gehoer_form == FORM_MULTIPART:
        # Der Dateiname ist nicht schmückendes Beiwerk: OpenAI verlangt
        # ausdrücklich „enough format metadata for the file to be identified"
        # und empfiehlt eine Endung samt passendem Inhaltstyp. Ohne beides
        # antwortet der Endpunkt mit einem Formatfehler auf einwandfreies WAV.
        anfrage = {
            "files": {"file": ("aeusserung.wav", wav, "audio/wav")},
            "data": {"model": modell},
        }
    else:
        kopf["Content-Type"] = "application/json"
        anfrage = {
            "json": {
                "model": modell,
                "input_audio": {
                    "data": base64.b64encode(wav).decode("ascii"),
                    # Roh und ohne ``data:``-Präfix. Eine Daten-URL wäre hier
                    # der naheliegende Fehler und wird als kaputter Ton
                    # abgelehnt, nicht als Formfehler gemeldet.
                    "format": "wav",
                },
            }
        }

    try:
        antwort = await client.post(adresse, headers=kopf, timeout=ZEITGRENZE, **anfrage)
    except httpx.TimeoutException as exc:
        raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc

    if antwort.status_code >= 400:
        # Derselbe Fehlercode wie im Chat, aus derselben Funktion. Eine eigene
        # Abbildung hier wäre eine zweite Wahrheit über dieselben Statuscodes.
        raise AiProviderRequestError(
            _error_code(antwort.status_code), await _error_detail(antwort)
        )

    try:
        nutzlast = antwort.json()
    except ValueError as exc:
        raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
    if not isinstance(nutzlast, dict):
        raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")

    if usage is not None:
        messwerte_uebernehmen(usage, nutzlast.get("usage"))

    roh = nutzlast.get("text")
    return roh if isinstance(roh, str) else ""
