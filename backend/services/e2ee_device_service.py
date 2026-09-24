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
        return _auffrischen(db, user, bestand, public_key_jwk, signatur, label, familie)

    _deckel_einhalten(db, user)

    # Das erste Geraet eines Kontos ist freigegeben — es gibt niemanden, der es
    # freigeben koennte. Jedes weitere wartet, bis ein freigegebenes es
    # unterschreibt. Wer nur das Passwort hat, bekommt damit nichts.
    eintrag = UserE2eeDevice(
        user_id=user.id,
        device_id=kennung,
        public_key_jwk=public_key_jwk,
        signing_public_key_jwk=signatur or None,
        label=(label or "").strip()[:MAX_BEZEICHNUNG],
        is_approved=_freigegebene(db, user.id).count() == 0,
        auth_family=familie or None,
        created_at=_jetzt(),
        last_seen_at=_jetzt(),
    )
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
        return _auffrischen(db, user, bestand, public_key_jwk, signatur, label, familie)
    db.refresh(eintrag)
    return eintrag


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
) -> UserE2eeDevice:
    """Frischt einen Eintrag auf. Ein neuer Schluessel verliert die Freigabe.

    Wer das Passwort hat, kann unter jeder bekannten Kennung neue Schluessel
    ablegen. Gaelte die alte Freigabe weiter, schriebe ihm jedes Gegenueber.
    Dazu zaehlt auch ein nachgereichter Signaturschluessel: die Freigabe
    unterschreibt beide Schluessel, und ein neuer ist durch sie nicht gedeckt.
    Ohne ein anderes freigegebenes Geraet bleibt die Freigabe — sonst kaeme
    das einzige Geraet eines Kontos nie wieder hinein; die Gegenueber sehen
    den Wechsel dann als Warnung.
    """
    neuer_schluessel = bestand.public_key_jwk != public_key_jwk or (
        bool(signatur) and (bestand.signing_public_key_jwk or "") != signatur
    )
    if neuer_schluessel and bestand.auth_family and familie and bestand.auth_family != familie:
        # Ein neuer Schluessel aus einer fremden Sitzung. Das echte Geraet
        # behaelt Kennung und Schluessel in derselben Ablage — verliert es sie,
        # zieht es eine neue Kennung. Unter einer bekannten Kennung einen
        # anderen Schluessel ablegen will nur, wer das Geraet kapern oder
        # stilllegen moechte.
        raise FremdeSitzungError()
    if neuer_schluessel:
        bestand.approved_by = None
        bestand.approval_signature = None
        andere = _freigegebene(db, user.id).filter(UserE2eeDevice.id != bestand.id).count()
        if andere > 0:
            bestand.is_approved = False
    bestand.public_key_jwk = public_key_jwk
    if signatur:
        bestand.signing_public_key_jwk = signatur
    if label:
        bestand.label = label.strip()[:MAX_BEZEICHNUNG]
    if familie:
        bestand.auth_family = familie
    bestand.last_seen_at = _jetzt()
    db.commit()
    db.refresh(bestand)
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
        "label": e.label or "",
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


def bestaetigen(
    db: Session,
    user: User,
    device_id: str,
    approver_device_id: str | None = None,
    signature: str | None = None,
) -> bool:
    """Gibt ein wartendes Geraet frei.

    Die Freigabe braucht die Unterschrift eines freigegebenen Geraets mit
    Signaturschluessel ueber `freigabe_daten`. Ein Geraet ohne
    Signaturschluessel gibt nichts frei: sonst genuegte es, seine Kennung zu
    nennen, und die steht in jeder Geraeteliste.

    Einzige Ausnahme: gibt es ueberhaupt kein freigegebenes Geraet mehr — das
    letzte hat sich selbst entfernt —, wird ein wartendes zum ersten.
    """
    kennung = (device_id or "").strip()
    eintrag = _geraet(db, user.id, kennung)
    if eintrag is None:
        return False
    if eintrag.is_approved:
        return True
    if _freigegebene(db, user.id).count() == 0:
        eintrag.is_approved = True
        db.commit()
        return True
    unterzeichner = _unterzeichner(db, user, approver_device_id)
    if unterzeichner is None or unterzeichner.device_id == kennung or not signature:
        return False
    daten = freigabe_daten(
        user.id, kennung, eintrag.public_key_jwk, eintrag.signing_public_key_jwk
    )
    if not _pruefe_signatur(daten, unterzeichner.signing_public_key_jwk, signature):
        return False
    eintrag.is_approved = True
    eintrag.approved_by = unterzeichner.device_id
    eintrag.approval_signature = signature
    db.commit()
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


def vergessen(
    db: Session,
    user: User,
    device_id: str,
    approver_device_id: str | None = None,
    signature: str | None = None,
) -> bool:
    """Nimmt ein Geraet aus dem Verzeichnis und sperrt seine Sitzung aus.

    Der `user_id`-Filter ist die Schranke gegen Fremde: ohne ihn liesse sich
    mit einer geratenen Kennung ein fremdes Geraet aus dem Verkehr ziehen.

    Ein freigegebenes Geraet entfernt nur, wer als freigegebenes Geraet
    unterschreibt (`entfernen_daten`) — auch das Geraet selbst. Sonst entfernte
    der, der nur das Passwort hat, alle echten Geraete, und sein eigenes waere
    danach das erste und damit freigegeben. Ein wartendes Geraet darf jeder
    angemeldete Teil des Kontos entfernen; es hat nie etwas bekommen.

    Die Sitzungskette des Geraets wird gesperrt: sein Access-Token gilt ab dem
    naechsten Aufruf nicht mehr (`dependencies._user_from_token`).
    """
    eintrag = _geraet(db, user.id, (device_id or "").strip())
    if eintrag is None:
        return False
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
    db.commit()
    for familie in familien - {familie_behalten}:
        _sperre_sitzung(db, user, familie)
    return len(eintraege)
