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
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from models import User


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


def zustellen(bauen: Callable[[], Awaitable[bool]], *, name: str) -> None:
    """Schickt die Mail in einem eigenen Thread mit eigener Ereignisschleife.

    ``bauen`` liefert die noch nicht gestartete Koroutine — als Fabrik und nicht
    als fertige Koroutine, weil eine bereits erzeugte Koroutine an eine Schleife
    gebunden waere, die es hier nicht gibt.

    Der Thread bekommt einen Namen. Das kostet nichts und ist der Unterschied
    zwischen einem lesbaren Stacktrace und "Thread-7".

    Wirft nie. Ein Mailfehler ist ein Mailfehler und beendet keinen KI-Lauf.
    """
    def _lauf() -> None:
        schleife = asyncio.new_event_loop()
        try:
            if not schleife.run_until_complete(bauen()):
                logger.warning("KI-Mail konnte nicht zugestellt werden (%s)", name)
        except Exception as exc:  # noqa: BLE001 - ein Mailfehler beendet keinen Lauf
            logger.warning("KI-Mail fehlgeschlagen (%s): %s", name, type(exc).__name__)
        finally:
            schleife.close()

    threading.Thread(target=_lauf, daemon=True, name=name).start()
