"""Die KI schreibt Betreff und Text ihrer Mails selbst — in Feldern, nicht als Fliesstext.

Der Betreiber hat eine Berichtsmail beanstandet: sie las sich wie ein Formular,
in das jemand einen Modellabsatz gelegt hat. Der feste Satz „Deine KI-Aufgabe
… war fällig“ stand ueber einem Text, der dasselbe noch einmal sagte, und die
Betreffzeile nannte den Namen der Aufgabe statt ihres Ergebnisses. Gewuenscht
ist das Gegenteil: die KI, die den Lauf gemacht hat, formuliert auch die
Nachricht darueber — **alle drei** Mails, ausdruecklich auch die Testmail.

Drei Entscheidungen tragen dieses Modul:

**Felder statt Freitext.** Das Modell liefert `betreff`, `absaetze`, `punkte`
und `schluss` — reinen Text, ohne jede Auszeichnung. Das Markup setzt MSM in
`EmailService._ai_report_email_html`. Der Grund ist doppelt: es gibt im Backend
keinen Markdown-Leser (weshalb `**Laufend:**` woertlich in einer Mail stand)
und keinen HTML-Reiniger — und es soll auch keinen geben. Felder loesen
Formatierung und Injektionsgefahr in einem Zug.

**Erzwungen ueber ein Werkzeug, nicht ueber eine Bitte.** „Antworte als JSON“
ist eine Bitte, `tool_choice` auf genau ein Werkzeug ist eine Vorgabe. Das
Schema steht hier und ausdruecklich **nicht** in `ai_tool_registry`: dort sind
die Werkzeuglisten bewusst ausgeschriebene Aufzaehlungen, und
`execute_read_tool` bekommt bewusst keine `run_id`. Dieses Werkzeug wird nie
ausgefuehrt — es ist ein Formular, kein Werkzeug.

**Der Rueckfall verschickt trotzdem.** Faellt der Modellaufruf aus, ist er
leer, oder sind die Felder unbrauchbar, liefert `verfassen` ``None`` und die
Mail geht mit dem festen Text hinaus, den `EmailService` seit jeher kennt.
Besonders bei der Testmail: sie ist das Messgeraet fuer den Versandweg und darf
nicht ausgerechnet dann schweigen, wenn das Modell klemmt.

Was hier **nicht** entschieden wird: an wen geschrieben wird (`ai_mail`), ob
etwas geschafft wurde (das Panel, ueber `geschafft`/`geheilt`) und wie die Mail
aussieht (`EmailService`). Das Modell schreibt den Text und sonst nichts.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx

from database import SessionLocal
from models import AiProvider, User
from services import ai_reasoning
from services.ai_provider_service import (
    anbieter_ohne_auswahl,
    estimate_cost_microunits,
    resolve_api_key,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    abrechnung,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)

#: So lang darf der Teil des Betreffs sein, den das Modell beisteuert. Davor
#: steht noch die Kennung des Panels und das Zustandswort; zusammen bleibt die
#: Zeile unter dem, was ein Postfach in der Uebersicht anzeigt.
MAX_BETREFF_ZEICHEN = 120

#: Obergrenze fuer die ganze Betreffzeile, nachdem MSM sie zusammengesetzt hat.
MAX_BETREFFZEILE_ZEICHEN = 200

#: Wieviele Absaetze, Punkte und Zeichen aus dem Modell in die Mail duerfen.
#: Eine Mail ist kein Protokoll: wer mehr braucht, sieht im KI-Chat nach.
MAX_ABSAETZE = 6
MAX_ABSATZ_ZEICHEN = 1200
MAX_PUNKTE = 12
MAX_PUNKT_ZEICHEN = 300
MAX_SCHLUSS_ZEICHEN = 400

#: Wie lange der Verfassungsschritt hoechstens dauern darf. Deutlich knapper als
#: der Lauf selbst (dort 90 s Lesefrist): hier haengt eine fertige Nachricht
#: daran, die auch ohne diesen Schritt vollstaendig ist. Lieber der feste Text
#: sofort als der schoene in zwei Minuten.
LESEFRIST_SEKUNDEN = 30.0

#: Der Name des Formulars. Steht als Konstante da, weil ihn drei Stellen lesen:
#: das Schema, die Auswertung der Antwort und der Test.
WERKZEUG_NAME = "mail_verfassen"

#: Das eine Werkzeug, das dieser Schritt anbietet — und das nie ausgefuehrt
#: wird. `tool_choice` zwingt das Modell hinein; was zurueckkommt, sind die
#: Argumente und nichts sonst.
WERKZEUG: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WERKZEUG_NAME,
        "description": (
            "Verfasse Betreff und Text der E-Mail an den Betreiber. "
            "Nur reiner Text, keine Formatierung."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "betreff": {
                    "type": "string",
                    "description": (
                        "Kurze, konkrete Betreffzeile ohne Praefix wie 'MSM:' "
                        "oder 'Bericht:'. Nennt das Ergebnis, nicht das Thema."
                    ),
                },
                "absaetze": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ein bis drei Saetze je Absatz, reiner Text. Kein "
                        "Markdown, keine Sternchen, keine Aufzaehlungszeichen, "
                        "kein HTML, keine Links."
                    ),
                },
                "punkte": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optionale Aufzaehlung, je ein kurzer Eintrag ohne "
                        "fuehrenden Strich. Weglassen, wenn es nichts "
                        "aufzuzaehlen gibt."
                    ),
                },
                "schluss": {
                    "type": "string",
                    "description": "Optionaler Schlusssatz, ein Satz.",
                },
            },
            "required": ["betreff", "absaetze"],
        },
    },
}

SYSTEMPROMPT = (
    "Du bist der Serverassistent des Maunting Service Managers und schreibst "
    "eine E-Mail an den Betreiber, der gerade nicht davorsass.\n"
    f"Rufe genau einmal das Werkzeug {WERKZEUG_NAME} auf und schreibe deinen "
    "Text ausschliesslich in dessen Felder.\n"
    "Schreibe auf Deutsch, sachlich, in der Du-Form, ohne Anrede und ohne "
    "Grussformel — beides setzt das Panel.\n"
    "Schreibe reinen Text: kein Markdown, keine Sternchen, keine Rauten, keine "
    "Bindestrich-Listen, kein HTML, keine Links, keine E-Mail-Adressen.\n"
    "Nenne nur, was in den Angaben steht. Erfinde keine Servernamen, keine "
    "Zahlen und keine Zeitpunkte, und behaupte kein Ergebnis, das dort nicht "
    "steht — ueber Erfolg oder Misserfolg schreibt das Panel selbst.\n"
    "Nenne niemals Passwoerter, Schluessel oder Tokens.\n"
    "Fasse zusammen, statt Logzeilen abzuschreiben."
)


@dataclass
class Mailtext:
    """Was das Modell beisteuert. Alles darin ist Text und wird maskiert."""

    betreff: str = ""
    absaetze: list[str] = field(default_factory=list)
    punkte: list[str] = field(default_factory=list)
    schluss: str | None = None


def betreff_bereinigen(roh: Any, *, grenze: int = MAX_BETREFF_ZEICHEN) -> str:
    """Macht aus Fremdtext eine Betreffzeile — oder aus nichts einen leeren String.

    Der Vorfall dahinter kostete eine ganze Mail und meldete dabei Erfolg:
    `EmailService.send_email` setzt ``msg["Subject"] = subject`` **vor** dem
    ``try``. Steht ein Zeilenumbruch darin, wirft Pythons Header-Pruefung eine
    `ValueError`, die an der Fehlerbehandlung des Versands vorbei bis nach
    `ai_mail.zustellen` durchlaeuft — und dort als Warnung im Log endet. Der
    Lauf ist gruen, der Betreiber wartet auf Post, die es nie gab.

    Entfernt werden deshalb **alle** Steuerzeichen, nicht nur CR und LF: ein
    NUL oder ein Escape in einem Header ist genauso wenig zustellbar, und die
    Unterscheidung waere eine Einladung, den naechsten Fall zu uebersehen.
    Mehrfache Leerzeichen fallen dabei zu einem zusammen, weil ein umgebrochener
    Betreff sonst als Wortsalat ankaeme.
    """
    if roh is None:
        return ""
    text = str(roh)
    # `Cc` ist die Unicode-Kategorie der Steuerzeichen, `Cf` die der
    # Formatzeichen (Rechts-nach-links-Marken und Verwandtes). Beide haben in
    # einer Kopfzeile nichts zu suchen und beide sind unsichtbar — genau die
    # Sorte Zeichen, die man in einem Test nicht bemerkt.
    sauber = "".join(
        " " if unicodedata.category(zeichen) in ("Cc", "Cf") else zeichen
        for zeichen in text
    )
    return " ".join(sauber.split())[:grenze].strip()


def _feldtext(roh: Any, grenze: int) -> str:
    """Ein einzelnes Textfeld aus der Modellantwort — geschwaerzt und gekuerzt.

    Nachsichtig gelesen: eine Zahl oder ein `None` an einer Stelle, an der ein
    String stehen sollte, ist ein Formfehler und kein Grund, die ganze Mail auf
    den festen Text zurueckfallen zu lassen. Streng gespeichert wird trotzdem —
    was hier herauskommt, ist Text und sonst nichts.
    """
    if roh is None or isinstance(roh, (dict, list)):
        return ""
    return redact_sensitive_text(str(roh)).strip()[:grenze]


def _liste(roh: Any, *, grenze: int, anzahl: int) -> list[str]:
    """Eine Liste von Textfeldern. Ein einzelner String gilt als Liste mit einem Eintrag.

    Modelle liefern `absaetze` gelegentlich als einen langen String statt als
    Feld mit einem Eintrag. Das ist dieselbe Aussage in einer anderen Form —
    daran soll keine Mail scheitern.
    """
    if isinstance(roh, str):
        roh = [teil for teil in roh.split("\n\n") if teil.strip()]
    if not isinstance(roh, list):
        return []
    ergebnis: list[str] = []
    for eintrag in roh:
        text = _feldtext(eintrag, grenze)
        if text:
            ergebnis.append(text)
        if len(ergebnis) >= anzahl:
            break
    return ergebnis


def auswerten(argumente: Any) -> Mailtext | None:
    """Macht aus den Werkzeugargumenten einen `Mailtext` — oder ``None``.

    ``None`` heisst „nimm den festen Text“. Das ist der Fall bei fehlendem
    Betreff und bei fehlenden Absaetzen: eine Mail ohne Betreff oder ohne Text
    waere schlechter als die Vorlage, die es ohnehin gibt. Alles andere ist
    optional — ein Bericht ohne Aufzaehlung ist ein normaler Bericht.
    """
    if not isinstance(argumente, dict):
        return None
    betreff = betreff_bereinigen(
        redact_sensitive_text(str(argumente.get("betreff") or ""))
    )
    absaetze = _liste(
        argumente.get("absaetze"), grenze=MAX_ABSATZ_ZEICHEN, anzahl=MAX_ABSAETZE
    )
    if not betreff or not absaetze:
        return None
    return Mailtext(
        betreff=betreff,
        absaetze=absaetze,
        punkte=_liste(
            argumente.get("punkte"), grenze=MAX_PUNKT_ZEICHEN, anzahl=MAX_PUNKTE
        ),
        schluss=_feldtext(argumente.get("schluss"), MAX_SCHLUSS_ZEICHEN) or None,
    )


def _vorbereiten(
    user_id: int, provider_id: int | None, fakten: str
) -> tuple[AiProvider, str | None, list[dict], UUID, int] | None:
    """Anbieter, Schluessel, Nachrichten und die gebuchte Reservierung.

    Eigene, kurze Sitzung und danach `expunge` — dasselbe Muster wie in
    `ai_compaction_service`, aus demselben Grund: waehrend des Providerrufs
    darf keine Transaktion offen stehen. Der Aufruf kommt hier zusaetzlich aus
    dem Arbeiter am Ausgangskorb (`ai_mail_outbox._verfassen_lassen`), der
    keiner Anfrage gehoert — eine geliehene Sitzung haette dort erst recht
    nichts zu suchen.
    """
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        provider = (
            db.get(AiProvider, provider_id)
            if provider_id
            else anbieter_ohne_auswahl(db, user)
        )
        if provider is None or not provider.enabled:
            return None
        api_key = resolve_api_key(db, provider, user.id)
        if provider.requires_api_key and not api_key:
            return None

        messages = [
            {"role": "system", "content": SYSTEMPROMPT},
            {"role": "user", "content": f"Angaben fuer die Mail:\n{fakten}"},
        ]
        geschaetzt = max(1, (len(SYSTEMPROMPT) + len(fakten)) // 4)
        request_id = uuid4()
        try:
            ereignis = reserve_ai_usage(
                db,
                user,
                request_id=request_id,
                estimated_tokens=geschaetzt,
                estimated_cost_microunits=estimate_cost_microunits(
                    provider, geschaetzt
                ),
                provider_id=provider.id,
                model=provider.default_model,
            )
            db.commit()
            del ereignis
        except (AiQuotaExceeded, AiUsageConflict):
            # Kein Kontingent fuer den Verfassungsschritt: dann geht die Mail
            # mit dem festen Text hinaus. Sie am Kontingent vorbei zu schreiben
            # waere genau der unsichtbare Verbrauch, den die Buchung verhindern
            # soll — und der feste Text sagt dasselbe, nur nuechterner.
            db.rollback()
            return None
        db.refresh(provider)
        db.expunge(provider)
    return provider, api_key, messages, request_id, geschaetzt


def _usage_event(db, request_id: UUID):
    from models import AiUsageEvent

    return (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(request_id))
        .first()
    )


def _abrechnen(
    request_id: UUID, provider: AiProvider, usage: StreamUsage, geschaetzt: int,
    *, gescheitert: bool,
) -> None:
    """Bucht den Verbrauch dieses Schritts — auch wenn er nichts geliefert hat.

    Ein abgebrochener Providerruf kann trotzdem Tokens gekostet haben. Die
    Reservierung offen stehen zu lassen waere schlimmer als beides: sie zaehlt
    dann dauerhaft gegen das Kontingent des Benutzers, ohne je abzulaufen.
    """
    with SessionLocal() as db:
        ereignis = _usage_event(db, request_id)
        if ereignis is None or ereignis.status != "reserved":
            return
        if gescheitert:
            fail_ai_usage(db, ereignis)
            db.commit()
            return
        tokens, kosten, herkunft = abrechnung(
            usage,
            reserved_tokens=ereignis.reserved_tokens,
            estimated_actual_tokens=geschaetzt,
            token_price_micro_usd_per_million=(
                provider.token_price_micro_usd_per_million
            ),
        )
        complete_ai_usage(
            db, ereignis,
            actual_tokens=tokens,
            actual_cost_microunits=kosten,
            aufschluesselung=usage,
            cost_source=herkunft,
        )
        db.commit()


async def verfassen(
    *,
    user_id: int,
    provider_id: int | None = None,
    anlass: str,
    fakten: str,
    client: httpx.AsyncClient | None = None,
) -> Mailtext | None:
    """Laesst das Modell Betreff und Text schreiben. ``None`` heisst „fester Text“.

    Wirft nie. Jeder Ausgang ausser „das Modell hat brauchbare Felder
    geliefert“ endet in ``None``, und ``None`` ist kein Fehler: die Mail geht
    dann mit dem Text hinaus, den `EmailService` ohnehin kennt. Verschickt wird
    immer — das ist die Zusage, an der dieser ganze Schritt haengt.

    Gerufen wird **beim Versand** und nicht beim Einreihen: im Arbeiter am
    Ausgangskorb, innerhalb dessen Schranke. Beim Einreihen zu verfassen hiesse,
    dass zehntausend gleichzeitig endende Auftraege zehntausend gleichzeitige
    Modellaufrufe ausloesen — und die Mail haenge wieder an einem Prozess statt
    an einer Zeile in der Datenbank.

    ``client`` ist normalerweise ``None``. Der Prozessclient aus
    `ai_run_service` waere hier falsch: dieser Aufruf laeuft am Ende in einer
    anderen Aufgabe als der Lauf, dessen Bericht er schreibt — moeglicherweise
    sogar in einem anderen Prozess, wenn die Zeile einen Neustart ueberdauert
    hat. Ein Test reicht trotzdem einen durch, damit er keinen echten aufmachen
    muss.

    ``anlass`` steht nur im Log. Er beantwortet die Frage „warum hat diese Mail
    keinen eigenen Text bekommen?“, ohne dass jemand die Fakten mitlesen muss —
    in denen steht der halbe Serverzustand.
    """
    try:
        vorbereitet = _vorbereiten(user_id, provider_id, fakten)
    except Exception as exc:  # noqa: BLE001 - ein Mailtext beendet keinen Lauf
        logger.warning(
            "KI-Mailtext nicht vorbereitet (%s): %s", anlass, type(exc).__name__
        )
        return None
    if vorbereitet is None:
        logger.info("KI-Mailtext entfaellt, fester Text geht hinaus (%s)", anlass)
        return None
    provider, api_key, messages, request_id, geschaetzt = vorbereitet

    usage = StreamUsage()
    eigener: httpx.AsyncClient | None = None
    try:
        if client is None:
            eigener = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0, read=LESEFRIST_SEKUNDEN, write=10.0, pool=5.0
                ),
                follow_redirects=False,
            )
            client = eigener
        # Ein Mailsatz aus einem festen Schema ist keine Ueberlegung. Bisher
        # ging dazu nichts hinaus, und „nichts" heisst bei einem Anbieter ohne
        # Schalter „nimm deine Vorgabe" — jede Betreibermail wurde also mit
        # Denkschritten bezahlt, die kein Mensch je zu sehen bekam.
        denken, denkstufe = await ai_reasoning.aus_fuer(
            client, provider, api_key=api_key
        )
        try:
            async for _stueck in stream_chat_completion(
                client,
                provider=provider,
                api_key=api_key,
                messages=messages,
                usage=usage,
                reasoning=denken,
                reasoning_effort=denkstufe,
                tools=[WERKZEUG],
                # Der eigentliche Zwang. Ohne diese Zeile waere das Schema eine
                # Bitte, und ein Modell, das stattdessen einen freundlichen
                # Absatz schreibt, haette die Formatierung wieder im Text.
                tool_choice={"type": "function", "function": {"name": WERKZEUG_NAME}},
            ):
                pass
        except (AiProviderRequestError, httpx.HTTPError) as exc:
            logger.info(
                "KI-Mailtext fehlgeschlagen (%s) error=%s", anlass, type(exc).__name__
            )
            _abrechnen(request_id, provider, usage, geschaetzt, gescheitert=True)
            return None
    finally:
        if eigener is not None:
            await eigener.aclose()

    _abrechnen(request_id, provider, usage, geschaetzt, gescheitert=False)

    aufruf = next(
        (ruf for ruf in usage.tool_calls if ruf.name == WERKZEUG_NAME), None
    )
    if aufruf is None:
        logger.info("KI-Mailtext ohne Werkzeugaufruf, fester Text (%s)", anlass)
        return None
    text = auswerten(aufruf.arguments)
    if text is None:
        logger.info("KI-Mailtext unbrauchbar, fester Text (%s)", anlass)
    return text
