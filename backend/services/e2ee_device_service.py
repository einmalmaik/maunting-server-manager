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
"""

from __future__ import annotations

from datetime import datetime, timezone

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
) -> UserE2eeDevice:
    """Legt den Schluessel dieses Geraets ab oder frischt ihn auf.

    Idempotent: dasselbe Geraet meldet sich bei jedem Start erneut. Wechselt
    dabei der Schluessel, gilt der neue — ein Geraet, das seine lokale Ablage
    verloren hat, muss sich neu melden koennen, sonst kaeme es nie wieder in
    ein Gespraech hinein.
    """
    from schemas.social import validate_rsa_public_key_jwk

    kennung = (device_id or "").strip()
    if not kennung_gueltig(kennung):
        raise ValueError("Ungueltige Geraetekennung.")

    validate_rsa_public_key_jwk(public_key_jwk)

    bestand = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id, UserE2eeDevice.device_id == kennung)
        .first()
    )
    if bestand is not None:
        bestand.public_key_jwk = public_key_jwk
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
        label=(label or "").strip()[:MAX_BEZEICHNUNG],
        created_at=_jetzt(),
        last_seen_at=_jetzt(),
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)
    return eintrag


def _deckel_einhalten(db: Session, user: User) -> None:
    """Macht Platz fuer einen Neuzugang, indem die aeltesten Stillen weichen.

    Verdraengt wird nach `last_seen_at`, nicht nach `created_at`: das aelteste
    Geraet ist oft das meistgenutzte, das laengst stille dagegen ein Browser,
    den niemand mehr oeffnet.
    """
    bestand = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == user.id)
        .order_by(UserE2eeDevice.last_seen_at.asc())
        .all()
    )
    ueberzaehlig = len(bestand) - MAX_GERAETE + 1
    for eintrag in bestand[: max(0, ueberzaehlig)]:
        db.delete(eintrag)
    if ueberzaehlig > 0:
        db.flush()


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
