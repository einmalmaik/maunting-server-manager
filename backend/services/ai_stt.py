"""Das Gehör: Gesprochenes wird Text, und danach ist es getippter Chat.

Hier steht, was **jede** Hörweise gleich macht: nachsehen, ob ein Modell
hinterlegt ist, die Äusserung auf eine vernünftige Länge bringen, sie in ein
WAV verpacken, das Ergebnis säubern und entscheiden, wann „nichts verstanden"
gilt. Wie der Ton dann tatsächlich zum Anbieter kommt, steht je Weg in einer
eigenen Datei:

* `ai_stt_endpunkt` — ``POST /audio/transcriptions``, ein Dienst, der nur
  abschreibt. Der richtige Weg, wenn es ihn gibt.
* `ai_stt_chat` — ``POST /chat/completions`` mit dem Ton als ``input_audio``.
  Der Umweg über ein hörfähiges **Chat**modell.

**Warum es beide gibt, und das ist keine Unentschlossenheit.** Der Endpunkt ist
sachlich der bessere Weg: ein Abschreibdienst kostet einen Bruchteil und denkt
nicht nach. Aber er wird bei OpenRouter aus Guthaben bezahlt und **nicht** über
den hinterlegten Fremdschlüssel (BYOK) — wer dort kein Guthaben hat, kann ihn
nicht benutzen, während sein Chat weiterläuft. Genau dieser Fall ist am
17.08.2026 im Betrieb aufgetreten. Der Chatweg dagegen läuft über dieselbe
Abrechnung wie alles andere und es gibt dort hörfähige Modelle zum Nulltarif.

Eine Hörweise ist damit kein Geschmack, sondern eine Frage danach, was das Konto
des Betreibers hergibt. Deshalb wählt er sie (`MSM_AI_STT_WEG`) und MSM rät
nicht.

**Wie man eine Hörweise wieder loswird:** die Datei löschen, ihren Eintrag aus
`_WEGE` streichen, ihren Namen aus `gehoer_wege` des betroffenen Anbieters
nehmen. Sonst hängt nichts an ihr — kein Aufrufer kennt sie, `ai_voice_bridge`
ruft nur `hoeren()`.

**Was hier bewusst nicht passiert.** Kein Bewerten, kein Zusammenfassen, kein
Beantworten. Was zurückkommt, ist der Wortlaut des Menschen und wird behandelt
wie eine getippte Zeile — mit derselben Skepsis und denselben Rechten, nicht mit
mehr. Ein hörendes Modell, das aus dem Gesagten Schlüsse zöge, wäre eine zweite
Denkstelle neben Luna, und genau die ist am 16.08.2026 abgeschafft worden.

**Gebucht wird die Abschrift beim Aufrufer**, nicht hier. `ai_voice_bridge`
übergibt ein `StreamUsage` und verbucht es nach gelungener Abschrift als
eigenen Verbrauch (`_abschrift_verbuchen`) — das Zuhören zählt damit gegen
dieselben Tages- und Monatsgrenzen wie der Denk- und der Sprechweg. Die
Buchung steht bewusst **nach** dem Hören: eine Reservierung davor würde die
Äusserung verwerfen, bevor irgendwer weiss, was gesagt wurde — der Sprechende
bekäme dann nicht einmal die Auskunft, dass sein Kontingent erschöpft ist.
Dieses Modul selbst bucht nichts: es kennt keinen Benutzer, und eine zweite
Buchungsstelle neben `reserve_ai_usage` wäre eine zweite Wahrheit über
dieselben Grenzen.

**Zum Mithören:** was der Mensch spricht, geht als Ton an den Anbieter. Das ist
dieselbe Aussage wie beim getippten Chat, nur unangenehmer zu lesen — und sie
gehört in die Datenschutzerklärung des Betreibers, nicht in einen Kommentar.
Hier steht sie, damit niemand sie übersieht.
"""

from __future__ import annotations

import io
import logging
import struct
from typing import Awaitable, Callable

import httpx

from config import settings
from models import AiProvider
from services.ai_provider_registry import anbieter as anbieter_spec
from services.openai_compatible_adapter import AiProviderRequestError, StreamUsage

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
#: den Fall, dass gar keine Pause kommt (3 Minuten Puffer für lange Monologe).
MAX_SEKUNDEN = 180

#: Wie lang ein Transkript höchstens sein darf.
MAX_ZEICHEN = 10_000

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

#: Die Namen der Hörwege. Als Konstanten, weil sie an vier Stellen stehen: hier,
#: in `_WEGE`, in `gehoer_wege` je Anbieter und in der Einstellung des
#: Betreibers. Ein Tippfehler in einer davon soll auffallen und nicht dazu
#: führen, dass ein Anbieter still keinen Weg mehr hat.
WEG_ENDPUNKT = "endpunkt"
WEG_CHAT = "chat"


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


def messwerte_uebernehmen(ziel: StreamUsage, roh: object) -> None:
    """Trägt die gemeldeten Tokenzahlen ein, soweit es welche gibt.

    Nachsichtig: fehlt der Block oder ist ein Feld keine Zahl, bleibt es leer.
    Ein Transkriptionsanbieter, der nichts meldet, ist kein Fehlerfall — er ist
    der Normalfall, und eine Ausnahme dafür würde ein Gespräch abreissen lassen,
    dessen Abschrift längst da ist.

    Beide Schreibweisen werden gelesen: der Chatweg meldet ``prompt_tokens`` und
    ``completion_tokens``, der Transkriptionsendpunkt ``input_tokens`` und
    ``output_tokens``. Dieselbe Zahl unter zwei Namen ist kein Grund für zwei
    Funktionen.
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


#: Eine Hörweise: Ton hinein, Wortlaut heraus. Was sie zurückgibt, ist roh —
#: gesäubert und auf „leer heisst nichts verstanden" geprüft wird **hier**,
#: damit beide Wege dieselbe Antwort auf dieselbe Stille geben.
Hoerweise = Callable[..., Awaitable[str]]


def _wege() -> dict[str, Hoerweise]:
    """Die verfügbaren Hörwege.

    Die Importe stehen absichtlich in der Funktion und nicht am Dateikopf. Nicht
    wegen eines Importzyklus, sondern damit das Löschen einer Hörweise
    tatsächlich beim Löschen der Datei endet: ein Import am Kopf würde beim
    Start der Anwendung scheitern, ein Eintrag hier fehlt nur dem, der ihn
    anfordert — und der bekommt eine Meldung, die den Namen nennt.
    """
    from services import ai_stt_chat, ai_stt_endpunkt

    return {
        WEG_ENDPUNKT: ai_stt_endpunkt.abschrift,
        WEG_CHAT: ai_stt_chat.abschrift,
    }


def weg_fuer(provider: AiProvider) -> str:
    """Welcher Hörweg für diesen Zugang gilt.

    Ohne Einstellung der erste, den der Anbieter kann — das ist der bessere
    Weg, weil `gehoer_wege` nach Güte sortiert ist. Mit Einstellung der
    gewählte, sofern der Anbieter ihn überhaupt spricht.

    Ein unpassender Wunsch ist ausdrücklich ein **Fehler** und keine stille
    Rückkehr zur Vorgabe: wer `MSM_AI_STT_WEG=endpunkt` an einem Anbieter
    setzt, der keinen Endpunkt hat, hat sich vertan — und eine Abschrift, die
    trotzdem gelingt, verbirgt das bis zur Rechnung.
    """
    spec = anbieter_spec(provider.provider_kind)
    if not spec.gehoer_wege:
        raise AiProviderRequestError("AI_PROVIDER_STT_UNSUPPORTED")
    gewuenscht = (settings.ai_stt_weg or "").strip()
    if not gewuenscht:
        return spec.gehoer_wege[0]
    if gewuenscht not in spec.gehoer_wege:
        raise AiProviderRequestError(
            "AI_PROVIDER_STT_UNSUPPORTED",
            f"{spec.label} kennt den Hoerweg {gewuenscht!r} nicht",
        )
    return gewuenscht


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
    Aufrufer sie sehen will — `ai_voice_bridge` verbucht sie damit nach
    gelungener Abschrift als eigenen Verbrauch (siehe Modulkopf). Ohne Angabe
    werden sie verworfen; das ist der Fall für Aufrufe ohne Buchungspflicht,
    etwa einen Testlauf.

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

    name = weg_fuer(provider)
    try:
        weg = _wege()[name]
    except KeyError as exc:
        # Die Datei ist weg, der Eintrag in `gehoer_wege` nicht. Genau der Fall,
        # den der späte Import offenhalten soll: es trifft den, der hört, und
        # nicht den Start der Anwendung.
        raise AiProviderRequestError(
            "AI_PROVIDER_STT_UNSUPPORTED", f"Hoerweg {name!r} ist nicht eingebaut"
        ) from exc

    wav = wav_verpacken(pcm, abtastrate=abtastrate)
    roh = await weg(
        client, provider=provider, api_key=api_key, modell=modell, wav=wav, usage=usage
    )

    wortlaut = _saeubern(roh) if isinstance(roh, str) else ""
    if not wortlaut:
        # Stille, ein Huster, ein Wort ins Leere: die Gegenstelle antwortet dann
        # mit leerem Text und nicht mit einem Fehler. Für den Sprachmodus ist
        # das kein Anbieterproblem, sondern ein Alltagsfall.
        raise NichtsVerstanden("leer")
    return wortlaut
