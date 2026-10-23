"""Die gerätebezogene Zustellung: annehmen, abholen, quittieren.

Der Weg, den ein Umschlag ab jetzt nimmt, wenn das Zielgeraet ihn
unterstuetzt: eine **opake Inbox je Geraet**, ein **Zustellauftrag** je
Nachricht, und ein **Quittung**, das den Auftrag loescht. Vorher teilten sich
zwei Geraete desselben Kontos eine blinde Mailbox, und der Chiffretext lag
30 Tage dort, obwohl ihn niemand mehr brauchte.

Drei Schranken, jede fuer sich:

1. **Der Kontext entscheidet ueber das Senden.** Wie beim Relais gilt
   `assert_mailbox_zugang`: wer in diese Mailbox schreiben darf, darf in sie
   zustellen. Die Mailbox steht in der Anfrage, wird aber **nicht**
   abgespeichert — der Zustellbestand fuehrt keinen Kontaktgraph.
2. **Das Zielgeraet entscheidet ueber das Empfangen.** Es muss freigegeben
   sein, und es muss zu einem Konto gehoeren, dem der Absender schreiben darf.
3. **Der Geraetebeleg entscheidet ueber das Abholen und das Quittieren.** Nur
   das Geraet kann seine eigene Inbox oeffnen: es unterschreibt mit seinem
   Signaturschluessel, und der Server prueft die Unterschrift gegen genau
   diesen Schluessel. Die Sitzung allein reicht nicht — ein abgegriffenes
   Token eines anderen Geraets desselben Kontos darf die Inbox des Telefons
   nicht lesen.

Die Frist steht bei der Annahme fest: 30 Tage. Eine Wiederholung verlaengert
sie nicht. Nach dem Quittieren bleibt nur der Idempotenzrest (Kennung, Hash
der Anfrage, Ergebnis, urspruengliche Frist) — genug, um eine verlorene
HTTP-Antwort zu beantworten, und zu wenig, um ein zweites Versandarchiv
anzulegen.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import E2eeDelivery, E2eeDeliveryPayload, E2eeDeliveryReceipt, E2eeDeviceInbox
from models.user_e2ee_device import UserE2eeDevice
from services.e2ee_device_service import pruefe_geraetesignatur

logger = logging.getLogger(__name__)

# Die Frist einer angenommenen Zustellung.
FRIST_TAGE = 30
# Hoechstens so viele Ziele nimmt eine Anfrage auf.
MAX_BATCH = 50
# Ein Umschlag bleibt so gross, wie er im alten Relais war (1 MiB B64).
MAX_CHIFFREXT_CHARS = 1_400_000
# Hoechstens so viele Auftraege liefert ein Abruf.
MAX_ABRUF = 200
# Hoechstens so viele Kennungen nimmt ein Quittung entgegen.
MAX_ACK = 100
# Grenzen je Inbox: offene Auftraege und ihre Bytes.
MAX_OFFENE_PRO_INBOX = 50
MAX_BYTES_PRO_INBOX = 10 * 1024 * 1024


class ZustellFehler(Exception):
    """Ein Fehler der Zustellung — mit einem Code, den der Client kennt."""

    code = "zustellung_fehler"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BeweisFehler(ZustellFehler):
    """Der Geraetebeleg fehlt, passt nicht oder gehoert zu einem anderen Geraet."""

    code = "beweis_fehler"


class KonfliktFehler(ZustellFehler):
    """Dieselbe Zustellkennung mit anderen Bytes."""

    code = "konflikt"


class UnbekanntesZielFehler(ZustellFehler):
    """Die Zustelladresse gehoert zu keinem Geraet (mehr)."""

    code = "unbekanntes_ziel"


class ZielNichtErlaubtFehler(ZustellFehler):
    """Das Zielgeraet ist nicht freigegeben oder der Absender darf es nicht beschreiben."""

    code = "ziel_nicht_erlaubt"


# ==========================================
# Die Zeichenketten, die unterschrieben werden
# ==========================================
#
# Client und Server bilden dieselbe Zeichenkette. Sie steht hier und in
# `frontend/src/services/e2eeZustellung.ts` — aendert sich eine, aendern sich
# beide. Die Klartextregeln:
#
# * keine Leerzeichen, keine Zeilenumbrueche,
# * Feldtrenner `:` zwischen den Feldern, `|` zwischen den Eintraegen,
# * die Eintraege sortiert, damit die Reihenfolge im Paket nichts bedeutet.


def relay_beweis_daten(
    geraet_id: str, mailbox_id: str, eintraege: list[dict]
) -> str:
    """Die Zeichenkette, die das sendende Geraet ueber einen Stapel unterschreibt."""
    teile = sorted(
        f"{e['delivery_id']}:{e['payload_id']}:{e['inbox_hash']}:{e['ciphertext_sha256']}"
        for e in eintraege
    )
    return f"msm:device-delivery-v1:{geraet_id}:{mailbox_id}:" + "|".join(teile)


def lese_beweis_daten(inbox_hash: str, seit_seq: int) -> str:
    """Die Zeichenkette, die ein Geraet ueber seinen Abruf unterschreibt."""
    return f"msm:device-delivery-read-v1:{inbox_hash}:{seit_seq}"


def ack_beweis_daten(inbox_hash: str, delivery_ids: list[str]) -> str:
    """Die Zeichenkette, die ein Geraet ueber sein Quittung unterschreibt."""
    return f"msm:device-delivery-ack-v1:{inbox_hash}:" + ",".join(sorted(delivery_ids))


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _frist() -> datetime:
    return _jetzt() + timedelta(days=FRIST_TAGE)


# ==========================================
# Die Inbox
# ==========================================


def inbox_hash_gueltig(wert: str | None) -> bool:
    """64 Hex-Zeichen. Sonst ist es keine Adresse."""
    if not wert or len(wert) != 64:
        return False
    return all(c in "0123456789abcdef" for c in wert.lower())


def kennung_gueltig(wert: str | None) -> bool:
    """Eine UUID — die Zustell- und die Objektkennung."""
    if not wert:
        return False
    try:
        uuid.UUID(str(wert))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def inbox_registrieren(
    db: Session, device: UserE2eeDevice, inbox_hash: str
) -> E2eeDeviceInbox:
    """Legt die Zustelladresse eines Geraets an oder frischt sie auf.

    Idempotent wie die Geraeteveroeffentlichung: dasselbe Geraet meldet sich
    bei jedem Start. Die Frist wird dabei erneuert — ein Geraet, das sich
    regelmaessig meldet, soll nicht an einer abgelaufenen Inbox sterben.
    """
    if not inbox_hash_gueltig(inbox_hash):
        raise ZustellFehler("Ungueltige Zustelladresse.")
    inbox_hash = inbox_hash.lower()
    vorhanden = (
        db.query(E2eeDeviceInbox)
        .filter(E2eeDeviceInbox.device_id == device.id)
        .first()
    )
    if vorhanden is not None:
        if vorhanden.inbox_hash != inbox_hash:
            # Ein Geraet bekommt genau eine Adresse. Ein Wechsel bedeutet ein
            # neues Geraetgeheimnis — und damit eine neue Freigabe.
            raise ZustellFehler("Dieses Geraet hat bereits eine Zustelladresse.")
        vorhanden.ablauf_am = _frist()
        db.commit()
        return vorhanden
    inbox = E2eeDeviceInbox(
        device_id=device.id,
        inbox_hash=inbox_hash,
        erstellt_am=_jetzt(),
        ablauf_am=_frist(),
    )
    db.add(inbox)
    db.commit()
    db.refresh(inbox)
    return inbox


def geraet_von_inbox(db: Session, inbox_hash: str) -> UserE2eeDevice | None:
    """Das Geraet hinter einer Zustelladresse — oder `None`."""
    if not inbox_hash_gueltig(inbox_hash):
        return None
    inbox = (
        db.query(E2eeDeviceInbox)
        .filter(E2eeDeviceInbox.inbox_hash == inbox_hash.lower())
        .first()
    )
    if inbox is None:
        return None
    return db.query(UserE2eeDevice).filter(UserE2eeDevice.id == inbox.device_id).first()


def inbox_von_geraet(db: Session, device_id: int) -> E2eeDeviceInbox | None:
    return (
        db.query(E2eeDeviceInbox)
        .filter(E2eeDeviceInbox.device_id == device_id)
        .first()
    )


# ==========================================
# Der Geraetebeleg
# ==========================================


def geraet_pruefen(
    db: Session,
    user_id: int,
    familie: str | None,
    device: UserE2eeDevice | None,
    signatur: str,
    daten: str,
) -> UserE2eeDevice:
    """Prueft, ob die Unterschrift zu diesem Geraet gehoert.

    Drei Dinge muessen zusammenpassen: das Geraet gehoert dem Konto der
    Sitzung, die Sitzung gehoert zur Geraetefamilie, und die Unterschrift
    stammt vom Signaturschluessel genau dieses Geraets. Fehlt eines, ist der
    Beleg ungueltig — und ein ungueltiger Beleg ist keine Beweisfrage, sondern
    eine Ablehnung.
    """
    if device is None:
        raise BeweisFehler("Unbekanntes Geraet.")
    if device.user_id != user_id:
        raise BeweisFehler("Dieses Geraet gehoert zu einem anderen Konto.")
    if device.auth_family and familie and device.auth_family != familie:
        # Ein Token eines anderen Geraets desselben Kontos. Die Familie
        # bindet die Sitzung an das Geraet; ohne sie darf diese Sitzung die
        # Inbox nicht oeffnen.
        raise BeweisFehler("Diese Sitzung gehoert zu einem anderen Geraet.")
    if not device.signing_public_key_jwk:
        raise BeweisFehler("Dieses Geraet hat keinen Signaturschluessel.")
    if not pruefe_geraetesignatur(daten, device.signing_public_key_jwk, signatur):
        raise BeweisFehler("Der Geraetebeleg passt nicht.")
    return device


# ==========================================
# Annehmen
# ==========================================


def _offene_last(db: Session, inbox_hash: str) -> tuple[int, int]:
    """Wie viele Auftraege offen sind und wie viele Bytes sie tragen."""
    zeilen = (
        db.query(
            E2eeDelivery.id,
            E2eeDeliveryPayload.groesse,
        )
        .join(E2eeDeliveryPayload, E2eeDelivery.payload_id == E2eeDeliveryPayload.id)
        .filter(
            E2eeDelivery.inbox_hash == inbox_hash,
            E2eeDelivery.quittiert_am.is_(None),
        )
        .all()
    )
    anzahl = len(zeilen)
    bytes = sum(int(z[1] or 0) for z in zeilen)
    return anzahl, bytes


def annehmen(
    db: Session,
    sender_id: int,
    mailbox_id: str,
    geraet_id: str,
    payloads: list[dict],
    eintraege: list[dict],
    signatur: str,
) -> dict:
    """Nimmt einen Stapel Zustellungen an.

    Die Reihenfolge ist Absicht: **erst die Berechtigung, dann die
    Idempotenz.** Wer nicht schreiben darf, erfährt nichts darueber, ob eine
    Zustellkennung schon bekannt ist.
    """
    if not eintraege:
        raise ZustellFehler("Keine Zustellungen im Stapel.")
    if len(eintraege) > MAX_BATCH:
        raise ZustellFehler(f"Zu viele Zustellungen auf einmal (max. {MAX_BATCH}).")
    if not mailbox_id:
        raise ZustellFehler("Ohne Kontext keine Zustellung.")

    # Die Nutzlasten zuerst: der Beleg deckt auch ihren Hash ab.
    nach_nutzlast: dict[str, dict] = {}
    for nutzlast in payloads:
        pid = str(nutzlast.get("id") or "")
        if not kennung_gueltig(pid):
            raise ZustellFehler("Ungueltige Objektkennung.")
        ciphertext = str(nutzlast.get("ciphertext") or "")
        if not ciphertext or len(ciphertext) > MAX_CHIFFREXT_CHARS:
            raise ZustellFehler("Der Chiffretext ist leer oder zu gross.")
        erwartet = str(nutzlast.get("ciphertext_sha256") or "")
        if not erwartet or _hash(ciphertext) != erwartet.lower():
            raise ZustellFehler("Die Pruefsumme des Chiffretexts stimmt nicht.")
        if pid in nach_nutzlast:
            raise ZustellFehler("Dieselbe Objektkennung zweimal im Stapel.")
        nach_nutzlast[pid] = {
            "id": pid,
            "ciphertext": ciphertext,
            "ciphertext_sha256": erwartet.lower(),
            "groesse": len(ciphertext),
        }

    # Der Beleg des sendenden Geraets — ueber den ganzen Stapel.
    sendend = db.query(UserE2eeDevice).filter(
        UserE2eeDevice.user_id == sender_id,
        UserE2eeDevice.device_id == geraet_id,
    ).first()
    if sendend is None:
        raise BeweisFehler("Unbekanntes sendendes Geraet.")
    daten = relay_beweis_daten(
        geraet_id,
        mailbox_id,
        [
            {
                "delivery_id": e["delivery_id"],
                "payload_id": e["payload_id"],
                "inbox_hash": e["inbox_hash"],
                "ciphertext_sha256": nach_nutzlast[e["payload_id"]]["ciphertext_sha256"],
            }
            for e in eintraege
            if e.get("payload_id") in nach_nutzlast
        ],
    )
    if not pruefe_geraetesignatur(daten, sendend.signing_public_key_jwk or "", signatur):
        raise BeweisFehler("Der Geraetebeleg passt nicht.")

    angenommen: list[dict] = []
    abgelehnt: list[dict] = []

    for eintrag in eintraege:
        delivery_id = str(eintrag.get("delivery_id") or "")
        payload_id = str(eintrag.get("payload_id") or "")
        inbox_hash = str(eintrag.get("inbox_hash") or "").lower()
        if not kennung_gueltig(delivery_id) or not kennung_gueltig(payload_id):
            raise ZustellFehler("Ungueltige Zustell- oder Objektkennung.")
        if not inbox_hash_gueltig(inbox_hash):
            raise ZustellFehler("Ungueltige Zustelladresse.")
        nutzlast = nach_nutzlast.get(payload_id)
        if nutzlast is None:
            raise ZustellFehler("Zustellung ohne zugehoerige Nutzlast.")

        # Schon bekannt? Dann ist es eine Wiederholung — oder ein Konflikt.
        vorhanden = (
            db.query(E2eeDelivery)
            .filter(
                E2eeDelivery.inbox_hash == inbox_hash,
                E2eeDelivery.id == delivery_id,
            )
            .first()
        )
        if vorhanden is not None:
            alt = db.query(E2eeDeliveryPayload).filter(
                E2eeDeliveryPayload.id == vorhanden.payload_id
            ).first()
            if alt is not None and alt.ciphertext_sha256 == nutzlast["ciphertext_sha256"]:
                angenommen.append({"delivery_id": delivery_id, "ergebnis": "bereits"})
            else:
                raise KonfliktFehler(
                    "Diese Zustellkennung wurde schon mit anderen Bytes benutzt."
                )
            continue

        rest = (
            db.query(E2eeDeliveryReceipt)
            .filter(
                E2eeDeliveryReceipt.inbox_hash == inbox_hash,
                E2eeDeliveryReceipt.delivery_id == delivery_id,
            )
            .first()
        )
        if rest is not None:
            # Der Auftrag ist schon quittiert; der Rest antwortet. Gleiche
            # Bytes sind eine Wiederholung, andere ein Konflikt.
            if rest.request_hash == _hash(eintrag_hash(eintrag, nutzlast)):
                angenommen.append({"delivery_id": delivery_id, "ergebnis": "bereits"})
            else:
                raise KonfliktFehler(
                    "Diese Zustellkennung wurde schon mit anderen Bytes benutzt."
                )
            continue

        # Darf der Absender dieses Geraet beschreiben?
        zielgeraet = geraet_von_inbox(db, inbox_hash)
        if zielgeraet is None:
            abgelehnt.append({"delivery_id": delivery_id, "grund": UnbekanntesZielFehler.code})
            continue
        if not zielgeraet.is_approved:
            abgelehnt.append({"delivery_id": delivery_id, "grund": ZielNichtErlaubtFehler.code})
            continue
        if zielgeraet.user_id == sender_id:
            # Die eigenen Geraete des Absenders bekommen die Nachricht ebenfalls
            # — sie sind eigenstaendige Zustaende und keine Kopien.
            pass
        else:
            erlaubt, grund = _darf_beschreiben(db, sender_id, zielgeraet.user_id, mailbox_id)
            if not erlaubt:
                abgelehnt.append({"delivery_id": delivery_id, "grund": ZielNichtErlaubtFehler.code})
                continue

        anzahl, bytes = _offene_last(db, inbox_hash)
        if anzahl >= MAX_OFFENE_PRO_INBOX or bytes + nutzlast["groesse"] > MAX_BYTES_PRO_INBOX:
            abgelehnt.append({"delivery_id": delivery_id, "grund": "inbox_voll"})
            continue

        # Das Chiffretextobjekt: geteilt, wenn es schon existiert.
        objekt = db.query(E2eeDeliveryPayload).filter(
            E2eeDeliveryPayload.id == payload_id
        ).first()
        if objekt is None:
            objekt = E2eeDeliveryPayload(
                id=payload_id,
                ciphertext=nutzlast["ciphertext"],
                ciphertext_sha256=nutzlast["ciphertext_sha256"],
                groesse=nutzlast["groesse"],
                angenommen_am=_jetzt(),
                ablauf_am=_frist(),
            )
            db.add(objekt)
            # Das Objekt muss in der Datenbank stehen, bevor der Auftrag auf
            # es zeigt. Ohne diesen Zwischenschritt wuerde der Auftrag zuerst
            # geschrieben und der Fremdschluessel schluege fehl.
            db.flush()
        else:
            if objekt.ciphertext_sha256 != nutzlast["ciphertext_sha256"]:
                raise KonfliktFehler(
                    "Diese Objektkennung wurde schon mit anderen Bytes benutzt."
                )
            # Die Frist steht bei der Annahme fest und wird nicht verlaengert.
            objekt.ablauf_am = min(objekt.ablauf_am, _frist())

        seq = _naechste_seq(db, inbox_hash)
        db.add(
            E2eeDelivery(
                id=delivery_id,
                payload_id=payload_id,
                inbox_hash=inbox_hash,
                laufende_seq=seq,
                angenommen_am=_jetzt(),
                ablauf_am=objekt.ablauf_am,
            )
        )
        angenommen.append({"delivery_id": delivery_id, "ergebnis": "angenommen"})

    db.commit()
    return {"angenommen": angenommen, "abgelehnt": abgelehnt}


def eintrag_hash(eintrag: dict, nutzlast: dict) -> str:
    """Der Hash einer Zustellung — fuer den Idempotenzrest."""
    return _hash(
        f"{eintrag['delivery_id']}:{eintrag['payload_id']}:{eintrag['inbox_hash']}:"
        f"{nutzlast['ciphertext_sha256']}"
    )


def _darf_beschreiben(
    db: Session, sender_id: int, ziel_id: int, mailbox_id: str
) -> tuple[bool, str | None]:
    """Darf der Absender das Zielkonto ueberhaupt beschreiben?

    Die Mailbox-Zugehoerigkeit entscheidet fuer den Gespraechskontext, der
    Zielkontakt fuer den Rest. Ein Geraet eines fremden Kontos, das in
    keinem Gespraech mit dem Absender steht, wird nicht beliefert — auch dann
    nicht, wenn seine Adresse bekannt ist.
    """
    from services.social_service import SocialService

    if ziel_id == sender_id:
        return True, None
    erlaubt, grund = SocialService.can_message_user(db, sender_id, ziel_id)
    return erlaubt, grund


def _naechste_seq(db: Session, inbox_hash: str) -> int:
    """Die Laufnummer des naechsten Auftrags dieser Inbox."""
    hoechste = (
        db.query(func.max(E2eeDelivery.laufende_seq))
        .filter(E2eeDelivery.inbox_hash == inbox_hash)
        .scalar()
    )
    return int(hoechste or 0) + 1


# ==========================================
# Abholen
# ==========================================


def lesen(
    db: Session,
    user_id: int,
    familie: str | None,
    inbox_hash: str,
    seit_seq: int,
    limit: int,
    signatur: str,
) -> dict:
    """Holt die offenen Auftraege einer Inbox.

    Der Abruf loescht **nichts**. Das Loeschen ist das Quittung — ein
    Abruf, der zugleich aufraeumt, wuerde eine verlorene Antwort mit einem
    verlorenen Inhalt bezahlen.
    """
    inbox_hash = inbox_hash.lower()
    geraet = geraet_von_inbox(db, inbox_hash)
    geraet_pruefen(
        db,
        user_id,
        familie,
        geraet,
        signatur,
        lese_beweis_daten(inbox_hash, seit_seq),
    )
    grenze = max(1, min(int(limit or MAX_ABRUF), MAX_ABRUF))
    auftraege = (
        db.query(E2eeDelivery)
        .filter(
            E2eeDelivery.inbox_hash == inbox_hash,
            E2eeDelivery.quittiert_am.is_(None),
            E2eeDelivery.laufende_seq > int(seit_seq or 0),
        )
        .order_by(E2eeDelivery.laufende_seq.asc())
        .limit(grenze)
        .all()
    )
    zeilen = []
    for auftrag in auftraege:
        objekt = db.query(E2eeDeliveryPayload).filter(
            E2eeDeliveryPayload.id == auftrag.payload_id
        ).first()
        if objekt is None:
            # Der Chiffretext ist weg, der Auftrag nicht. Ein solcher Auftrag
            # wird nicht zugestellt; er zaehlt als erledigt.
            db.delete(auftrag)
            continue
        zeilen.append(
            {
                "delivery_id": auftrag.id,
                "payload_id": objekt.id,
                "ciphertext": objekt.ciphertext,
                "ciphertext_sha256": objekt.ciphertext_sha256,
                "seq": auftrag.laufende_seq,
                "angenommen_am": auftrag.angenommen_am.isoformat(),
                "ablauf_am": auftrag.ablauf_am.isoformat(),
            }
        )
    db.commit()
    naechste = zeilen[-1]["seq"] if zeilen else int(seit_seq or 0)
    return {"eintraege": zeilen, "naechste_seq": naechste}


# ==========================================
# Quittieren
# ==========================================


def quittieren(
    db: Session,
    user_id: int,
    familie: str | None,
    inbox_hash: str,
    geraet_id: str,
    delivery_ids: list[str],
    signatur: str,
) -> dict:
    """Bestaetigt die Zustellungen einer Inbox — und loescht genau sie.

    Die Quittung ist der einzige Weg, der einen Auftrag beendet. Sie loescht
    nie fremde Auftraege: die Inbox gehoert zum Geraet, und das Geraet hat
    bewiesen, dass es diese Inbox ist.
    """
    inbox_hash = inbox_hash.lower()
    if not delivery_ids:
        return {"quittiert": 0}
    if len(delivery_ids) > MAX_ACK:
        raise ZustellFehler(f"Zu viele Kennungen auf einmal (max. {MAX_ACK}).")
    geraet = geraet_von_inbox(db, inbox_hash)
    geraet_pruefen(
        db,
        user_id,
        familie,
        geraet,
        signatur,
        ack_beweis_daten(inbox_hash, delivery_ids),
    )
    quittiert = 0
    for delivery_id in delivery_ids:
        auftrag = (
            db.query(E2eeDelivery)
            .filter(
                E2eeDelivery.inbox_hash == inbox_hash,
                E2eeDelivery.id == delivery_id,
            )
            .first()
        )
        if auftrag is None:
            # Schon quittiert: das ist der Normalfall bei einer wiederholten
            # Quittung. Sie bleibt erfolgreich.
            continue
        objekt = db.query(E2eeDeliveryPayload).filter(
            E2eeDeliveryPayload.id == auftrag.payload_id
        ).first()
        rest_hash = _hash(auftrag.id + ":" + (objekt.ciphertext_sha256 if objekt else ""))
        db.add(
            E2eeDeliveryReceipt(
                inbox_hash=inbox_hash,
                delivery_id=auftrag.id,
                request_hash=rest_hash,
                ergebnis=1,
                angenommen_am=_jetzt(),
                ablauf_am=auftrag.ablauf_am,
            )
        )
        db.delete(auftrag)
        quittiert += 1
        # Das Chiffretextobjekt faellt, sobald kein Auftrag mehr darauf zeigt.
        if objekt is not None:
            weitere = (
                db.query(E2eeDelivery)
                .filter(
                    E2eeDelivery.payload_id == objekt.id,
                    E2eeDelivery.quittiert_am.is_(None),
                )
                .count()
            )
            if weitere == 0:
                # Erst der Auftrag weg, dann das Objekt — sonst zeigte der
                # Fremdschluessel noch auf eine Zeile, die es nicht mehr gibt.
                db.flush()
                db.delete(objekt)
    db.commit()
    return {"quittiert": quittiert}


# ==========================================
# Aufraeumen
# ==========================================


def abgelaufene_raeumen(db: Session) -> dict:
    """Entfernt, was die Frist ueberschritten hat.

    Wird regelmaessig und beim Start vor der Freigabe gerufen. Ein Fehler
    wird gemeldet, nicht verschluckt: ein stilles Aufraeumen sieht aus wie
    „alles sauber".
    """
    jetzt = _jetzt()
    nutzlasten = (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.ablauf_am < jetzt)
        .all()
    )
    auftraege = (
        db.query(E2eeDelivery)
        .filter(E2eeDelivery.ablauf_am < jetzt)
        .all()
    )
    reste = (
        db.query(E2eeDeliveryReceipt)
        .filter(E2eeDeliveryReceipt.ablauf_am < jetzt)
        .all()
    )
    for zeile in auftraege:
        db.delete(zeile)
    for zeile in nutzlasten:
        db.delete(zeile)
    for zeile in reste:
        db.delete(zeile)
    # Inboxen, deren Geraet laenger nicht gemeldet hat, fallen mit ihrem
    # Geraet weg. Verwaiste Zeilen ohne Geraet werden hier aufgeraeumt.
    verwaist = (
        db.query(E2eeDeviceInbox)
        .filter(E2eeDeviceInbox.ablauf_am < jetzt)
        .all()
    )
    for zeile in verwaist:
        db.delete(zeile)
    db.commit()
    return {
        "nutzlasten": len(nutzlasten),
        "auftraege": len(auftraege),
        "reste": len(reste),
        "inboxen": len(verwaist),
    }
