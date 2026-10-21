"""Die veroeffentlichten E2EE-Schluessel der Geraete eines Kontos.

Ein Verzeichnis, mehr nicht: Kennung rein, oeffentlicher Schluessel rein,
Liste raus. Der Server erzeugt hier **kein** Schluesselmaterial und bewahrt
keines auf, das ein Geheimnis waere — genau das war der Fehler davor, den
`20260917_01_e2ee_geraete` beseitigt.

Zwei Schranken tragen die Sicherheit:

* Ein Geraet schreibt ausschliesslich seinen eigenen Eintrag. Die
  Benutzerkennung kommt aus der Sitzung, nie aus dem Rumpf der Anfrage.
* Der Schluessel muss ein RSA-OAEP-Public-Key sein. `validate_rsa_public_key_jwk`
  weist private Bestandteile, Signaturschluessel und zu kurze Moduln ab — ein
  Client, der aus Versehen seinen privaten Teil hochlaedt, wird hier gestoppt
  statt stillschweigend gespeichert.

Seit 09/2026 steht daneben ein zweiter oeffentlicher Schluessel: ECDSA P-256,
der nichts verschluesselt und nur den Absender einer Gruppennachricht
beglaubigt. Warum es ihn braucht, steht im Kopf von `models/user_e2ee_device.py`.
Beide Pruefungen sind getrennt und lassen einander nicht durch.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import User, UserE2eeDevice

# Deckel je Konto. Ein Absender faechert seine Nachricht je Eintrag auf, also
# kostet jedes Geraet alle Gegenstellen etwas. Zehn ist grosszuegig fuer
# Browser, Desktop und Telefon und klein genug, dass niemand die Liste als
# Verstaerker missbraucht.
MAX_GERAETE = 10
MAX_BEZEICHNUNG = 64
MAX_KENNUNG = 64

# Der Name, den ein Browser-Login ohne eigene Bezeichnung bekommt.
#
# Wer sich im Panel anmeldet, tippt keinen Geraetenamen ein, und eine
# Kopplungseinladung gibt es dafuer auch nicht. Bis 10/2026 blieb `label`
# deshalb leer, und das Web-Frontend stand auf jedem anderen Geraet als
# „Unbenanntes Geraet" — nicht von einem Menschen gewaehlt, sondern die
# Auskunft, dass niemand einen Namen vergeben hat. Der Name ist Produkt, kein
# Satz: er steht in der Datenbank und wird auch in der englischen Oberflaeche
# so angezeigt (dieselbe Zeichenkette in `geraeteBezeichnung.ts`).
STANDARD_BEZEICHNUNG = "Web-Frontend"


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def kennung_gueltig(device_id: str) -> bool:
    """Bedeutungsfreie Zufallskennung, nichts weiter.

    Sie steht im Klartext in jedem Umschlag. Erlaubt sind deshalb nur Zeichen,
    die kein Trennzeichen des Wire-Formats sind (`.` trennt dort die Felder) und
    die in keiner URL umkodiert werden muessen.
    """
    if not isinstance(device_id, str):
        return False
    wert = device_id.strip()
    if not 8 <= len(wert) <= MAX_KENNUNG:
        return False
    return all(c.isalnum() or c in "-_" for c in wert)


def veroeffentlichen(
    db: Session,
    user: User,
    device_id: str,
    public_key_jwk: str,
    label: str = "",
    signing_public_key_jwk: str = "",
    familie: str | None = None,
    self_signature: str = "",
) -> UserE2eeDevice:
    """Legt den Schluessel dieses Geraets ab oder frischt ihn auf.

    Idempotent: dasselbe Geraet meldet sich bei jedem Start erneut. Wechselt
    dabei der Schluessel, gilt der neue — aber ohne Freigabe, solange es ein
    anderes freigegebenes Geraet gibt (siehe `_auffrischen`).

    Der Signaturschluessel kommt beim Bestand nach: ein leerer Wert laesst den
    gespeicherten stehen, statt ihn zu loeschen. Ein aelterer Client, der das
    Feld gar nicht kennt, nimmt einem Geraet sonst seine Beglaubigung wieder
    weg — und damit faellt es zurueck in den ungeprueften Zustand, den die
    Signatur gerade beendet hat.

    `familie` ist die Sitzungskette der Anfrage. Sie wird am Geraet vermerkt,
    damit Entfernen genau diese Sitzung aussperren kann.

    `self_signature` ist die Unterschrift des Geraets ueber seine eigene
    Freigabe (`_selbst_beglaubigen`). Sie wird nur eingetragen, solange das
    Geraet freigegeben ist — ein wartendes hat nichts zu beglaubigen.
    """
    from schemas.social import validate_ecdsa_public_key_jwk, validate_rsa_public_key_jwk

    kennung = (device_id or "").strip()
    if not kennung_gueltig(kennung):
        raise ValueError("Ungueltige Geraetekennung.")

    validate_rsa_public_key_jwk(public_key_jwk)
    signatur = (signing_public_key_jwk or "").strip()
    if signatur:
        validate_ecdsa_public_key_jwk(signatur)

    bestand = _geraet(db, user.id, kennung)
    if bestand is not None:
        return _auffrischen(
            db, user, bestand, public_key_jwk, signatur, label, familie, self_signature
        )

    _deckel_einhalten(db, user)

    # Das erste Geraet eines Kontos ist freigegeben — es gibt niemanden, der es
    # freigeben koennte. Jedes weitere wartet, bis ein freigegebenes es
    # unterschreibt. Wer nur das Passwort hat, bekommt damit nichts.
    eintrag = UserE2eeDevice(
        user_id=user.id,
        device_id=kennung,
        public_key_jwk=public_key_jwk,
        signing_public_key_jwk=signatur or None,
        label=(
            (label or "").strip()
            or _name_der_einladung(db, user.id, familie)
            or STANDARD_BEZEICHNUNG
        )[:MAX_BEZEICHNUNG],
        is_approved=_freigegebene(db, user.id).count() == 0,
        auth_family=familie or None,
        created_at=_jetzt(),
        last_seen_at=_jetzt(),
    )
    if eintrag.is_approved and self_signature:
        _selbst_beglaubigen(eintrag, user.id, self_signature)
    db.add(eintrag)
    try:
        db.commit()
    except IntegrityError:
        # Zwei Anfragen desselben Geraets im selben Augenblick. Das ist hier
        # kein Ausnahmefall, sondern der Normalfall: der Messenger meldet sich
        # beim Aufbau mehrfach, und alle Aufrufe finden dieselbe leere Ablage
        # vor, bevor einer von ihnen geschrieben hat. Der Verlierer bekam eine
        # 500 und das Geraet damit keinen veroeffentlichten Schluessel — ohne
        # den kann ihm niemand schreiben. Aufgefallen ist es erst am laufenden
        # System; die Tests fahren die drei Aufrufe nacheinander.
        #
        # `uq_user_e2ee_device` hat also schon entschieden. Der Verlierer
        # frischt den Eintrag des Gewinners auf — mit derselben Regel fuer
        # einen Schluesselwechsel wie jeder andere Aufruf.
        db.rollback()
        bestand = _geraet(db, user.id, kennung)
        if bestand is None:
            raise
        return _auffrischen(
            db, user, bestand, public_key_jwk, signatur, label, familie, self_signature
        )
    db.refresh(eintrag)
    _verzeichnis_geaendert(user.id)
    return eintrag


def _selbst_beglaubigen(eintrag: UserE2eeDevice, user_id: int, signatur: str | None) -> bool:
    """Traegt die Selbst-Signatur eines Erstgeraets ein, wenn sie stimmt.

    Das erste Geraet eines Kontos gibt sich selbst frei — es gibt niemanden,
    der es freigeben koennte. Bis 10/2026 blieben `approved_by` und
    `approval_signature` dabei leer, und die Clients, die statt `is_approved`
    die Unterschrift pruefen, sahen „freigegeben, aber ohne gueltige Freigabe".
    Jetzt steht die Unterschrift darunter, und beides sagt dasselbe.

    **Eine Selbst-Signatur belegt nichts ueber ein Gegenueber** — nur das
    Geraet selbst kennt seinen Schluessel. Sie ist deshalb ausdruecklich
    ersetzbar: sobald ein freigegebenes Geraet die Freigabe unterschreibt,
    ueberschreibt `bestaetigen` sie.

    Stimmt die Unterschrift nicht, bleibt das Feld leer statt abgelehnt: sie
    ist Beiwerk zur Freigabe, kein Zugang.
    """
    if not signatur or not eintrag.signing_public_key_jwk:
        return False
    daten = freigabe_daten(
        user_id, eintrag.device_id, eintrag.public_key_jwk, eintrag.signing_public_key_jwk
    )
    if not _pruefe_signatur(daten, eintrag.signing_public_key_jwk, signatur):
        return False
    eintrag.approved_by = eintrag.device_id
    eintrag.approval_signature = signatur
    return True


def _verzeichnis_geaendert(user_id: int) -> None:
    """Sagt jeder offenen Geraeteliste des Kontos, dass sie neu laden soll.

    Nur das Signal, kein Inhalt: die Liste holt sich den Stand selbst. Bis
    5.0.3 lud sie nur beim Oeffnen, und ein frisch gekoppeltes Geraet erschien
    zum Freigeben erst nach dem Neuladen der Seite.
    """
    from services.sync_event_service import SyncEventService

    SyncEventService.publish({"entity": "e2ee_devices", "action": "changed"}, user_id=user_id)


def _name_der_einladung(db: Session, user_id: int, familie: str | None) -> str:
    """Der Name, unter dem dieses Geraet im Panel eingeladen wurde, oder "".

    Die Einladung merkt sich die Sitzungskette, die aus ihr entstand — und mit
    genau der meldet sich das gekoppelte Geraet hier. Ohne eigenen Namen stand
    es sonst als „Unbenanntes Geraet" in der Liste, neben dem Browser, der
    auch keinen hat.
    """
    if not familie:
        return ""
    from models.device_pairing import DevicePairing

    einladung = (
        db.query(DevicePairing)
        .filter(DevicePairing.user_id == user_id, DevicePairing.family == familie)
        .order_by(DevicePairing.created_at.desc())
        .first()
    )
    return (einladung.label or "").strip() if einladung else ""


def _geraet(db: Session, user_id: int, device_id: str) -> UserE2eeDevice | None:
    return (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user_id, UserE2eeDevice.device_id == device_id)
        .first()
    )


def _freigegebene(db: Session, user_id: int):
    return db.query(UserE2eeDevice).filter(
        UserE2eeDevice.user_id == user_id, UserE2eeDevice.is_approved.is_(True)
    )


def _auffrischen(
    db: Session,
    user: User,
    bestand: UserE2eeDevice,
    public_key_jwk: str,
    signatur: str,
    label: str,
    familie: str | None,
    self_signature: str = "",
) -> UserE2eeDevice:
    """Frischt einen Eintrag auf. Ein neuer Schluessel verliert die Freigabe.

    Wer das Passwort hat, kann unter jeder bekannten Kennung neue Schluessel
    ablegen. Gaelte die alte Freigabe weiter, schriebe ihm jedes Gegenueber.
    Dazu zaehlt auch ein **ersetzter** Signaturschluessel: die Freigabe
    unterschreibt beide Schluessel, und ein neuer ist durch sie nicht gedeckt.
    Einen neuen Schluessel legt nur die Sitzung ab, an die das Geraet
    gebunden ist.

    **Die Freigabe bleibt nie mehr stehen, auch nicht beim einzigen Geraet.**
    Bis 10/2026 galt hier eine Ausnahme: gab es kein anderes freigegebenes
    Geraet, blieb `is_approved = true` — sonst kaeme das einzige Geraet nie
    wieder hinein. Genau daraus entstand der Widerspruch aus dem Fehlerbericht:
    die Liste zeigte „freigegeben", obwohl keine Unterschrift mehr existierte,
    und jedes Gegenueber warnte „es bekommt nichts". Jetzt ist der Eintrag
    ehrlich wartend, und der Weg zurueck steht in `wieder_freigeben` — mit
    frischem Nachweis statt mit einem stehengebliebenen Wort.

    **Auch ein nachgereichter Signaturschluessel ist ein Wechsel.** Er ist der
    bequeme Weg, sich Freigaberecht zu verschaffen: wer das Token dieses Geraets
    hat, legt unter derselben Kennung seinen eigenen Signaturschluessel ab und
    unterschreibt damit Freigaben fuer weitere Geraete. Eine Ausnahme fuer den
    Altbestand (Feld war leer) gab es kurz und sie war genau das Loch — ein
    abgegriffenes Token wurde damit zur Freigabeinstanz. Der Preis ist eine
    einmalige Nachbestaetigung fuer Geraete von vor 09/2026; sie laeuft ueber
    `wieder_freigeben` oder die Unterschrift eines freigegebenen Geraets.
    """
    neuer_schluessel = bestand.public_key_jwk != public_key_jwk or (
        bool(signatur) and (bestand.signing_public_key_jwk or "") != signatur
    )
    if neuer_schluessel and (not bestand.auth_family or bestand.auth_family != familie):
        # Ein neuer Schluessel aus einer fremden Sitzung. Das echte Geraet
        # behaelt Kennung und Schluessel in derselben Ablage — verliert es sie,
        # zieht es eine neue Kennung. Unter einer bekannten Kennung einen
        # anderen Schluessel ablegen will nur, wer das Geraet kapern oder
        # stilllegen moechte.
        #
        # Unbekannte Familie heisst fremd, nicht egal. Bis 29.09.2026 liess
        # eine Zeile ohne `auth_family` (Bestand vor `20260924_01`, oder
        # angelegt mit einem Token ohne Familie) jeden Wechsel durch; war sie
        # das einzige freigegebene Geraet, blieb die Freigabe stehen. Ein
        # abgegriffenes Access-Token genuegte, um dort eigene Schluessel
        # abzulegen und das echte Geraet an die eigene Familie zu binden.
        # Mit unveraenderten Schluesseln uebernimmt das echte Geraet seine
        # Familie weiter unten wie bisher.
        raise FremdeSitzungError()
    if neuer_schluessel:
        bestand.approved_by = None
        bestand.approval_signature = None
        bestand.is_approved = False
    vorher = (bestand.public_key_jwk, bestand.signing_public_key_jwk, bestand.label, bestand.is_approved)
    bestand.public_key_jwk = public_key_jwk
    if signatur:
        bestand.signing_public_key_jwk = signatur
    if label.strip():
        bestand.label = label.strip()[:MAX_BEZEICHNUNG]
    elif not bestand.label or bestand.label == STANDARD_BEZEICHNUNG:
        # Auch der Standardname gilt hier als „noch nichts gewaehlt": ein
        # Geraet, das sich vor seiner Einladung gemeldet hat, bekommt den Namen
        # der Einladung, sobald es mit deren Sitzungskette wiederkommt. Ein von
        # Hand vergebener Name wird nie ueberschrieben.
        gewaehlt = _name_der_einladung(db, user.id, familie)
        if gewaehlt:
            bestand.label = gewaehlt[:MAX_BEZEICHNUNG]
        elif not bestand.label:
            bestand.label = STANDARD_BEZEICHNUNG
    if familie:
        bestand.auth_family = familie
    if bestand.is_approved and not bestand.approval_signature and self_signature:
        _selbst_beglaubigen(bestand, user.id, self_signature)
    bestand.last_seen_at = _jetzt()
    db.commit()
    db.refresh(bestand)
    # Jeder Start meldet sich neu; das allein ist keine Aenderung der Liste.
    if vorher != (
        bestand.public_key_jwk,
        bestand.signing_public_key_jwk,
        bestand.label,
        bestand.is_approved,
    ):
        _verzeichnis_geaendert(user.id)
    return bestand


class GeraetedeckelErreichtError(ValueError):
    """Der Deckel ist voll und der Mensch muss entscheiden, was weichen soll."""

    def __init__(self) -> None:
        super().__init__(
            f"Es sind bereits {MAX_GERAETE} Geraete angemeldet. Entferne eines unter "
            "Profil → Geraete, bevor du dieses hinzufuegst."
        )


class FremdeSitzungError(ValueError):
    """Eine andere Sitzung will unter dieser Geraetekennung neue Schluessel ablegen."""

    def __init__(self) -> None:
        super().__init__("Diese Geraetekennung gehoert zu einer anderen Sitzung.")


class GeraeteaenderungAbgelehntError(PermissionError):
    """Entfernen ohne gueltige Unterschrift eines freigegebenen Geraets."""


def _deckel_einhalten(db: Session, user: User) -> None:
    """Weist einen Neuzugang ab, wenn der Deckel voll ist.

    Bis 09/2026 verdraengte diese Funktion hier das laengst stille Geraet, und
    zwar lautlos. Das war die falsche Haelfte der Entscheidung: der Server nahm
    einem Geraet die Zustelladresse weg, ohne dass irgendwo etwas davon stand.
    Auf dem verdraengten Geraet aenderte sich nichts sichtbar — der Verlauf
    blieb stehen, die Oberflaeche wirkte heil, und es kam nur nie wieder eine
    Nachricht an. Ein Messenger, der stumm wird und so tut als sei er in
    Ordnung, ist schlimmer als einer, der sagt was los ist.

    Jetzt entscheidet der Mensch. Die Liste steht unter `/profile?tab=devices`,
    und dort ist auch zu sehen, welches Geraet am laengsten nichts mehr getan
    hat.
    """
    anzahl = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id)
        .count()
    )
    if anzahl >= MAX_GERAETE:
        raise GeraetedeckelErreichtError()


def eintrag_als_dict(e: UserE2eeDevice) -> dict:
    return {
        "device_id": e.device_id,
        "public_key": e.public_key_jwk,
        "signing_public_key": e.signing_public_key_jwk or "",
        # Bestand von vor 10/2026: das Feld ist leer, weil es damals niemand
        # gefuellt hat. Die Anzeige nennt trotzdem einen Namen, statt auf jedem
        # anderen Geraet „Unbenanntes Geraet" zu zeigen.
        "label": (e.label or "").strip() or STANDARD_BEZEICHNUNG,
        "is_approved": bool(e.is_approved),
        "approved_by": e.approved_by or "",
        "approval_signature": e.approval_signature or "",
    }


def geraete(db: Session, user_id: int, nur_bestaetigt: bool = False) -> list[dict]:
    """Die Zustelladressen eines Kontos, juengste Aktivitaet zuerst.

    Mit `nur_bestaetigt` nur freigegebene Geraete: ein wartendes bekommt
    nichts. Das ist die Schranke gegen den, der nur das Passwort hat. Gegen den
    Server selbst hilft sie nicht — der koennte `is_approved` setzen, wie er
    will. Dagegen steht die Unterschrift in `approval_signature`, die jedes
    Gegenueber selbst prueft (`vertrauteGeraete` im Frontend).
    """
    query = db.query(UserE2eeDevice).filter(UserE2eeDevice.user_id == user_id)
    if nur_bestaetigt:
        query = query.filter(UserE2eeDevice.is_approved.is_(True))
    return [eintrag_als_dict(e) for e in query.order_by(UserE2eeDevice.last_seen_at.desc()).all()]


def _hash(wert: str | None) -> str:
    return hashlib.sha256((wert or "").encode("utf-8")).hexdigest()


def freigabe_daten(
    user_id: int, device_id: str, public_key_jwk: str, signing_public_key_jwk: str | None
) -> str:
    """Was ein freigegebenes Geraet unterschreibt, wenn es ein neues freigibt.

    Beide Schluessel stehen als Hash darin. Die erste Fassung (v1) nannte nur
    die Kennung: unter derselben Kennung haette jeder neue Schluessel die alte
    Freigabe geerbt. Gehasht wird die Zeichenkette, wie der Server sie
    ausliefert — alle Seiten rechnen ueber dieselben Bytes
    (`freigabeDaten` in `e2eeGeraet.ts`).
    """
    return (
        f"msm:device-approval:v2:{user_id}:{device_id}:"
        f"{_hash(public_key_jwk)}:{_hash(signing_public_key_jwk)}"
    )


def entfernen_daten(user_id: int, device_id: str, public_key_jwk: str) -> str:
    """Was ein freigegebenes Geraet unterschreibt, wenn es ein Geraet entfernt.

    Der Schluessel steht darin, damit eine alte Unterschrift nicht eine spaetere
    Anmeldung unter derselben Kennung wieder entfernen kann.
    """
    return f"msm:device-removal:v1:{user_id}:{device_id}:{_hash(public_key_jwk)}"


def _pruefe_signatur(daten: str, signing_jwk_str: str, signatur_b64: str) -> bool:
    """ECDSA P-256 / SHA-256 im Format der WebCrypto (r||s, 64 Byte, Base64)."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        jwk = json.loads(signing_jwk_str)

        def b64url(s: str) -> bytes:
            return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))

        oeffentlich = ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64url(jwk["x"]), "big"),
            int.from_bytes(b64url(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        roh = base64.b64decode(signatur_b64)
        if len(roh) != 64:
            return False
        der = utils.encode_dss_signature(
            int.from_bytes(roh[:32], "big"), int.from_bytes(roh[32:], "big")
        )
        oeffentlich.verify(der, daten.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def _unterzeichner(db: Session, user: User, device_id: str | None) -> UserE2eeDevice | None:
    """Ein freigegebenes Geraet dieses Kontos mit Signaturschluessel, oder nichts."""
    kennung = (device_id or "").strip()
    if not kennung:
        return None
    geraet = _geraet(db, user.id, kennung)
    if geraet is None or not geraet.is_approved or not geraet.signing_public_key_jwk:
        return None
    return geraet


def unterschrift_gilt(
    db: Session, user: User, device_id: str | None, daten: str, signatur: str | None
) -> bool:
    """Hat ein freigegebenes Geraet dieses Kontos ``daten`` unterschrieben?"""
    unterzeichner = _unterzeichner(db, user, device_id)
    if unterzeichner is None or not signatur:
        return False
    return _pruefe_signatur(daten, unterzeichner.signing_public_key_jwk, signatur)


def bestaetigen(
    db: Session,
    user: User,
    device_id: str,
    approver_device_id: str | None = None,
    signature: str | None = None,
) -> bool:
    """Gibt ein wartendes Geraet frei — mit der Unterschrift eines freigegebenen.

    Die Freigabe braucht die Unterschrift eines freigegebenen Geraets mit
    Signaturschluessel ueber `freigabe_daten`. Ein Geraet ohne
    Signaturschluessel gibt nichts frei: sonst genuegte es, seine Kennung zu
    nennen, und die steht in jeder Geraeteliste.

    **Keine Ausnahme mehr fuer das letzte Geraet.** Bis 10/2026 wurde ein
    wartendes ohne jede Unterschrift freigegeben, sobald es kein freigegebenes
    Geraet mehr gab. Damit holte sich jeder, der nur ein Access-Token hatte,
    wieder Zutritt. Den Fall uebernimmt `wieder_freigeben`, und der verlangt
    einen frischen Nachweis.

    **Auch ein schon freigegebenes Geraet laesst sich nachziehen.** Bis 10/2026
    antwortete diese Funktion bei `is_approved` sofort mit `True` und schrieb
    nichts: ein Geraet, das ohne Unterschrift freigegeben war (Erstgeraet nach
    `wieder_freigeben`), blieb auf jedem anderen Geraet „ohne gueltige
    Freigabe", und der Knopf daneben tat nichts. Jetzt unterschreibt ein
    freigegebenes Geraet auch nach, solange die vorhandene Unterschrift nur
    eine Selbst-Signatur ist.
    """
    kennung = (device_id or "").strip()
    eintrag = _geraet(db, user.id, kennung)
    if eintrag is None:
        return False
    # Von einem anderen Geraet beglaubigt: dabei bleibt es. Eine Selbst-Signatur
    # (`approved_by == device_id`) ist ausdruecklich ersetzbar.
    if eintrag.is_approved and eintrag.approval_signature and eintrag.approved_by != kennung:
        return True
    unterzeichner = _unterzeichner(db, user, approver_device_id)
    if unterzeichner is None or unterzeichner.device_id == kennung or not signature:
        return eintrag.is_approved
    daten = freigabe_daten(
        user.id, kennung, eintrag.public_key_jwk, eintrag.signing_public_key_jwk
    )
    if not _pruefe_signatur(daten, unterzeichner.signing_public_key_jwk, signature):
        return False
    eintrag.is_approved = True
    eintrag.approved_by = unterzeichner.device_id
    eintrag.approval_signature = signature
    db.commit()
    _verzeichnis_geaendert(user.id)
    return True


def freigabe_hindernis(db: Session, user: User, device_id: str) -> str | None:
    """Was einer Selbstfreigabe im Weg steht, bevor ein Nachweis verbraucht wird.

    Ein Passkey oder ein Einmalcode gilt genau einmal. Prueft die Route erst
    *nach* dem Nachweis, ob es das Geraet ueberhaupt gibt und ob ein anderes
    freigegebenes Geraet existiert, verbrennt eine veraltete Geraeteliste einen
    gueltigen Nachweis fuer eine Antwort, die 403 oder 404 lautet.

    Zurueck kommt `"nicht_gefunden"`, `"anderes_freigegeben"` oder `None`. Die
    Entscheidung selbst faellt hier **nicht**: zwischen dieser Abfrage und dem
    Schreiben kann ein anderes Geraet dazukommen, deshalb prueft
    `wieder_freigeben` unter der Kontosperre noch einmal.
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        return "nicht_gefunden"
    if eintrag.is_approved:
        return None
    if _freigegebene(db, user.id).count() > 0:
        return "anderes_freigegeben"
    return None


def wieder_freigeben(db: Session, user: User, device_id: str, signatur: str | None = None) -> bool:
    """Holt ein wartendes Geraet zurueck, wenn es das einzige ist.

    Der Gegenpol zu `_auffrischen`: wer seinen Schluessel verloren und neu
    erzeugt hat, sitzt sonst in einem Wartezustand, den niemand aufloesen kann
    — es gibt kein zweites freigegebenes Geraet, das unterschreiben koennte.

    **Nur wenn kein freigegebenes Geraet mehr existiert.** Existiert eines,
    ist das hier ein Weg an der Unterschrift vorbei und wird abgelehnt: sonst
    holte sich jeder, der nur das Passwort hat, mit einem Klick wieder Zutritt.
    Den Nachweis (2FA oder Passwort) prueft der Aufrufer — hier steht nur die
    Regel, wer ueberhaupt in Frage kommt.

    **Gelesen und geschrieben unter einer Sperre.** Zwei wartende Geraete, die
    gleichzeitig anfragen, saehen sonst beide „kein freigegebenes" und gäben
    beide frei; die Unterschrift des ersten waere umgangen. Die Sperre liegt auf
    der Kontozeile, weil sie die gemeinsame Zeile beider Anfragen ist.

    `approved_by` und `approval_signature` bleiben ohne `signatur` leer, und
    das ist der Unterschied zwischen „freigegeben" und „beglaubigt": der Server
    gibt das Geraet fuer die Zustellung frei, aber niemand hat den Schluessel
    unterschrieben. Die Gegenueber sehen genau das („Ohne gueltige Freigabe")
    und entscheiden selbst, ob sie die Sicherheitsnummer vergleichen.

    Reicht der Client seine eigene Unterschrift mit (`signatur`), steht sie
    danach darunter — `is_approved` und `approval_signature` sagen dann
    dasselbe. Sie belegt nur, dass dieses Geraet seinen eigenen Schluessel
    kennt, und wird von der Unterschrift eines anderen freigegebenen Geraets
    ersetzt (`bestaetigen`).
    """
    # Sperre vor dem Lesen: PostgreSQL serialisiert damit alle gleichzeitigen
    # Selbstfreigaben desselben Kontos, auch wenn sie verschiedene Geraete
    # betreffen.
    db.query(User.id).filter(User.id == user.id).with_for_update().one()

    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        db.rollback()
        return False
    if eintrag.is_approved:
        db.rollback()
        return True
    if _freigegebene(db, user.id).count() > 0:
        db.rollback()
        raise GeraeteaenderungAbgelehntError(
            "Ein freigegebenes Geraet muss dieses Geraet freigeben."
        )
    eintrag.is_approved = True
    eintrag.approved_by = None
    eintrag.approval_signature = None
    _selbst_beglaubigen(eintrag, user.id, signatur)
    db.commit()
    _verzeichnis_geaendert(user.id)
    return True


def ausstehende_geraete(db: Session, user: User) -> list[dict]:
    """Geraete des Kontos, die noch auf Freigabe warten."""
    eintraege = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id, UserE2eeDevice.is_approved.is_(False))
        .order_by(UserE2eeDevice.created_at.desc())
        .all()
    )
    return [eintrag_als_dict(e) for e in eintraege]


def _sperre_sitzung(db: Session, user: User, familie: str | None) -> None:
    if familie:
        from services.auth_service import AuthService

        AuthService.revoke_refresh_family(db, user.id, familie)


def entfernen_hindernis(
    db: Session, user: User, device_id: str, anfrage_familie: str | None = None
) -> str | None:
    """Was dem Entfernen im Weg steht, bevor ein Nachweis verbraucht wird.

    Ein Passkey und ein Einmalcode gelten genau einmal. Prueft die Route erst
    *nach* der Nachweispflicht, ob es das Geraet gibt und ob es das eigene ist,
    ist der Nachweis fuer eine 404 oder 403 weg (dieselbe Regel wie in
    `freigabe_hindernis`).

    Zurueck kommt `"selbst"`, `"nicht_gefunden"` oder `None`. Die Entscheidung
    faellt trotzdem in `vergessen` noch einmal — zwischen dieser Abfrage und dem
    Loeschen kann sich das Verzeichnis aendern.
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        return "nicht_gefunden"
    if _ist_eigenes_geraet(eintrag, anfrage_familie):
        return "selbst"
    return None


def entfernen_beleg_gilt(
    db: Session,
    user: User,
    device_id: str,
    approver_device_id: str | None = None,
    signature: str | None = None,
) -> bool:
    """Wuerde `vergessen` diese Unterschrift annehmen? Prueft nur, schreibt nicht.

    Die Route fragt das **vor** dem Nachweis: ein Einmalcode oder Passkey gilt
    genau einmal, und ein abgelehnter Beleg ist kein Grund, ihn zu verbrauchen
    (dieselbe Regel wie `freigabe_hindernis`).

    Ein wartendes Geraet braucht keinen Beleg — es hat nie etwas bekommen.
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        return False
    if not eintrag.is_approved:
        return True
    return unterschrift_gilt(
        db,
        user,
        approver_device_id,
        entfernen_daten(user.id, eintrag.device_id, eintrag.public_key_jwk),
        signature,
    )


def eintrag_ist_freigegeben(db: Session, user: User, device_id: str) -> bool:
    """Ist das Ziel freigegeben — bekommt es also ueberhaupt Nachrichten?

    Der Unterschied entscheidet, ob vor dem Entfernen ein Nachweis faellig ist.
    Ein wartendes Geraet hat nie etwas bekommen; es zu entfernen nimmt niemandem
    etwas weg, und genau das ist der Weg „das ist nicht mein Geraet, sofort
    raus" nach einer Kopplung mit falscher Sicherheitsnummer
    (`AiDevicePairingCard`). Ein Nachweisfenster davor waere hier eine Bremse
    an der falschen Stelle.
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    return bool(eintrag and eintrag.is_approved)


def _ist_eigenes_geraet(eintrag: UserE2eeDevice, anfrage_familie: str | None) -> bool:
    """Fragt *dieses* Geraet nach seinem eigenen Eintrag?

    Die Sitzungskette ist die verlaessliche Spur: sie kommt aus dem Token der
    Anfrage und steht am Geraet (`auth_family`). Fehlt einer der beiden Werte,
    gilt es als fremd — „unbekannt" heisst hier nicht „egal" (Punkt 58 der
    Projektregeln).
    """
    return bool(anfrage_familie and eintrag.auth_family == anfrage_familie)


def vergessen(
    db: Session,
    user: User,
    device_id: str,
    approver_device_id: str | None = None,
    signature: str | None = None,
    anfrage_familie: str | None = None,
) -> bool:
    """Nimmt ein Geraet aus dem Verzeichnis und sperrt seine Sitzung aus.

    Der `user_id`-Filter ist die Schranke gegen Fremde: ohne ihn liesse sich
    mit einer geratenen Kennung ein fremdes Geraet aus dem Verkehr ziehen.

    Ein freigegebenes Geraet entfernt nur, wer als freigegebenes Geraet
    unterschreibt (`entfernen_daten`) — auch das Geraet selbst. Sonst entfernte
    der, der nur das Passwort hat, alle echten Geraete, und sein eigenes waere
    danach das erste und damit freigegeben. Ein wartendes Geraet darf jeder
    angemeldete Teil des Kontos entfernen; es hat nie etwas bekommen.

    **Das eigene Geraet entfernt sich nie selbst.** Bis 10/2026 durfte es das
    mit seiner eigenen Unterschrift; in der Oberflaeche gab es dafuer keinen
    Knopf, aber ein Aufruf mit `device_id == approver_device_id` kam durch. Ein
    Geraet, das sich selbst hinauswirft, ist kein Entfernen, sondern ein
    Abmelden — dafuer gibt es `/auth/logout`, und der sperrt nicht das ganze
    Verzeichnis. Erkannt wird es an der Sitzungskette der Anfrage und an der
    Kennung des Unterzeichners.

    Die Sitzungskette des Geraets wird gesperrt: sein Access-Token gilt ab dem
    naechsten Aufruf nicht mehr (`dependencies._user_from_token`).
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        return False
    if _ist_eigenes_geraet(eintrag, anfrage_familie) or (
        (approver_device_id or "").strip() == eintrag.device_id
    ):
        raise GeraeteaenderungAbgelehntError(
            "Ein Gerät kann sich nicht selbst aus der Geräteliste entfernen."
        )
    if eintrag.is_approved:
        unterzeichner = _unterzeichner(db, user, approver_device_id)
        daten = entfernen_daten(user.id, eintrag.device_id, eintrag.public_key_jwk)
        if (
            unterzeichner is None
            or not signature
            or not _pruefe_signatur(daten, unterzeichner.signing_public_key_jwk, signature)
        ):
            raise GeraeteaenderungAbgelehntError(
                "Ein freigegebenes Geraet entfernt nur ein freigegebenes Geraet."
            )
    familie = eintrag.auth_family
    db.delete(eintrag)
    db.commit()
    _sperre_sitzung(db, user, familie)
    _verzeichnis_geaendert(user.id)
    return True


def zuruecksetzen(db: Session, user: User, familie_behalten: str | None) -> int:
    """Entfernt alle Geraete des Kontos und sperrt ihre Sitzungen.

    Der Weg fuer den, dem alle Geraete verloren gegangen sind. Das Passwort
    prueft der Aufrufer. Danach ist das naechste Geraet, das sich meldet, das
    erste — und jedes Gegenueber sieht, dass alle ihm bekannten Geraete fort
    sind, und warnt. Wer nur das Passwort hat, kann das auch; still geht es
    nicht: die echten Geraete fliegen hinaus, und die Kontakte bekommen die
    Warnung.
    """
    eintraege = db.query(UserE2eeDevice).filter(UserE2eeDevice.user_id == user.id).all()
    familien = {e.auth_family for e in eintraege if e.auth_family}
    for e in eintraege:
        db.delete(e)
    # Der Notizschluessel lag auf diesen Geraeten. Ohne sie gibt es niemanden
    # mehr, der ihn hat oder den Eintrag beglaubigen koennte: das naechste
    # Geraet setzt den Kontoschluessel neu (`kontoschluesselAbgleichen`).
    db.query(User).filter(User.id == user.id).update(
        {
            User.notes_key_abdruck: None,
            User.notes_key_geraet: None,
            User.notes_key_signatur: None,
            # `notes_key_stand` bleibt: auch nach dem Neubeginn passt keine
            # Unterschrift von vorher auf den naechsten Eintrag.
        },
        synchronize_session=False,
    )
    db.commit()
    for familie in familien - {familie_behalten}:
        _sperre_sitzung(db, user, familie)
    _verzeichnis_geaendert(user.id)
    return len(eintraege)
