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
) -> UserE2eeDevice:
    """Legt den Schluessel dieses Geraets ab oder frischt ihn auf.

    Idempotent: dasselbe Geraet meldet sich bei jedem Start erneut. Wechselt
    dabei der Schluessel, gilt der neue — ein Geraet, das seine lokale Ablage
    verloren hat, muss sich neu melden koennen, sonst kaeme es nie wieder in
    ein Gespraech hinein.

    Der Signaturschluessel kommt beim Bestand nach: ein leerer Wert laesst den
    gespeicherten stehen, statt ihn zu loeschen. Ein aelterer Client, der das
    Feld gar nicht kennt, nimmt einem Geraet sonst seine Beglaubigung wieder
    weg — und damit faellt es zurueck in den ungeprueften Zustand, den die
    Signatur gerade beendet hat.
    """
    from schemas.social import validate_ecdsa_public_key_jwk, validate_rsa_public_key_jwk

    kennung = (device_id or "").strip()
    if not kennung_gueltig(kennung):
        raise ValueError("Ungueltige Geraetekennung.")

    validate_rsa_public_key_jwk(public_key_jwk)
    signatur = (signing_public_key_jwk or "").strip()
    if signatur:
        validate_ecdsa_public_key_jwk(signatur)

    bestand = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id, UserE2eeDevice.device_id == kennung)
        .first()
    )
    if bestand is not None:
        bestand.public_key_jwk = public_key_jwk
        if signatur:
            bestand.signing_public_key_jwk = signatur
        if label:
            bestand.label = label.strip()[:MAX_BEZEICHNUNG]
        bestand.last_seen_at = _jetzt()
        db.commit()
        db.refresh(bestand)
        return bestand

    _deckel_einhalten(db, user)

    eintrag = UserE2eeDevice(
        user_id=user.id,
        device_id=kennung,
        public_key_jwk=public_key_jwk,
        signing_public_key_jwk=signatur or None,
        label=(label or "").strip()[:MAX_BEZEICHNUNG],
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
        # `uq_user_e2ee_device` hat also schon entschieden. Der Gewinner hat
        # denselben Schluessel abgelegt, den dieser Aufruf ablegen wollte, und
        # damit ist dieselbe Antwort richtig.
        db.rollback()
        bestand = (
            db.query(UserE2eeDevice)
            .filter(UserE2eeDevice.user_id == user.id, UserE2eeDevice.device_id == kennung)
            .first()
        )
        if bestand is None:
            raise
        bestand.public_key_jwk = public_key_jwk
        if signatur:
            bestand.signing_public_key_jwk = signatur
        if label:
            bestand.label = label.strip()[:MAX_BEZEICHNUNG]
        bestand.last_seen_at = _jetzt()
        db.commit()
        db.refresh(bestand)
        return bestand
    db.refresh(eintrag)
    return eintrag


class GeraetedeckelErreichtError(ValueError):
    """Der Deckel ist voll und der Mensch muss entscheiden, was weichen soll."""

    def __init__(self) -> None:
        super().__init__(
            f"Es sind bereits {MAX_GERAETE} Geraete angemeldet. Entferne eines unter "
            "Profil → Geraete, bevor du dieses hinzufuegst."
        )


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


def geraete(db: Session, user_id: int) -> list[dict]:
    """Die Zustelladressen eines Kontos, juengste Aktivitaet zuerst."""
    eintraege = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user_id)
        .order_by(UserE2eeDevice.last_seen_at.desc())
        .all()
    )
    return [
        {
            "device_id": e.device_id,
            "public_key": e.public_key_jwk,
            "signing_public_key": e.signing_public_key_jwk or "",
            "label": e.label or "",
        }
        for e in eintraege
    ]


def vergessen(db: Session, user: User, device_id: str) -> bool:
    """Nimmt ein Geraet aus dem Verzeichnis.

    Der `user_id`-Filter ist die Schranke: ohne ihn liesse sich mit einer
    geratenen Kennung ein fremdes Geraet aus dem Verkehr ziehen und damit ein
    Gespraech stilllegen.
    """
    eintrag = (
        db.query(UserE2eeDevice)
        .filter(
            UserE2eeDevice.user_id == user.id,
            UserE2eeDevice.device_id == (device_id or "").strip(),
        )
        .first()
    )
    if eintrag is None:
        return False
    db.delete(eintrag)
    db.commit()
    return True
