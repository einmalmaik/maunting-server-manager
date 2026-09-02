"""Kopplungscodes anlegen, einloesen, aufraeumen.

Die Form ist von `node_enrollment_service` uebernommen — Hash statt Klartext,
Anzeigealphabet ohne verwechselbare Zeichen, kurze Frist, Aufraeumen bei jeder
Gelegenheit. Die Richtung ist die andere: dort meldet sich ein Rechner an und
ein Mensch bestaetigt, hier laedt ein angemeldeter Mensch ein Geraet ein.

Daraus folgt der eine Unterschied, der zaehlt: **der Code ist das ganze
Geheimnis.** Beim Node identifiziert der Anzeigecode nur, authentifiziert wird
mit einem 48-Byte-Claim daneben. Hier tippt oder klebt ein Mensch nur den einen
Wert in die App. Deshalb zwoelf Zeichen aus einem 32er-Alphabet (32^12, rund
1,2 Trillionen Moeglichkeiten), zehn Minuten Frist und genau ein Einloesen —
und weil der ganze Auth-Router unter `auth_rate_limit` haengt, kommt niemand
auf mehr als eine Handvoll Versuche.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy.orm import Session

from models import DevicePairing, User

# Zehn Minuten: lang genug, um vom Browser zum Desktop-Fenster zu wechseln,
# kurz genug, dass ein Code in der Zwischenablage nicht zum Dauerausweis wird.
FRIST_MINUTEN = 10
# Ohne I, O, 0 und 1 — der Code wird abgetippt, und diese vier sind die
# Verwechslungen, die dann passieren.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GRUPPEN = 3
GRUPPENLAENGE = 4
MAX_BEZEICHNUNG = 64


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _code() -> str:
    """Zwoelf Zeichen in drei Gruppen: ``ABCD-EFGH-JKLM``."""
    roh = "".join(secrets.choice(ALPHABET) for _ in range(GRUPPEN * GRUPPENLAENGE))
    return "-".join(
        roh[i : i + GRUPPENLAENGE] for i in range(0, len(roh), GRUPPENLAENGE)
    )


def normalisieren(eingabe: str) -> str:
    """Nachsichtig lesen: Kleinschreibung, Leerzeichen und fehlende Striche.

    Wer den Code abtippt, tippt ihn irgendwie. Streng ist erst der Vergleich —
    ein Formfehler soll eine Runde kosten, nie die Kopplung (dieselbe Haltung
    wie bei den Werkzeugargumenten).
    """
    nur = "".join(z for z in eingabe.upper() if z in ALPHABET)
    return "-".join(nur[i : i + GRUPPENLAENGE] for i in range(0, len(nur), GRUPPENLAENGE))


def _jetzt(bezug: datetime | None = None) -> datetime:
    """Vergleichbare Zeit — SQLite liefert naive, PostgreSQL bewusste Stempel."""
    jetzt = datetime.now(timezone.utc)
    if bezug is not None and bezug.tzinfo is None:
        return jetzt.replace(tzinfo=None)
    return jetzt


def aufraeumen(db: Session) -> None:
    """Abgelaufene Einladungen loeschen. Eingeloeste bleiben — an ihnen haengt
    die Familie, und ohne sie weiss die Geraeteliste nicht mehr, wie ein Geraet
    heisst."""
    db.query(DevicePairing).filter(
        DevicePairing.redeemed_at.is_(None),
        DevicePairing.expires_at <= datetime.now(timezone.utc),
    ).delete(synchronize_session=False)
    db.commit()


def anlegen(db: Session, user: User, bezeichnung: str) -> tuple[DevicePairing, str]:
    """Legt eine Einladung an und gibt den Klartextcode **einmal** zurueck.

    Der Rueckgabewert ist ein Geheimnis: er gehoert in den Antwortkoerper des
    Aufrufers, der ihn angefordert hat, und in kein Protokoll.
    """
    aufraeumen(db)
    code = _code()
    einladung = DevicePairing(
        user_id=user.id,
        code_hash=_hash(code),
        label=bezeichnung.strip()[:MAX_BEZEICHNUNG],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=FRIST_MINUTEN),
    )
    db.add(einladung)
    db.commit()
    db.refresh(einladung)
    return einladung, code


def einloesen(db: Session, code: str) -> DevicePairing | None:
    """Sucht die passende offene Einladung und markiert sie als verbraucht.

    ``None`` heisst unbekannt, abgelaufen oder schon benutzt — der Aufrufer
    unterscheidet das bewusst **nicht** nach aussen. Wer raten will, soll aus
    der Antwort nicht lernen, ob er nah dran war.
    """
    einladung = (
        db.query(DevicePairing)
        .filter(DevicePairing.code_hash == _hash(normalisieren(code)))
        .with_for_update()
        .first()
    )
    if einladung is None or einladung.redeemed_at is not None:
        return None
    if einladung.expires_at <= _jetzt(einladung.expires_at):
        return None
    einladung.redeemed_at = _jetzt(einladung.expires_at)
    db.flush()
    return einladung


def familie_vermerken(db: Session, einladung: DevicePairing, family: str) -> None:
    """Haengt die Refresh-Familie der frisch entstandenen Sitzung an.

    Getrennt vom Einloesen, weil die Sitzung erst danach existiert. `einloesen`
    setzt `redeemed_at` nur per `flush`: scheitert das Ausstellen danach, rollt
    die Anfrage zurueck und der Code bleibt bis zum Ablauf brauchbar. Fuer die
    Einmaligkeit reicht das — sie haengt an der Sperre in `einloesen`, nicht am
    Zeitpunkt des Commits.
    """
    einladung.family = family
    db.commit()


def geraete(db: Session, user: User) -> list[DevicePairing]:
    """Die gekoppelten Geraete dieses Benutzers, neueste zuerst."""
    return (
        db.query(DevicePairing)
        .filter(
            DevicePairing.user_id == user.id,
            DevicePairing.redeemed_at.isnot(None),
            DevicePairing.family.isnot(None),
        )
        .order_by(DevicePairing.redeemed_at.desc())
        .all()
    )


def vergessen(db: Session, user: User, family: str) -> DevicePairing | None:
    """Entfernt den Eintrag zu einer Familie — **nur** aus dem eigenen Bestand.

    Das Aussperren selbst macht `AuthService.revoke_refresh_family`; hier
    verschwindet nur der Name aus der Liste. Der `user_id`-Filter ist die
    Schranke: ohne ihn koennte man fremde Familien mit geratener Kennung
    treffen.
    """
    einladung = (
        db.query(DevicePairing)
        .filter(DevicePairing.user_id == user.id, DevicePairing.family == family)
        .first()
    )
    if einladung is None:
        return None
    db.delete(einladung)
    db.commit()
    return einladung


def status(db: Session, user: User, code: str) -> dict:
    """Prueft den Status eines erzeugten Kopplungscodes fuer den Ersteller."""
    einladung = (
        db.query(DevicePairing)
        .filter(
            DevicePairing.user_id == user.id,
            DevicePairing.code_hash == _hash(normalisieren(code)),
        )
        .first()
    )
    if einladung is None:
        return {"exists": False, "redeemed": False, "expired": True}
    is_redeemed = einladung.redeemed_at is not None
    is_expired = einladung.expires_at <= _jetzt(einladung.expires_at)
    return {
        "exists": True,
        "redeemed": is_redeemed,
        "expired": is_expired and not is_redeemed,
        "label": einladung.label,
        "family": einladung.family,
    }

