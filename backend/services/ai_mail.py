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
die Funktion kein `async def` ist. Die Antwort war lange ein eigener Thread mit
eigener Schleife je Mail. Sie war falsch: ein Thread je Mail war tragbar,
solange Mails einzeln entstanden, aber seit es stehende Aufträge gibt, entstehen
sie in Bündeln. Zehntausend Aufgaben, alle auf 18:00 gestellt, waren zehntausend
Threads mit zehntausend Ereignisschleifen — und darunter zehntausend frische
SMTP-Verbindungen, denn `aiosmtplib.send` baut je Aufruf eine neue auf. Kein
Anbieter nimmt das an. Schlimmer noch war, was danach geschah: der Versand endete
in `except Exception: return False`, und die Nachricht existierte danach nirgends
mehr.

Deshalb legt `zustellen` nur noch eine Zeile in `ai_mail_outbox` an — synchron,
in Millisekunden, ohne Thread. Den Versand macht ein einziger begrenzter
Arbeiter (`services/ai_mail_outbox.py`), und was scheitert, bleibt liegen und
wird noch einmal versucht. Der Thread-Weg steht nicht mehr daneben: ein zweiter,
schlechterer Weg, den niemand mehr fährt, wird irgendwann versehentlich wieder
gefahren.

**Nicht** hier drin: wie eine Mail aussieht. Das bleibt bei `EmailService` —
dieses Modul entscheidet, *ob* und *wie* zugestellt wird, nicht *was*.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

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
    fakten: str | None = None,
    rahmen: dict | None = None,
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

    ``betreff``, ``text`` und ``html`` sind der **Rueckfall**: die Mail, wie sie
    hinausgeht, wenn niemand mehr etwas dazutut. ``fakten`` und ``rahmen`` sind
    das Material fuer die schoenere Fassung — der Arbeiter laesst daraus
    `ai_mail_text.verfassen` schreiben und rendert ueber den Rahmen neu. Fehlt
    eines von beiden, bleibt es beim Rueckfall, und das ist kein Fehler.

    **Warum nicht hier verfasst wird**, obwohl die Angaben hier vorliegen:
    zehntausend gleichzeitig endende Auftraege waeren zehntausend gleichzeitige
    Modellaufrufe. Im Arbeiter liegt derselbe Aufruf innerhalb der Schranke, die
    es dort ohnehin gibt — und er ueberlebt einen Neustart, weil die Angaben in
    der Datenbank stehen und nicht in einem Thread.

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

    rahmen_json: str | None = None
    if rahmen:
        try:
            # `ensure_ascii=False`: der Rahmen traegt deutschen Text mit echten
            # Umlauten, und ein Feld voller `ä` waere in der Datenbank
            # dreimal so gross und beim Nachsehen unlesbar.
            rahmen_json = json.dumps(rahmen, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            # Ein nicht serialisierbarer Rahmen kostet die schoene Fassung, nie
            # die Mail: ohne Rahmen faellt der Arbeiter auf den festen Text
            # zurueck, der hier ohnehin schon fertig danebensteht.
            logger.warning(
                "KI-Mail ohne Rahmen eingereiht (%s): %s", anlass, type(exc).__name__
            )
            rahmen_json = None

    zeile = AiMailOutbox(
        id=str(uuid.uuid4()),
        user_id=int(user_id),
        anlass=str(anlass or "ai-mail")[:48],
        betreff=betreff[:255],
        text_body=text,
        html_body=html or None,
        fakten=(str(fakten).strip() or None) if fakten else None,
        rahmen_json=rahmen_json,
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


def zustellen(
    *,
    name: str,
    db: Session | None = None,
    user_id: int | None = None,
    betreff: str | None = None,
    text: str | None = None,
    html: str | None = None,
    fakten: str | None = None,
    rahmen: dict | None = None,
) -> None:
    """Übergibt eine Mail an den Ausgangskorb. Wirft nie.

    Es gibt genau einen Weg hinein: ``db``, ``user_id``, ``betreff`` und
    ``text`` angegeben. Dann entsteht eine Zeile in `ai_mail_outbox` und der
    Arbeiter verschickt sie. ``name`` wird zum ``anlass`` der Zeile. ``fakten``
    und ``rahmen`` sind optional: liegen sie an, lässt der Arbeiter den Text
    vom Modell schreiben, statt den mitgegebenen zu nehmen.

    Daneben stand bis vor kurzem ein zweiter Weg: eine Koroutinenfabrik, die in
    einem eigenen Thread lief. Er hatte alle Nachteile, wegen derer es den Korb
    gibt — keine Obergrenze, kein zweiter Versuch, und was scheiterte, war weg.
    Aufrufer hatte er keine mehr, also ist er fort. Wer ihn versehentlich ruft,
    bekommt sofort einen ``TypeError`` und damit die deutlichste aller
    Rückmeldungen; ein stiller Verlust ist das gerade nicht.

    **Fehlt eine der vier Korbangaben**, wird geloggt und zurückgekehrt statt
    geworfen. Das ist Absicht: der Aufrufer ist ein Berichtspfad am Ende eines
    KI-Laufs, und eine nicht zugestellte Mail darf diesen Lauf nicht mitnehmen.
    """
    if db is None or user_id is None or betreff is None or text is None:
        logger.warning("KI-Mail nicht zustellbar (%s): Korbangaben fehlen", name)
        return

    einreihen(
        db,
        user_id=user_id,
        anlass=name,
        betreff=betreff,
        text=text,
        html=html,
        fakten=fakten,
        rahmen=rahmen,
    )
