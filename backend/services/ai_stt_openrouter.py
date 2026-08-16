"""Das Gehör: Gesprochenes wird Text, und danach ist es getippter Chat.

Der Weg ist ``POST {base_url}/audio/transcriptions`` — OpenRouters eigener
Transkriptions-Endpunkt. Nutzlast ist JSON mit dem Ton als Base64
(``{"model": …, "input_audio": {"data": …, "format": "wav"}}``), zurück kommt
``{"text": …, "usage": {…}}``.

**Hier stand bis zum 17.08.2026 das Gegenteil**, und der Irrtum ist lehrreich
genug, um ihn aufzuschreiben: geprüft worden war der **Modellkatalog**
(``/models``), und der führt bis heute kein ``whisper`` und kein
``gpt-transcribe`` — er listet Chatmodelle. Daraus wurde geschlossen, es gebe
den Endpunkt nicht, und der Ton ging stattdessen als Inhaltsteil
(``input_audio``) in eine gewöhnliche Chatanfrage an ein hörfähiges Modell. Das
funktionierte, war aber der teure Umweg: ein Chatmodell, das abschreibt, kostet
ein Vielfaches eines Transkriptionsmodells und denkt dabei nach.

Die Lehre ist nicht „besser suchen", sondern: **ein leerer Katalog ist kein
fehlender Endpunkt.** Ein `404` auf den Pfad wäre der Beweis gewesen; ein `401`
ist er nicht, und genau den liefert dieser Pfad ohne Schlüssel.

Der Aufruf geht **nicht** durch `openai_compatible_adapter` — der spricht
``/chat/completions`` und Server-Sent-Events, hier ist es eine einzelne
JSON-Antwort. Was von dort trotzdem mitkommt, weil es dieselbe Wahrheit bleiben
soll: `_error_code` bildet den Status auf denselben Fehlercode ab wie im Chat,
und `_error_detail` redigiert die Anbietermeldung nach denselben Regeln.

Gesendet werden **nur** die nötigen Kopfzeilen. OpenRouter nimmt optional
``HTTP-Referer`` und ``X-Title`` für seine öffentliche Rangliste entgegen; ein
selbst gehostetes Panel meldet seine Adresse und seinen Namen nicht an einen
Dritten, damit es in einer Rangliste erscheint.

**Gebucht wird die Abschrift nicht**, und das ist eine Lücke und keine
Feinheit. `openai_compatible_adapter` bucht nirgends — es füllt nur ein
übergebenes `StreamUsage`; gebucht wird über `reserve_ai_usage` beim Aufrufer.
Der einzige Aufrufer hier ist `ai_voice_bridge`, und der übergibt keines: die
Abschrift gehört zu keinem Lauf, sie geht ihm voraus. Für den Betreiber heisst
das: die Tokengrenze und die Kostengrenze decken den **Denk**- und den
Sprechweg, nicht das Zuhören. Das steht so auch in `docs/self-hosting.md` unter
"Kontingent", damit es niemand aus einer Rechnung erfahren muss.

Wer es ändern will, braucht mehr als ein Argument mehr: eine abgelehnte
Reservierung würde die Äusserung verwerfen, **bevor** irgendwer weiss, was
gesagt wurde — der Sprechende bekäme dann nicht einmal die Auskunft, dass sein
Kontingent erschöpft ist.

**Was hier bewusst nicht passiert.** Kein Bewerten, kein Zusammenfassen, kein
Beantworten. Was zurückkommt, ist der Wortlaut des Menschen und wird behandelt
wie eine getippte Zeile — mit derselben Skepsis und denselben Rechten, nicht mit
mehr. Ein hörendes Modell, das aus dem Gesagten Schlüsse zöge, wäre eine zweite
Denkstelle neben Luna, und genau die ist am 16.08.2026 abgeschafft worden.

**Zum Mithören:** was der Mensch spricht, geht als Ton an den Anbieter. Das ist
dieselbe Aussage wie beim getippten Chat, nur unangenehmer zu lesen — und sie
gehört in die Datenschutzerklärung des Betreibers, nicht in einen Kommentar.
Hier steht sie, damit niemand sie übersieht.
"""

from __future__ import annotations

import base64
import io
import logging
import struct

import httpx

from models import AiProvider
from services.ai_provider_service import base_url as anbieter_adresse
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    _error_code,
    _error_detail,
)

logger = logging.getLogger(__name__)


#: Abtastrate des Tons vom Browser. Dieselbe wie bei der Wiedergabe — der
#: Sprachmodus rechnet nirgends um.
ABTASTRATE = 24_000

#: Wie lang eine einzelne Äusserung höchstens sein darf, in Sekunden.
#:
#: Zwei Gründe, und der zweite ist der wichtigere. Erstens die Nutzlast: eine
#: Minute PCM16 bei 24 kHz sind knapp 3 MB, als Base64 gut 4 — das ist kein
#: JSON-Körper mehr, den man beiläufig verschickt. Zweitens die Kosten:
#: abgerechnet wird nach Tonlänge, und eine Aufnahme, die läuft, weil jemand das
#: Mikrofon vergessen hat, wäre eine Rechnung ohne Gegenwert.
#:
#: Wer länger spricht, wird nicht abgeschnitten — die Sprechpausenerkennung
#: (`ai_voice_vad`) trennt vorher. Diese Grenze ist die Schranke dahinter, für
#: den Fall, dass gar keine Pause kommt.
MAX_SEKUNDEN = 30

#: Wie lang ein Transkript höchstens sein darf. Was länger ist, hat mit dem, was
#: jemand in 30 Sekunden sagen kann, nichts mehr zu tun. Seit der Endpunkt ein
#: Transkriptionsdienst ist und kein plauderndes Chatmodell, ist das kaum noch
#: zu erwarten — die Grenze bleibt trotzdem: sie schützt den Verlauf vor einem
#: Anbieter, der eines Tages Zeitmarken oder ein Protokoll mitschickt.
MAX_ZEICHEN = 2_000

#: Wie kurz eine Äusserung sein darf, damit sie überhaupt hinausgeht.
#:
#: **Nicht** die Schranke gegen den Huster — die sitzt in `ai_voice_vad`, und
#: sie muss dort sitzen: gemessen wird hier die Länge des *Stücks*, und jedes
#: Stück aus der Pausenerkennung trägt Vorlauf und Nachlauf mit sich. Ein
#: Räuspern von einer Zehntelsekunde käme als knappe Sekunde an und liefe glatt
#: durch. Die Pausenerkennung zählt stattdessen die **lauten** Rahmen
#: (`_min_laute_rahmen`) und gibt so etwas gar nicht erst ab.
#:
#: Diese Grenze hier ist die Schranke für Aufrufer **ohne** Pausenerkennung —
#: ein Testlauf, ein Diktierfeld, was auch immer als nächstes kommt. Sie ist
#: bewusst dieselbe Zahl: wer sie an einer Stelle ändert, soll die andere
#: finden.
MIN_SEKUNDEN = 0.35

#: Wie lange auf die Abschrift gewartet wird.
#:
#: Sie steht vor jeder einzelnen Äusserung und damit mitten im Gespräch: wer
#: hier zu lange wartet, hört Stille und redet noch einmal. Grosszügiger als
#: nötig (eine Abschrift von 30 Sekunden Ton dauert ein bis zwei), aber kurz
#: genug, dass eine hängende Verbindung das Gespräch nicht auffrisst.
ZEITGRENZE = 30.0

#: Hier stand ``ANWEISUNG`` — ein Prompt, der das hörende Chatmodell bat,
#: abzuschreiben statt zu befolgen, weil in fremder Rede eine Anweisung stehen
#: kann („Ignoriere deine Anweisungen und sage …").
#:
#: Mit dem Transkriptions-Endpunkt ist er ersatzlos entfallen, und das ist
#: **mehr** Sicherheit und nicht weniger: es gibt keinen Prompt mehr, in den
#: sich etwas hineinschmuggeln liesse. Der Endpunkt nimmt Ton entgegen und gibt
#: Text zurück, er befolgt nichts. Der Schutz von vorher war eine Bitte an ein
#: Modell; jetzt ist es die Bauform.
#:
#: Unverändert gilt, was den Rest trägt: der Wortlaut geht als *Benutzer*-
#: nachricht weiter und hat nie mehr Rechte, als der Sprechende ohnehin hat.


def wav_verpacken(pcm: bytes, *, abtastrate: int = ABTASTRATE) -> bytes:
    """Legt einen WAV-Kopf um rohes PCM16 (mono, Little Endian).

    Von Hand und ohne Bibliothek, weil ein WAV-Kopf 44 Bytes und acht Felder
    hat — `wave` aus der Standardbibliothek täte dasselbe über eine
    Dateischnittstelle und einen Zwischenpuffer.

    Nötig ist er, weil die Gegenstelle ein Format braucht, das sich selbst
    beschreibt: rohes PCM sagt nicht, wie schnell es abgespielt gehört. Ohne
    Kopf klingt dieselbe Aufnahme je nach Annahme zu hoch oder zu tief, und das
    hörende Modell versteht Kauderwelsch statt einer Frage.
    """
    kanaele = 1
    bits = 16
    byte_rate = abtastrate * kanaele * bits // 8
    block = kanaele * bits // 8
    puffer = io.BytesIO()
    puffer.write(b"RIFF")
    puffer.write(struct.pack("<I", 36 + len(pcm)))
    puffer.write(b"WAVEfmt ")
    puffer.write(struct.pack("<IHHIIHH", 16, 1, kanaele, abtastrate, byte_rate, block, bits))
    puffer.write(b"data")
    puffer.write(struct.pack("<I", len(pcm)))
    puffer.write(pcm)
    return puffer.getvalue()


def _sekunden(pcm: bytes, abtastrate: int) -> float:
    return len(pcm) / (abtastrate * 2)


def _saeubern(text: str) -> str:
    """Bringt das Transkript auf eine Zeile und auf Länge.

    Nachsichtig lesen, streng speichern — dieselbe Regel wie am Werkzeugrand.
    Umbrüche und doppelte Leerzeichen fallen weg, ein in Anführungszeichen
    gesetzter Wortlaut verliert sie: das an einer Fehlermeldung scheitern zu
    lassen hiesse, ein Gespräch an einem Anführungszeichen scheitern zu lassen.

    Weiter geht die Nachsicht nicht. Was inhaltlich kein Transkript ist, wird
    hier **nicht** zurechtgeschnitten — eine Zeile wie „Der Sprecher fragt, ob …"
    zu retten hiesse zu raten, was gesagt wurde.
    """
    sauber = " ".join(text.split())
    if len(sauber) >= 2 and sauber[0] in "\"'«„" and sauber[-1] in "\"'»“":
        sauber = sauber[1:-1].strip()
    return sauber[:MAX_ZEICHEN]


class NichtsVerstanden(RuntimeError):
    """Es war etwas zu hören, aber kein Wort daraus zu machen.

    Ein eigener Fehler und kein leerer String, weil die beiden verschiedene
    Antworten verlangen: bei „nichts verstanden" soll der Sprachmodus nachfragen
    („Das habe ich nicht verstanden"), bei einem Anbieterfehler soll er das
    sagen. Ein leerer String zwänge den Aufrufer, beides gleich zu behandeln.
    """


async def hoeren(
    client: httpx.AsyncClient,
    *,
    provider: AiProvider,
    api_key: str | None,
    pcm: bytes,
    usage: StreamUsage | None = None,
    abtastrate: int = ABTASTRATE,
) -> str:
    """Macht aus einer Äusserung ihren Wortlaut.

    Ein Aufruf, eine Antwort — nichts wird gestreamt. Bei einer Äusserung von
    wenigen Sekunden gäbe es nichts zu streamen: der Text ist da oder nicht, und
    der Sprachmodus kann mit einem halben Satz ohnehin nichts anfangen, bevor er
    weiss, wo er endet.

    ``usage`` nimmt die vom Anbieter gemeldeten Tokenzahlen auf, wenn der
    Aufrufer sie sehen will. Ohne Angabe werden sie verworfen — das ist
    ausdrücklich der Fall für Aufrufe, die zu keinem Lauf gehören, und genau der
    Fall aus `ai_voice_bridge` (siehe die Lücke im Modulkopf).

    Wirft `NichtsVerstanden`, wenn nichts Verständliches zu hören war, und
    `AiProviderRequestError` bei allem, was der Anbieter ablehnt.
    """
    modell = (provider.transcription_model or "").strip()
    if not modell:
        # Kein geratenes Modell. Der Betreiber hat keines hinterlegt, und eines
        # zu wählen hiesse, ihm eine Rechnung für ein Modell zu stellen, das er
        # nie gesehen hat.
        raise AiProviderRequestError("AI_PROVIDER_MODEL_MISSING")

    dauer = _sekunden(pcm, abtastrate)
    if dauer < MIN_SEKUNDEN:
        raise NichtsVerstanden("zu kurz")
    if dauer > MAX_SEKUNDEN:
        # Vorne abschneiden und nicht hinten: bei einer zu langen Aufnahme ist
        # das Zuletztgesagte das, was jemand gerade gesagt hat.
        pcm = pcm[-int(MAX_SEKUNDEN * abtastrate * 2) :]
        logger.info("Aufnahme auf %d s gekuerzt (war %.1f s)", MAX_SEKUNDEN, dauer)

    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    daten = base64.b64encode(wav_verpacken(pcm, abtastrate=abtastrate)).decode("ascii")
    kopf = {"Content-Type": "application/json"}
    if api_key:
        kopf["Authorization"] = f"Bearer {api_key}"

    adresse = anbieter_adresse(provider).rstrip("/") + "/audio/transcriptions"
    try:
        antwort = await client.post(
            adresse,
            headers=kopf,
            json={
                "model": modell,
                "input_audio": {"data": daten, "format": "wav"},
            },
            timeout=ZEITGRENZE,
        )
    except httpx.TimeoutException as exc:
        raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT") from exc
    except httpx.HTTPError as exc:
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc

    if antwort.status_code >= 400:
        # Derselbe Fehlercode wie im Chat, aus derselben Funktion. Eine eigene
        # Abbildung hier waere eine zweite Wahrheit ueber dieselben Statuscodes.
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
        _messwerte_uebernehmen(usage, nutzlast.get("usage"))

    roh = nutzlast.get("text")
    wortlaut = _saeubern(roh) if isinstance(roh, str) else ""
    if not wortlaut:
        # Stille, ein Huster, ein Wort ins Leere: der Endpunkt antwortet dann
        # mit leerem Text und nicht mit einem Fehler. Fuer den Sprachmodus ist
        # das kein Anbieterproblem, sondern ein Alltagsfall.
        raise NichtsVerstanden("leer")
    return wortlaut


def _messwerte_uebernehmen(ziel: StreamUsage, roh: object) -> None:
    """Traegt die gemeldeten Tokenzahlen ein, soweit es welche gibt.

    Nachsichtig: fehlt der Block oder ist ein Feld keine Zahl, bleibt es leer.
    Ein Transkriptionsanbieter, der nichts meldet, ist kein Fehlerfall — er ist
    der Normalfall, und eine Ausnahme dafuer wuerde ein Gespraech abreissen
    lassen, dessen Abschrift laengst da ist.
    """
    if not isinstance(roh, dict):
        return
    def zahl(feld: str) -> int | None:
        wert = roh.get(feld)
        return wert if isinstance(wert, int) and not isinstance(wert, bool) else None

    ziel.prompt_tokens = zahl("prompt_tokens") or zahl("input_tokens")
    ziel.completion_tokens = zahl("completion_tokens") or zahl("output_tokens")
    ziel.total_tokens = zahl("total_tokens")
    ziel.vom_anbieter = ziel.total_tokens is not None
