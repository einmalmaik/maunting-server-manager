"""Die eine Stelle, an der die KI einen Menschen per E-Mail erreicht.

Es gibt inzwischen drei Anlaesse — den Bericht nach einer Guardian-Heilung, den
Bericht nach einer faelligen Aufgabe und die Testmail, mit der sich der
eingerichtete Versandweg nachpruefen laesst. Alle drei stellen dieselben zwei
Fragen, und beide sind leicht falsch zu beantworten:

**Darf ich diesem Menschen ueberhaupt schreiben?** Drei Bedingungen, und keine
davon ist ein Fehler, wenn sie nicht erfuellt ist: der Benutzer kann
Benachrichtigungen abgeschaltet haben, der Betreiber kann keinen Versandweg
eingerichtet haben, und ein Konto kann ohne Adresse bestehen. Wer das je einzeln
hinschreibt, laesst irgendwann eine Bedingung weg — und verschickt entweder
nichts oder etwas an jemanden, der es abbestellt hat.

**Wie komme ich aus synchronem Code an einen asynchronen Versand?** Der Aufruf
kommt aus `_lauf_abschliessen`, das je nach Weg bereits auf der Ereignisschleife
der Anwendung steht. `asyncio.run` ist dort ein Fehler, `await` geht nicht, weil
die Funktion kein `async def` ist. Bleibt ein eigener Thread mit eigener
Schleife — dreimal fast gleich geschrieben ist dreimal die Gelegenheit, den
Rueckgabewert zu verschlucken.

Genau der Rueckgabewert ist der Punkt: `EmailService.send_email` wirft nie und
gibt bei jedem Problem `False` zurueck. Ohne Auswertung waere die Zusage "der
Benutzer wird informiert" weder erfuellt noch nachpruefbar, und im Log stuende
nichts.

**Nicht** hier drin: wie eine Mail aussieht. Das bleibt bei `EmailService` —
dieses Modul entscheidet, *ob* und *wie* zugestellt wird, nicht *was*.

**Die zweite Frage ist inzwischen anders beantwortet.** Ein Thread je Mail war
tragbar, solange Mails einzeln entstanden. Seit es stehende Auftraege gibt,
entstehen sie in Buendeln: zehntausend Aufgaben, alle auf 18:00 gestellt, waren
zehntausend Threads mit zehntausend Ereignisschleifen — und darunter
zehntausend frische SMTP-Verbindungen, denn `aiosmtplib.send` baut je Aufruf
eine neue auf. Kein Anbieter nimmt das an. Schlimmer noch war, was danach
geschah: der Versand endete in `except Exception: return False`, und die
Nachricht existierte danach nirgends mehr.

Deshalb legt `zustellen` heute nur noch eine Zeile in `ai_mail_outbox` an —
synchron, in Millisekunden, ohne Thread. Den Versand macht ein einziger
begrenzter Arbeiter (`services/ai_mail_outbox.py`), und was scheitert, bleibt
liegen und wird noch einmal versucht.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from models import AiMailOutbox, User


logger = logging.getLogger(__name__)


def empfaenger(db: Session, user: User | None) -> str | None:
    """Die Adresse, an die geschrieben werden darf — oder ``None``.

    ``None`` ist nie ein Fehler. Jede der drei Bedingungen ist eine gueltige
    Einstellung, und jede bekommt trotzdem eine eigene Logzeile: sonst ist
    "warum kam keine Mail?" nicht zu beantworten.

    Der Zugriff auf ``user.email`` ist ein synchroner Aufruf an den
    DIS-Sidecar und faellt geschlossen aus — bei nicht erreichbarem Sidecar
    fliegt eine Ausnahme statt ``None`` zurueckzukommen. Sie wird hier gefangen:
    ein Berichtspfad, der wegen eines Sidecar-Aussetzers durchschlaegt, nimmt
    den ganzen Lauf mit.

    ``db`` wird heute nicht gebraucht und steht trotzdem in der Signatur: jede
    Erweiterung dieser Entscheidung (eine Teamadresse, eine Vertretung, ein
    Verteiler) braucht sie, und der Aufrufer hat sie ohnehin zur Hand.
    """
    from services.email_service import EmailService

    if user is None:
        return None
    if not getattr(user, "email_notifications", False):
        logger.debug("KI-Mail unterbleibt: Benutzer %s will keine", user.id)
        return None
    if not EmailService.is_configured():
        logger.info("KI-Mail nicht zustellbar: kein Versandweg eingerichtet")
        return None
    try:
        adresse = user.email
    except Exception as exc:  # noqa: BLE001 - ein Sidecar-Aussetzer beendet keinen Lauf
        logger.warning(
            "KI-Mail nicht zustellbar: Adresse nicht lesbar (%s)", type(exc).__name__
        )
        return None
    if not adresse:
        logger.debug("KI-Mail unterbleibt: Benutzer %s hat keine Adresse", user.id)
        return None
    return str(adresse)


def einreihen(
    db: Session,
    *,
    user_id: int,
    anlass: str,
    betreff: str,
    text: str,
    html: str | None = None,
) -> str | None:
    """Legt die Mail in den Ausgangskorb. Verschickt nichts.

    Der ganze Sinn dieser Funktion ist ihre Kuerze: ein `INSERT` und ein
    `COMMIT`. Was danach passiert — Verbindung aufbauen, Anbieter drosselt,
    Netz weg, Prozess startet neu — ist Sache des Arbeiters und keine Sorge des
    Aufrufers mehr.

    **Es wird hier commitet**, und das ist eine Entscheidung. Nur zu `flush`en
    hiesse, die Zusage "die Nachricht ist gespeichert" an ein Commit zu haengen,
    das irgendwo spaeter faellt oder eben nicht — genau die Unbestimmtheit, wegen
    der es diese Tabelle gibt. Der Preis ist bekannt: was in derselben Sitzung
    noch offen war, geht mit. Die Aufrufer sind Berichtspfade am Ende eines
    Laufs, die ohnehin unmittelbar danach commiten.

    **Geprueft wird hier nichts.** Weder ob der Benutzer Mails will noch ob ein
    Versandweg eingerichtet ist. Beides kann sich bis zur Zustellung aendern,
    und beides steht in `empfaenger` — einmal, an der Stelle, an der es zaehlt.

    Gibt die Kennung der Zeile zurueck, oder ``None``, wenn sie nicht angelegt
    werden konnte. Wirft nie: eine nicht eingereihte Mail beendet keinen Lauf.
    """
    betreff = str(betreff or "").strip()
    text = str(text or "").strip()
    if not betreff or not text:
        # Nachsichtig lesen, streng speichern — aber eine Mail ohne Betreff oder
        # ohne Text ist nicht unvollstaendig, sie ist keine Mail.
        logger.warning("KI-Mail nicht eingereiht (%s): Betreff oder Text fehlt", anlass)
        return None

    zeile = AiMailOutbox(
        id=str(uuid.uuid4()),
        user_id=int(user_id),
        anlass=str(anlass or "ai-mail")[:48],
        betreff=betreff[:255],
        text_body=text,
        html_body=html or None,
        status="offen",
        versuche=0,
        # Sofort faellig. Ein Versatz waere eine Verzoegerung ohne Zweck: die
        # Begrenzung macht die Schranke des Arbeiters, nicht die Uhr.
        naechster_versuch_at=datetime.now(timezone.utc),
    )
    try:
        db.add(zeile)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - ein Mailfehler beendet keinen Lauf
        db.rollback()
        logger.warning(
            "KI-Mail nicht eingereiht (%s): %s", anlass, type(exc).__name__
        )
        return None
    logger.info("KI-Mail eingereiht outbox=%s anlass=%s", zeile.id, zeile.anlass)
    return zeile.id


#: Wieviele Mails des Koroutinenwegs gleichzeitig unterwegs sein duerfen.
#: Fuenf, weil dahinter eine SMTP-Verbindung je Mail steht und kein Anbieter
#: mehr als eine Handvoll gleichzeitig zulaesst — und weil in derselben
#: Koroutine der Verfassungsschritt liegt, also auch ein Modellaufruf.
MAX_GLEICHZEITIGE_MAILS = 5

_pool: ThreadPoolExecutor | None = None
_pool_schloss = threading.Lock()


def _ARBEITER() -> ThreadPoolExecutor:
    """Der begrenzte Arbeitsstock fuer den Koroutinenweg.

    Hier stand ``threading.Thread(...).start()`` — **ein eigener Betriebssystem-
    Thread mit eigener Ereignisschleife je Mail**, ohne jede Obergrenze. Bei
    einem Panel mit vielen Kunden ist das kein theoretischer Fall: Tagesberichte
    stehen zur vollen Stunde an, und zehntausend gleichzeitig faellige Auftraege
    haetten zehntausend Threads erzeugt, jeden mit eigener Schleife und eigener
    frischer SMTP-Verbindung. Der Prozess waere vorher gestorben.

    Ein Stock mit fester Groesse dreht das um: die ueberzaehligen Mails warten in
    der Schlange statt jede einen Thread zu bekommen. Verloren geht dabei
    nichts, es dauert nur laenger — und genau so herum ist es richtig.

    Traege angelegt und nicht beim Import: ein Arbeitsstock, der in jedem
    Testlauf und in jedem Hilfsskript entsteht, kostet Threads fuer nichts.
    """
    global _pool
    if _pool is None:
        with _pool_schloss:
            if _pool is None:
                _pool = ThreadPoolExecutor(
                    max_workers=MAX_GLEICHZEITIGE_MAILS,
                    thread_name_prefix="ai-mail",
                )
    return _pool


def zustellen(
    bauen: Callable[[], Awaitable[bool]] | None = None,
    *,
    name: str,
    db: Session | None = None,
    user_id: int | None = None,
    betreff: str | None = None,
    text: str | None = None,
    html: str | None = None,
) -> None:
    """Uebergibt eine Mail an den Versand. Wirft nie.

    Es gibt zwei Wege hinein, und der Unterschied ist nicht Geschmack:

    **Der Korbweg** — ``db``, ``user_id``, ``betreff`` und ``text`` angegeben.
    Dann entsteht eine Zeile in `ai_mail_outbox` und der Arbeiter verschickt
    sie. Das ist der Weg fuer alles, was in Buendeln anfaellt, also fuer jeden
    KI-Bericht. ``name`` wird zum ``anlass`` der Zeile.

    **Der Thread-Weg** — nur ``bauen``, wie frueher. Er bleibt erhalten, weil
    beim Umbau nicht alle Aufrufer gleichzeitig umgestellt werden konnten und
    ein stillschweigender Bruch der Signatur schlimmer waere als ein alter Pfad
    daneben. Er hat alle Nachteile, wegen derer es den Korb gibt: keine
    Obergrenze, kein zweiter Versuch, und was scheitert, ist weg. Wer eine neue
    Mailart baut, nimmt den Korbweg.

    ``bauen`` ist im Thread-Weg eine Fabrik und keine fertige Koroutine: die
    waere an eine Ereignisschleife gebunden, die es hier nicht gibt. Der Thread
    bekommt einen Namen — das kostet nichts und ist der Unterschied zwischen
    einem lesbaren Stacktrace und "Thread-7".
    """
    if db is not None and user_id is not None and betreff is not None and text is not None:
        einreihen(
            db,
            user_id=user_id,
            anlass=name,
            betreff=betreff,
            text=text,
            html=html,
        )
        return

    if bauen is None:
        logger.warning(
            "KI-Mail nicht zustellbar (%s): weder Korbangaben noch Koroutine", name
        )
        return

    def _lauf() -> None:
        schleife = asyncio.new_event_loop()
        try:
            if not schleife.run_until_complete(bauen()):
                logger.warning("KI-Mail konnte nicht zugestellt werden (%s)", name)
        except Exception as exc:  # noqa: BLE001 - ein Mailfehler beendet keinen Lauf
            logger.warning("KI-Mail fehlgeschlagen (%s): %s", name, type(exc).__name__)
        finally:
            schleife.close()

    _ARBEITER().submit(_lauf)
