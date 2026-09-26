"""WebPush: der Weg zu einem Empfaenger, der die Anwendung geschlossen hat.

Der Vordergrund funktionierte schon immer — ein offener Tab bekommt das
Ereignis ueber den Social-WebSocket, und `ServerIncidentNotifier` macht daraus
eine Meldung des Betriebssystems. Wer den Messenger schloss, erfuhr dagegen
nichts: `frontend/public/sw.js` hatte einen fertigen `push`-Listener, aber es
gab keine Tabelle fuer Abonnements, kein Schluesselpaar und keinen Versender.
Diese Datei ist das fehlende Stueck.

## Warum kein `pywebpush`

Weil nichts uebrig bleibt, das es tun muesste. Ein WebPush-Versand ist ein
POST mit zwei Kopfzeilen, und beide entstehen aus Bausteinen, die seit dem
ersten Tag im Baum liegen: RFC 8291 ist ECDH auf P-256, HKDF-SHA256 und
AES-128-GCM (alles `cryptography`, schon da fuer DIS und TLS), RFC 8292 ist ein
ES256-JWT (`python-jose`, signiert bereits jede Panel-Sitzung), und der POST
geht ueber `httpx`. `pywebpush` zoege `http-ece` und `py-vapid` nach — zwei
Pakete fuer die rund achtzig Zeilen unter `_verschluesseln` und `_vapid_kopf`.
Dieselbe Rechnung wie bei LiveKit, nachzulesen in
`docs/agent-rules/dependencies.md`.

Erfunden wird dabei nichts. Die Reihenfolge der Schritte steht in den RFCs;
hier werden fertige Primitive in dieser Reihenfolge zusammengesetzt. Die
Testdatei haelt das gegen die Testvektoren aus RFC 8291 §5, damit ein
Tippfehler in einer `info`-Zeichenkette auffaellt und nicht erst beim
Empfaenger als stilles Nichts endet.

## Was der Push-Dienst erfaehrt

Alles, was hier hinausgeht, ist gegen den oeffentlichen Schluessel des
Browsers verschluesselt (RFC 8291). Google, Mozilla und Apple sehen Chiffrat,
nicht den Text — und der Text waere ohnehin nur „Neue Nachricht", weil
`NotificationService.sanitize_push_payload` jeden Inhalt vorher entfernt.

Was sie sehr wohl sehen: dass diese Endpunkt-Adresse zu diesem Zeitpunkt etwas
bekommen hat. Das ist der Preis dieses Weges, es laesst sich nicht wegbauen,
und es steht deshalb in der Datenschutzerklaerung und in `docs/self-hosting.md`
statt in einer Fussnote.

## SSRF

Die Endpunkt-Adresse kommt aus dem Browser, also von aussen. Ohne Schranke
koennte jemand `http://127.0.0.1:8000/...` eintragen und das Panel gegen sich
selbst POSTen lassen. `_ziel_ist_erlaubt` verlangt deshalb `https` und
aufloesbar oeffentliche Adressen — geprueft vor jedem Versand, nicht nur beim
Eintragen. Restrisiko benannt: zwischen Aufloesung und Verbindung bleibt ein
DNS-Rebind-Fenster. Was dort ankaeme, waere ein POST mit undurchsichtigem
Chiffrat ohne Panel-Cookie; das VAPID-Token nennt seinen Empfaenger im
`aud`-Anspruch und ist anderswo wertlos.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models import E2eeMailboxPush, PanelSetting, PushSubscription, User

logger = logging.getLogger(__name__)

SCHLUESSEL_PRIVAT = "webpush_vapid_private_encrypted"
SCHLUESSEL_OEFFENTLICH = "webpush_vapid_public"
VAPID_AAD = "msm:settings:webpush_vapid"

# Wie lange der Push-Dienst eine Meldung fuer ein gerade nicht erreichbares
# Geraet aufhebt. Eine Stunde: wer laenger weg war, liest die Nachricht beim
# Oeffnen ohnehin im Verlauf, und ein Stapel alter „Neue Nachricht"-Meldungen
# beim Einschalten ist keine Benachrichtigung mehr, sondern Laerm.
TTL_SEKUNDEN = 3600

# Feste Satzlaenge aus RFC 8188. Unsere Nutzlast ist ein kurzes JSON; alles,
# was hier nicht hineinpasst, ist ein Fehler und kein Grosstransport.
SATZ_LAENGE = 4096
MAX_NUTZLAST_BYTES = SATZ_LAENGE - 17  # Begrenzer (1) + GCM-Tag (16)

# Zustellung blockiert den Nachrichtenversand nicht: `relay_blind_envelope`
# laeuft synchron im Anfrage-Thread, und ein langsamer Push-Dienst darf den
# Absender nicht warten lassen. Vier Faeden reichen fuer menschliches Tempo und
# setzen zugleich eine Obergrenze — unbegrenzt viele waeren eine Fadenlawine bei
# einer grossen Gruppe.
_versender = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webpush")


# ── Base64url ohne Polster ──────────────────────────────────────────────────


def _b64e(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    rein = (text or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(rein + "=" * (-len(rein) % 4))


# ── VAPID-Schluesselpaar ────────────────────────────────────────────────────


def _neues_paar() -> tuple[str, str]:
    """Erzeugt ein P-256-Paar: (privates PEM, oeffentlicher Punkt base64url)."""
    privat = ec.generate_private_key(ec.SECP256R1())
    pem = privat.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    punkt = privat.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return pem, _b64e(punkt)


def _paar(db: Session) -> tuple[str, str] | None:
    """Das Schluesselpaar des Panels, beim ersten Gebrauch erzeugt.

    Gelesen wird absichtlich direkt aus der Tabelle und nicht ueber den Cache
    von `PanelSettingsService`: der Cache gilt je Prozess und kennt keine
    Invalidierung ueber Prozessgrenzen. Ein Arbeiter mit einem veralteten
    Schluessel wuerde mit einem Token signieren, das nicht zu dem passt, mit
    dem der Browser abonniert hat — der Push-Dienst antwortet dann 403, und
    zwar stillschweigend fuer jede Zustellung.

    Das Anlegen laeuft ueber den Primaerschluessel der Tabelle: zwei Arbeiter,
    die gleichzeitig starten, koennen nicht zwei Paare durchsetzen. Wer
    verliert, liest das Paar des Gewinners. Das ist wichtiger als es aussieht —
    ein gewechselter VAPID-Schluessel entwertet **jedes bestehende Abonnement**,
    weil der Browser es an den Schluessel gebunden hat, mit dem er abonniert hat.
    """
    from services.auth_service import AuthService

    privat_zeile = db.query(PanelSetting).filter_by(key=SCHLUESSEL_PRIVAT).first()
    oeff_zeile = db.query(PanelSetting).filter_by(key=SCHLUESSEL_OEFFENTLICH).first()
    if privat_zeile and oeff_zeile:
        try:
            return AuthService.decrypt_secret(privat_zeile.value, aad=VAPID_AAD), oeff_zeile.value
        except Exception:
            logger.error("webpush: VAPID-Schluessel nicht entschluesselbar, kein Versand")
            return None

    pem, oeffentlich = _neues_paar()
    try:
        db.add(PanelSetting(key=SCHLUESSEL_PRIVAT, value=AuthService.encrypt_secret(pem, aad=VAPID_AAD)))
        db.add(PanelSetting(key=SCHLUESSEL_OEFFENTLICH, value=oeffentlich))
        db.commit()
        logger.info("webpush: VAPID-Schluesselpaar erzeugt")
        return pem, oeffentlich
    except IntegrityError:
        # Ein anderer Arbeiter war schneller. Seins gilt.
        db.rollback()
    except Exception:
        db.rollback()
        logger.exception("webpush: VAPID-Schluesselpaar konnte nicht abgelegt werden")
        return None

    privat_zeile = db.query(PanelSetting).filter_by(key=SCHLUESSEL_PRIVAT).first()
    oeff_zeile = db.query(PanelSetting).filter_by(key=SCHLUESSEL_OEFFENTLICH).first()
    if not privat_zeile or not oeff_zeile:
        return None
    try:
        return AuthService.decrypt_secret(privat_zeile.value, aad=VAPID_AAD), oeff_zeile.value
    except Exception:
        logger.error("webpush: VAPID-Schluessel nicht entschluesselbar, kein Versand")
        return None


def oeffentlicher_schluessel(db: Session) -> str:
    """Der Wert, den der Browser als `applicationServerKey` braucht.

    Leer heisst: kein Versand moeglich. Der Client abonniert dann nicht, statt
    ein Abonnement anzulegen, das nie bedient wird.
    """
    paar = _paar(db)
    return paar[1] if paar else ""


def _vapid_kopf(endpunkt: str, privates_pem: str, oeffentlich: str) -> dict[str, str]:
    """Der `Authorization`-Kopf nach RFC 8292.

    `aud` ist der Ursprung genau dieses Push-Dienstes. Damit ist das Token
    nirgendwo sonst verwendbar — auch nicht, wenn eine eingetragene Adresse
    einmal woanders hinzeigt.
    """
    teile = urlsplit(endpunkt)
    anspruch = {
        "aud": f"{teile.scheme}://{teile.netloc}",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()),
        # RFC 8292 verlangt eine Kontaktangabe des Absenders. Die Panel-Adresse
        # ist die ehrlichste: sie sagt, wer sendet, ohne eine Mailadresse zu
        # erfinden, die niemand liest.
        "sub": (settings.panel_url or "https://localhost").rstrip("/"),
    }
    token = jwt.encode(anspruch, privates_pem, algorithm="ES256")
    return {"Authorization": f"vapid t={token}, k={oeffentlich}"}


# ── Nutzlast verschluesseln (RFC 8291 / RFC 8188) ───────────────────────────


def _hkdf(salz: bytes, ikm: bytes, info: bytes, laenge: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=laenge, salt=salz, info=info).derive(ikm)


def _verschluesseln(nutzlast: bytes, p256dh: str, auth: str, *, salz: bytes | None = None,
                    server_privat: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Ein `aes128gcm`-Koerper fuer genau diesen Browser.

    Die beiden Parameter am Ende gibt es nur fuer den Test gegen die
    Vektoren aus RFC 8291 §5; im Betrieb zieht die Funktion beides selbst.
    Deterministisch gemacht wird hier nichts — wer Salz oder Schluessel
    wiederverwendet, bricht AES-GCM.
    """
    if len(nutzlast) > MAX_NUTZLAST_BYTES:
        raise ValueError("Push-Nutzlast zu gross")

    browser_punkt = _b64d(p256dh)
    auth_geheimnis = _b64d(auth)
    browser_schluessel = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), browser_punkt
    )

    salz = salz or os.urandom(16)
    server_privat = server_privat or ec.generate_private_key(ec.SECP256R1())
    server_punkt = server_privat.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    gemeinsam = server_privat.exchange(ec.ECDH(), browser_schluessel)

    # RFC 8291 §3.4: das Authentifizierungsgeheimnis ist das Salz dieser Stufe,
    # nicht das Salz des Satzes. Die Reihenfolge der beiden Punkte ist
    # festgelegt — erst der Browser, dann der Server.
    schluessel_info = b"WebPush: info\x00" + browser_punkt + server_punkt
    ikm = _hkdf(auth_geheimnis, gemeinsam, schluessel_info, 32)

    # RFC 8188 §2.2. Die abschliessende Null gehoert zur `info` und ist kein
    # Trennzeichen zwischen Feldern.
    cek = _hkdf(salz, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salz, ikm, b"Content-Encoding: nonce\x00", 12)

    # Ein einziger Satz, deshalb `0x02` als Begrenzer (`0x01` hiesse: es folgt
    # noch einer).
    chiffrat = AESGCM(cek).encrypt(nonce, nutzlast + b"\x02", None)

    kopf = salz + struct.pack("!I", SATZ_LAENGE) + bytes([len(server_punkt)]) + server_punkt
    return kopf + chiffrat


# ── Zielpruefung ────────────────────────────────────────────────────────────


def _ziel_ist_erlaubt(endpunkt: str) -> bool:
    """`https` und eine oeffentlich geroutete Adresse — sonst nicht.

    Siehe Modulkopf: die Adresse kommt von aussen, und ohne diese Pruefung
    waere das Eintragen eines Abonnements ein Weg, das Panel gegen sein eigenes
    Netz POSTen zu lassen.
    """
    try:
        teile = urlsplit(endpunkt)
    except ValueError:
        return False
    if teile.scheme != "https" or not teile.hostname:
        return False

    try:
        aufgeloest = socket.getaddrinfo(teile.hostname, teile.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not aufgeloest:
        return False

    for eintrag in aufgeloest:
        try:
            adresse = ipaddress.ip_address(eintrag[4][0])
        except ValueError:
            return False
        if not adresse.is_global or adresse.is_multicast:
            return False
    return True


# ── Eintragen und austragen ─────────────────────────────────────────────────


def _geprueftes_ziel(endpoint: str, p256dh: str, auth: str) -> tuple[str, str, str]:
    """Adresse und Schluessel, so wie sie in eine Zeile gehoeren.

    Wirft 400, wenn etwas davon unbrauchbar ist. Fruehe Formpruefung mit Absicht:
    ein kaputter Punkt faellt beim Eintragen auf und nicht erst im
    Hintergrundfaden, wo ihn niemand sieht.
    """
    from fastapi import HTTPException

    sauber = (endpoint or "").strip()
    if not _ziel_ist_erlaubt(sauber):
        raise HTTPException(status_code=400, detail="Ungültige Push-Adresse.")

    sauberer_p256dh = (p256dh or "").strip()
    sauberes_auth = (auth or "").strip()
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64d(sauberer_p256dh))
        if len(_b64d(sauberes_auth)) != 16:
            raise ValueError("auth-Geheimnis hat nicht 16 Bytes")
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültige Push-Schlüssel.")

    return sauber, sauberer_p256dh, sauberes_auth


def endpunkt_abdruck(endpoint: str) -> str:
    """Der SHA-256 einer Zustelladresse, hex.

    Traegt die Eindeutigkeit in `e2ee_mailbox_push` und ist zugleich das, was
    ein Absender als `push_ausnahme` mitschickt. Der Client rechnet denselben
    Wert — deshalb wird hier nur getrimmt und nicht etwa normalisiert: eine
    Adresse ist byteweise das, was der Browser herausgegeben hat.
    """
    return hashlib.sha256((endpoint or "").strip().encode("utf-8")).hexdigest()


def eintragen(
    db: Session, user: User, *, endpoint: str, p256dh: str, auth: str, familie: str | None = None
) -> PushSubscription:
    """Legt die Zustelladresse dieses Browsers an oder uebernimmt sie.

    Uebernimmt, nicht verdoppelt: eine Endpunkt-Adresse gehoert zu einer
    Browserinstallation. Meldet sich in einem geteilten Browser ein anderes
    Konto an, wandert die Zeile mit — sonst bekaeme der neue Benutzer die
    Benachrichtigungen des vorherigen.
    """
    sauber, sauberer_p256dh, sauberes_auth = _geprueftes_ziel(endpoint, p256dh, auth)

    vorhanden = db.query(PushSubscription).filter_by(endpoint=sauber).first()
    if vorhanden:
        vorhanden.user_id = user.id
        vorhanden.p256dh = sauberer_p256dh
        vorhanden.auth = sauberes_auth
        vorhanden.auth_family = familie
        db.commit()
        return vorhanden

    abo = PushSubscription(
        user_id=user.id,
        endpoint=sauber,
        p256dh=sauberer_p256dh,
        auth=sauberes_auth,
        auth_family=familie,
        created_at=datetime.now(timezone.utc),
    )
    db.add(abo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        vorhanden = db.query(PushSubscription).filter_by(endpoint=sauber).first()
        if vorhanden:
            vorhanden.user_id = user.id
            vorhanden.auth_family = familie
            db.commit()
            return vorhanden
        raise
    return abo


def austragen_familie(db: Session, user_id: int, familie: str | None) -> int:
    """Entfernt die Zustelladressen einer gesperrten Sitzung. Gibt deren Zahl zurueck.

    Der Weg beim Aussperren eines Geraets: bis 09/2026 blieb seine Adresse
    stehen, und ein gestohlenes Geraet bekam weiter Benachrichtigungen. Mit
    der Kontozeile fallen auch die Mailbox-Zustellungen derselben Adresse;
    die kennen kein Konto, haengen aber am selben Browser.

    Ohne ``familie`` fallen alle Adressen des Kontos: das ist das Sperren
    aller Sitzungen (Passwort zurueckgesetzt, Abmelden ohne Familie).
    """
    abfrage = db.query(PushSubscription).filter(PushSubscription.user_id == user_id)
    if familie:
        abfrage = abfrage.filter(PushSubscription.auth_family == familie)
    zeilen = abfrage.all()
    for abo in zeilen:
        austragen_mailboxen(db, abo.endpoint)
        db.delete(abo)
    db.commit()
    return len(zeilen)


def austragen(db: Session, user: User, endpoint: str) -> bool:
    """Entfernt eine Zustelladresse. Nur die eigene."""
    sauber = (endpoint or "").strip()
    abo = db.query(PushSubscription).filter_by(endpoint=sauber, user_id=user.id).first()
    if not abo:
        return False
    db.delete(abo)
    db.commit()
    return True


# ── Dasselbe, aber ohne Konto: Zustelladressen je Mailbox ───────────────────


def eintragen_mailbox(db: Session, *, mailbox_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    """Merkt sich: in diese Mailbox faellt etwas, schick es an diese Adresse.

    Kein `user`-Parameter, und das ist der ganze Punkt. Die Sitzung, ueber die
    das hereinkommt, entscheidet im Router, **ob** eingetragen werden darf; in
    der Zeile bleibt von ihr nichts uebrig.

    Uebernimmt eine vorhandene Zeile, statt eine zweite anzulegen: gemeldet
    wird bei jeder Anmeldung (siehe `mailboxPush.ts`), und die Schluessel eines
    Browsers koennen sich dabei geaendert haben.
    """
    sauber, sauberer_p256dh, sauberes_auth = _geprueftes_ziel(endpoint, p256dh, auth)
    kennung = (mailbox_id or "").strip()
    if not kennung:
        return
    abdruck = endpunkt_abdruck(sauber)

    vorhanden = (
        db.query(E2eeMailboxPush).filter_by(mailbox_id=kennung, endpoint_hash=abdruck).first()
    )
    if vorhanden:
        vorhanden.endpoint = sauber
        vorhanden.p256dh = sauberer_p256dh
        vorhanden.auth = sauberes_auth
        db.commit()
        return

    db.add(
        E2eeMailboxPush(
            mailbox_id=kennung,
            endpoint_hash=abdruck,
            endpoint=sauber,
            p256dh=sauberer_p256dh,
            auth=sauberes_auth,
            created_at=datetime.now(timezone.utc),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Zwei Tabs derselben Anmeldung. Der andere war schneller.
        db.rollback()


def mailboxen_von(db: Session, endpoint: str) -> set[str]:
    """Wofuer dieser Browser heute eingetragen ist.

    Gebraucht beim Melden, um abzuraeumen, was nicht mehr genannt wird. Nur
    Kennungen, keine Adressen und keine Schluessel — wer fragt, hat die Adresse
    ohnehin schon.
    """
    sauber = (endpoint or "").strip()
    if not sauber:
        return set()
    abdruck = endpunkt_abdruck(sauber)
    zeilen = (
        db.query(E2eeMailboxPush.mailbox_id)
        .filter(E2eeMailboxPush.endpoint_hash == abdruck)
        .all()
    )
    return {z[0] for z in zeilen}


def austragen_mailboxen(db: Session, endpoint: str, *, nur: set[str] | None = None) -> int:
    """Entfernt die Zustellungen dieses Browsers. Gibt deren Zahl zurueck.

    Ohne `nur` faellt alles, was zu dieser Adresse gehoert — der Weg beim
    Abmelden. Mit `nur` fallen genau die genannten Mailboxen, der Weg, wenn der
    Client eine Gruppe verlassen hat.

    Geprueft wird hier **nichts** ausser dem Besitz der Adresse, und das mit
    Absicht: wer eine Endpunkt-Adresse hat, ist der Browser, um den es geht.
    Ein Konto daneben zu verlangen hiesse, die Zeile doch wieder einem Konto
    zuzuordnen. Die Adresse ist ein Geheimnis des Browsers und steht in keiner
    Antwort dieses Panels.
    """
    abdruck = endpunkt_abdruck(endpoint)
    if not (endpoint or "").strip():
        return 0
    abfrage = db.query(E2eeMailboxPush).filter(E2eeMailboxPush.endpoint_hash == abdruck)
    if nur is not None:
        sauber = {m.strip() for m in nur if (m or "").strip()}
        if not sauber:
            return 0
        abfrage = abfrage.filter(E2eeMailboxPush.mailbox_id.in_(sauber))
    entfernt = abfrage.delete(synchronize_session=False)
    db.commit()
    return int(entfernt or 0)


def sende_an_mailbox(
    db: Session,
    mailbox_id: str,
    nutzlast: dict[str, Any],
    *,
    ausser_abdruck: str | None = None,
) -> int:
    """Stellt `nutzlast` an alle Adressen dieser Mailbox zu. Gibt deren Zahl zurueck.

    Das Gegenstueck zu `sende_an_konto` fuer Mailboxen, die der Server niemandem
    zuordnen kann. Zwei Unterschiede, beide unvermeidlich:

    Es gibt **keinen `users.device_notifications`-Schalter** zu pruefen, denn es
    gibt kein Konto. Der Schalter wirkt trotzdem: der Client traegt gar nicht
    erst ein, solange er aus ist, und traegt beim Umlegen aus. Die Entscheidung
    liegt damit beim Geraet des Benutzers statt beim Server — was fuer einen
    Schalter ueber die eigenen Benachrichtigungen die richtige Seite ist.

    Und es gibt **keine Vordergrund-Pruefung** je Empfaenger: welche Adresse zu
    welcher offenen Verbindung gehoert, weiss hier niemand. Unterdrueckt wird
    stattdessen dort, wo die Frage beantwortbar ist — `sw.js` zeigt nichts an,
    solange ein Fenster den Fokus hat.

    `ausser_abdruck` haelt den absendenden Browser heraus. Dessen **andere**
    Geraete bekommen die Meldung: sie zu erkennen hiesse, sie wieder
    untereinander zu verknuepfen, und genau das soll hier nicht mehr gehen.
    """
    kennung = (mailbox_id or "").strip()
    if not kennung:
        return 0

    abfrage = db.query(E2eeMailboxPush).filter(E2eeMailboxPush.mailbox_id == kennung)
    if ausser_abdruck:
        abfrage = abfrage.filter(E2eeMailboxPush.endpoint_hash != ausser_abdruck.strip().lower())
    abos = abfrage.all()
    if not abos:
        return 0

    paar = _paar(db)
    if not paar:
        return 0
    privates_pem, oeffentlich = paar

    koerper = json.dumps(nutzlast, separators=(",", ":"), default=str).encode("utf-8")
    if len(koerper) > MAX_NUTZLAST_BYTES:
        logger.warning("webpush: Nutzlast zu gross (%d Bytes), kein Versand", len(koerper))
        return 0

    ziele = [(a.id, a.endpoint, a.p256dh, a.auth) for a in abos]
    _versender.submit(_zustellen_alle, ziele, koerper, privates_pem, oeffentlich, True)
    return len(ziele)


# ── Versand ─────────────────────────────────────────────────────────────────


def sende_an_konto(db: Session, user_id: int, nutzlast: dict[str, Any]) -> int:
    """Stellt `nutzlast` an alle Geraete dieses Kontos zu. Gibt deren Zahl zurueck.

    Kehrt sofort zurueck: der eigentliche Versand laeuft im Hintergrund, weil
    diese Funktion mitten im Nachrichtenrelais steht und ein langsamer
    Push-Dienst dort niemanden aufhalten darf. Die Zahl sagt deshalb „an so
    viele Geraete ging es hinaus", nicht „so viele haben es bekommen".

    Der Schalter `users.device_notifications` gilt hier genauso wie im
    Vordergrund. Wer Benachrichtigungen abgeschaltet hat, bekommt auch bei
    geschlossener Anwendung keine.
    """
    konto = db.query(User).filter_by(id=user_id).first()
    if not konto or not konto.is_active or not konto.device_notifications:
        return 0

    abos = db.query(PushSubscription).filter_by(user_id=user_id).all()
    if not abos:
        return 0

    paar = _paar(db)
    if not paar:
        return 0
    privates_pem, oeffentlich = paar

    koerper = json.dumps(nutzlast, separators=(",", ":"), default=str).encode("utf-8")
    if len(koerper) > MAX_NUTZLAST_BYTES:
        logger.warning("webpush: Nutzlast zu gross (%d Bytes), kein Versand", len(koerper))
        return 0

    # Reine Werte ueber die Fadengrenze, keine ORM-Objekte: die gehoeren zu
    # dieser Sitzung und waeren drueben abgeloest.
    ziele = [(a.id, a.endpoint, a.p256dh, a.auth) for a in abos]
    _versender.submit(_zustellen_alle, ziele, koerper, privates_pem, oeffentlich, False)
    return len(ziele)


def _zustellen_alle(
    ziele: list[tuple[int, str, str, str]],
    koerper: bytes,
    privates_pem: str,
    oeffentlich: str,
    je_mailbox: bool = False,
) -> None:
    """Laeuft im Hintergrundfaden. Faengt alles — hier gibt es niemanden mehr,
    dem ein Fehler auffallen wuerde, ausser dem Log.

    `je_mailbox` sagt nur, aus welcher Tabelle die `id` in `ziele` stammt. Beide
    Wege raeumen ihre toten Adressen selbst ab; eine tote Adresse in der einen
    Tabelle sagt nichts ueber die andere, weil dort andere Zeilen stehen.
    """
    tot: list[int] = []
    try:
        with httpx.Client(timeout=10.0) as client:
            for abo_id, endpunkt, p256dh, auth in ziele:
                if _zustellen(client, endpunkt, p256dh, auth, koerper, privates_pem, oeffentlich):
                    continue
                tot.append(abo_id)
    except Exception:
        logger.exception("webpush: Versand abgebrochen")

    if not tot:
        return
    db = SessionLocal()
    try:
        # Ein 404 oder 410 heisst: diesen Browser gibt es nicht mehr. Die Zeile
        # stehen zu lassen hiesse, bei jeder Nachricht erneut dagegen zu
        # laufen.
        modell = E2eeMailboxPush if je_mailbox else PushSubscription
        db.query(modell).filter(modell.id.in_(tot)).delete(synchronize_session=False)
        db.commit()
        logger.info("webpush: %d abgelaufene Abonnements entfernt", len(tot))
    except Exception:
        db.rollback()
        logger.exception("webpush: abgelaufene Abonnements nicht entfernbar")
    finally:
        db.close()


def _zustellen(
    client: httpx.Client,
    endpunkt: str,
    p256dh: str,
    auth: str,
    koerper: bytes,
    privates_pem: str,
    oeffentlich: str,
) -> bool:
    """Eine Zustellung. `False` heisst ausschliesslich: diese Adresse ist tot.

    Ein Netzwerkfehler oder eine 500 des Push-Dienstes sind `True` — sie sagen
    nichts ueber das Abonnement, und ein Abonnement wegen einer fremden
    Stoerung zu loeschen waere der teuerste moegliche Fehler: der Benutzer
    bekaeme nie wieder etwas und saehe nirgends warum.
    """
    if not _ziel_ist_erlaubt(endpunkt):
        logger.warning("webpush: Zustelladresse abgelehnt (kein oeffentliches https-Ziel)")
        return True

    try:
        verschluesselt = _verschluesseln(koerper, p256dh, auth)
        kopf = _vapid_kopf(endpunkt, privates_pem, oeffentlich)
    except Exception:
        # Ein kaputter Schluessel in der Zeile. Die Adresse ist damit unbenutzbar.
        logger.warning("webpush: Abonnement nicht verschluesselbar, wird entfernt")
        return False

    kopf.update(
        {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SEKUNDEN),
        }
    )

    try:
        antwort = client.post(endpunkt, content=verschluesselt, headers=kopf)
    except httpx.HTTPError:
        logger.warning("webpush: Push-Dienst nicht erreichbar")
        return True

    if antwort.status_code in (404, 410):
        return False
    if antwort.status_code >= 400:
        # Kein Antworttext ins Log: er kann die Endpunkt-Adresse enthalten.
        logger.warning("webpush: Push-Dienst antwortete %d", antwort.status_code)
    return True
