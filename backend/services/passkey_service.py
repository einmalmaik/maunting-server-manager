"""Passkeys als zweiter Faktor — geprueft auf dem Server (WebAuthn Level 3).

Bis 09/2026 war der Passkey eine reine Browser-Abfrage. Der Server glaubte dem
Feld ``passkey_verified: true``, das danach kam — und das konnte jeder
schicken, der das Passwort kannte. Das galt beim Login fuer **jedes** Konto mit
2FA, auch fuer eines mit Authenticator-App.

Jetzt laeuft es wie in der Spezifikation:

1. Der Server legt eine Einmal-Challenge an (``login_challenges``, fuenf
   Minuten, gebunden an Konto, Host und Zweck).
2. Der Browser laesst sie vom Authenticator unterschreiben.
3. Der Server prueft Challenge, Herkunft, RP-ID-Hash, Nutzerpruefung und die
   Unterschrift gegen den gespeicherten oeffentlichen Schluessel.

Ohne Bibliothek (siehe ``docs/agent-rules/adr-0009-webauthn-ohne-bibliothek.md``):
Beim Anlegen liest der Browser den Schluessel selbst aus
(``AuthenticatorAttestationResponse.getPublicKey()``, SPKI/DER) und schickt
``getAuthenticatorData()`` mit. Damit entfaellt CBOR. Attestation wird nicht
verlangt (``"none"``): welches Geraet den Schluessel haelt, ist hier egal —
entscheidend ist, dass es spaeter derselbe Schluessel ist.

Abschnittsverweise beziehen sich auf https://www.w3.org/TR/webauthn-3/.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from sqlalchemy.orm import Session

from config import TAURI_ORIGINS, get_cors_origins, settings
from models import LoginChallenge, User, UserPasskey
from services import login_challenge_service


# Wofuer ein Nachweis gilt. Die Challenge traegt den Zweck; ein Nachweis fuer
# „Geraet koppeln" oeffnet deshalb nicht „2FA ausschalten". Die angemeldeten
# Zwecke fordert der Benutzer selbst an (`/auth/passkey/options`); Login und
# OAuth bekommen ihre Optionen aus dem jeweiligen Anmeldeschritt.
ZWECKE_ANGEMELDET = frozenset({
    "2fa_disable", "device_pairing", "e2ee_reset",
    "password_change", "email_change", "account_delete", "data_export",
})
ZWECKE = ZWECKE_ANGEMELDET | {"login", "oauth_2fa"}

_ZWECK_ANLEGEN = "passkey_register"
_ZWECK_PRUEFEN = "passkey_auth"
_ALGORITHMEN = (-7, -8, -257)  # ES256, EdDSA, RS256 — in dieser Vorliebe
_TIMEOUT_MS = 120_000
_MAX_CREDENTIAL_ID = 1023  # §5.1: credential ID hoechstens 1023 Byte

_FLAG_UP = 0x01
_FLAG_UV = 0x04
_FLAG_BE = 0x08
_FLAG_BS = 0x10
_FLAG_AT = 0x40


class PasskeyFehler(Exception):
    """Der Nachweis gilt nicht. Die Meldung darf der Benutzer sehen."""


class KeinPasskeyFuerHost(PasskeyFehler):
    """Das Konto hat keinen Passkey, der unter dieser Adresse gilt."""


# ── Kodierung ────────────────────────────────────────────────────────────


def b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def _entb64url(text: Any, feld: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise PasskeyFehler(f"Passkey-Antwort unvollständig ({feld}).")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as e:
        raise PasskeyFehler(f"Passkey-Antwort unlesbar ({feld}).") from e


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


# ── Herkunft ─────────────────────────────────────────────────────────────


def _herkuenfte() -> list[str]:
    """Die CORS-Liste ohne die lokalen Entwicklungsports, ausser im Debug.

    `TAURI_ORIGINS` enthaelt bedingungslos auch `http://localhost:3000` und
    Verwandte. Fuer CORS ist das harmlos, fuer Passkeys nicht: jede Seite, die
    auf dem Rechner des Benutzers unter localhost laeuft, galte sonst als
    Herkunft fuer Passkeys mit RP-ID `localhost` (Desktop-App).
    """
    lokal = {o for o in TAURI_ORIGINS if o.startswith(("http://localhost", "http://127.0.0.1"))}
    return [o for o in get_cors_origins() if settings.debug or o not in lokal]


def _erlaubte_herkunft(origin: str | None) -> str:
    """Die Herkunft, falls das Panel sie kennt; sonst ein Fehler.

    Die Liste ist dieselbe wie fuer CORS: nur von dort kann ein Browser das
    Panel mit Anmeldung ueberhaupt ansprechen.
    """
    wert = (origin or "").strip().rstrip("/")
    if not wert or wert not in _herkuenfte():
        raise PasskeyFehler("Passkeys gehen nur über die Adresse des Panels.")
    return wert


def rp_id_fuer(origin: str | None) -> str:
    """Die RP-ID ist der Host der Seite, auf der der Passkey benutzt wird (§5.1.2)."""
    host = urlsplit(_erlaubte_herkunft(origin)).hostname
    if not host:
        raise PasskeyFehler("Passkeys gehen nur über die Adresse des Panels.")
    return host


# ── Einmal-Challenge ─────────────────────────────────────────────────────


def _challenge_anlegen(db: Session, user: User, zweck_intern: str, payload: dict) -> str:
    # `token_urlsafe(32)` ist bereits Base64url von 32 Zufallsbytes: genau so
    # steht die Challenge spaeter in `clientDataJSON` (§5.8.1).
    return login_challenge_service.create_challenge(
        db, purpose=zweck_intern, user_id=user.id, payload=payload
    )


def _challenge_verbrauchen(db: Session, user: User, challenge: Any, zweck_intern: str) -> dict:
    """Sucht die Challenge und verbraucht sie im selben Schritt.

    Pruefen und Verbrauchen in einem UPDATE: zwei gleichzeitige Anfragen mit
    derselben Challenge koennen nicht beide durchkommen. Verbraucht wird auch,
    wenn die Pruefung danach scheitert — ein zweiter Versuch holt sich eine
    neue Challenge.
    """
    if not isinstance(challenge, str):
        raise PasskeyFehler("Passkey-Bestätigung abgelaufen. Bitte neu starten.")
    row = login_challenge_service.lookup_valid(db, challenge, zweck_intern)
    if row is None or row.user_id != user.id:
        raise PasskeyFehler("Passkey-Bestätigung abgelaufen. Bitte neu starten.")
    getroffen = (
        db.query(LoginChallenge)
        .filter(LoginChallenge.id == row.id, LoginChallenge.consumed_at.is_(None))
        .update({LoginChallenge.consumed_at: _jetzt()}, synchronize_session=False)
    )
    db.commit()
    if getroffen != 1:
        raise PasskeyFehler("Passkey-Bestätigung abgelaufen. Bitte neu starten.")
    return json.loads(row.payload_json) if row.payload_json else {}


# ── Gemeinsame Pruefschritte ─────────────────────────────────────────────


def _client_data(antwort: dict, typ: str) -> tuple[bytes, dict]:
    """§7.1 Schritte 5–10 / §7.2 Schritte 10–13: Typ und Herkunft."""
    roh = _entb64url(antwort.get("clientDataJSON"), "clientDataJSON")
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PasskeyFehler("Passkey-Antwort unlesbar (clientDataJSON).") from e
    if not isinstance(daten, dict) or daten.get("type") != typ:
        raise PasskeyFehler("Passkey-Antwort passt nicht zum Vorgang.")
    # Eingebettet in eine fremde Seite (iframe) wird nicht angenommen — das
    # Panel erwartet keine Einbettung (§7.1 Schritt 10–11, §7.2 Schritt 14–15).
    if daten.get("crossOrigin") is True or "topOrigin" in daten:
        raise PasskeyFehler("Passkeys gehen nur über die Adresse des Panels.")
    return roh, daten


def _auth_data_kopf(auth_data: bytes, rp_id: str) -> tuple[int, int]:
    """§6.1: rpIdHash, Flags, Zaehler. Verlangt Anwesenheit **und** Nutzerpruefung."""
    if len(auth_data) < 37:
        raise PasskeyFehler("Passkey-Antwort unvollständig (authenticatorData).")
    if auth_data[:32] != hashlib.sha256(rp_id.encode("ascii")).digest():
        raise PasskeyFehler("Passkey gehört zu einer anderen Adresse.")
    flags = auth_data[32]
    if not flags & _FLAG_UP:
        raise PasskeyFehler("Passkey-Bestätigung ohne Anwesenheit.")
    # Ein zweiter Faktor, der nur „jemand hat den Stick beruehrt" belegt, waere
    # schwaecher als der TOTP-Code, den er ersetzt. Deshalb Fingerabdruck,
    # Gesicht oder PIN.
    if not flags & _FLAG_UV:
        raise PasskeyFehler("Passkey-Bestätigung ohne Fingerabdruck, Gesicht oder PIN.")
    # „If the BE bit of the flags in authData is not set, verify that the BS bit is not set."
    if flags & _FLAG_BS and not flags & _FLAG_BE:
        raise PasskeyFehler("Passkey-Antwort widersprüchlich.")
    return flags, int.from_bytes(auth_data[33:37], "big")


def _herkunft_passt(daten: dict, rp_id: str) -> None:
    origin = daten.get("origin")
    if not isinstance(origin, str) or rp_id_fuer(origin) != rp_id:
        raise PasskeyFehler("Passkeys gehen nur über die Adresse des Panels.")


# ── Anlegen (§7.1) ───────────────────────────────────────────────────────


def anlege_optionen(db: Session, user: User, origin: str | None) -> dict:
    """`PublicKeyCredentialCreationOptions` als JSON (Binaerfelder Base64url)."""
    rp_id = rp_id_fuer(origin)
    challenge = _challenge_anlegen(db, user, _ZWECK_ANLEGEN, {"rp_id": rp_id})
    vorhandene = [p for p in user.passkeys if p.rp_id == rp_id]
    return {
        "challenge": challenge,
        "rp": {"name": "Maunting Service Manager", "id": rp_id},
        "user": {
            "id": _user_handle(user),
            "name": user.username,
            "displayName": user.username,
        },
        "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in _ALGORITHMEN],
        "timeout": _TIMEOUT_MS,
        "attestation": "none",
        "authenticatorSelection": {
            "userVerification": "required",
            "residentKey": "preferred",
        },
        "excludeCredentials": [
            {"type": "public-key", "id": p.credential_id} for p in vorhandene
        ],
    }


def _user_handle(user: User) -> str:
    """Die Kennung des Kontos im Passkey (`user.id`, §5.4.3) — ohne Name oder Mail."""
    return b64url(f"msm-user-{user.id}".encode("ascii"))


def _schluessel_laden(spki: bytes, alg: int):
    try:
        schluessel = load_der_public_key(spki)
    except (ValueError, TypeError) as e:
        raise PasskeyFehler("Passkey-Schlüssel unlesbar.") from e
    passt = (
        (alg == -7 and isinstance(schluessel, ec.EllipticCurvePublicKey)
         and isinstance(schluessel.curve, ec.SECP256R1))
        or (alg == -8 and isinstance(schluessel, ed25519.Ed25519PublicKey))
        or (alg == -257 and isinstance(schluessel, rsa.RSAPublicKey) and schluessel.key_size >= 2048)
    )
    if not passt:
        raise PasskeyFehler("Dieser Passkey-Typ wird nicht unterstützt.")
    return schluessel


def anlegen(db: Session, user: User, antwort: dict) -> UserPasskey:
    """Prueft die Antwort von `navigator.credentials.create()` und speichert den Schluessel.

    Committet nicht: der Aufrufer schaltet im selben Zug 2FA ein.
    """
    inhalt = antwort.get("response")
    if antwort.get("type") != "public-key" or not isinstance(inhalt, dict):
        raise PasskeyFehler("Passkey-Antwort unvollständig.")
    _, daten = _client_data(inhalt, "webauthn.create")
    payload = _challenge_verbrauchen(db, user, daten.get("challenge"), _ZWECK_ANLEGEN)
    rp_id = payload.get("rp_id") or ""
    _herkunft_passt(daten, rp_id)

    auth_data = _entb64url(inhalt.get("authenticatorData"), "authenticatorData")
    flags, zaehler = _auth_data_kopf(auth_data, rp_id)
    if not flags & _FLAG_AT or len(auth_data) < 55:
        raise PasskeyFehler("Passkey-Antwort ohne Schlüssel.")
    laenge = int.from_bytes(auth_data[53:55], "big")
    credential_id = auth_data[55:55 + laenge]
    if not credential_id or laenge > _MAX_CREDENTIAL_ID or len(credential_id) != laenge:
        raise PasskeyFehler("Passkey-Antwort ohne gültige Kennung.")
    if _entb64url(antwort.get("rawId"), "rawId") != credential_id:
        raise PasskeyFehler("Passkey-Antwort widersprüchlich.")

    alg = inhalt.get("publicKeyAlgorithm")
    if not isinstance(alg, int) or alg not in _ALGORITHMEN:
        raise PasskeyFehler("Dieser Passkey-Typ wird nicht unterstützt.")
    spki = _entb64url(inhalt.get("publicKey"), "publicKey")
    _schluessel_laden(spki, alg)

    kennung = b64url(credential_id)
    # §7.1 Schritt 25: eine Kennung, die schon einem Konto gehoert, wird abgelehnt.
    if db.query(UserPasskey).filter(UserPasskey.credential_id == kennung).first() is not None:
        raise PasskeyFehler("Dieser Passkey ist bereits eingerichtet.")

    transports = inhalt.get("transports")
    erlaubt = {"usb", "nfc", "ble", "smart-card", "hybrid", "internal"}
    wege = [t for t in transports if t in erlaubt] if isinstance(transports, list) else []
    passkey = UserPasskey(
        user_id=user.id,
        credential_id=kennung,
        public_key=b64url(spki),
        algorithm=alg,
        rp_id=rp_id,
        sign_count=zaehler,
        transports=",".join(wege) or None,
    )
    db.add(passkey)
    return passkey


# ── Bestaetigen (§7.2) ───────────────────────────────────────────────────


def bestaetigungs_optionen(db: Session, user: User, origin: str | None, zweck: str) -> dict:
    """`PublicKeyCredentialRequestOptions` fuer genau dieses Konto, diesen Host, diesen Zweck."""
    if zweck not in ZWECKE:
        raise PasskeyFehler("Unbekannter Vorgang.")
    rp_id = rp_id_fuer(origin)
    passende = [p for p in user.passkeys if p.rp_id == rp_id]
    if not passende:
        raise KeinPasskeyFuerHost(
            "Für diese Adresse ist kein Passkey eingerichtet. Nimm einen Backup-Code "
            "oder bestätige dort, wo du den Passkey angelegt hast."
        )
    challenge = _challenge_anlegen(db, user, _ZWECK_PRUEFEN, {"rp_id": rp_id, "zweck": zweck})
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "timeout": _TIMEOUT_MS,
        "userVerification": "required",
        "allowCredentials": [
            {
                "type": "public-key",
                "id": p.credential_id,
                **({"transports": p.transports.split(",")} if p.transports else {}),
            }
            for p in passende
        ],
    }


def _unterschrift_pruefen(passkey: UserPasskey, signatur: bytes, daten: bytes) -> None:
    schluessel = _schluessel_laden(_entb64url(passkey.public_key, "publicKey"), passkey.algorithm)
    try:
        if passkey.algorithm == -7:
            schluessel.verify(signatur, daten, ec.ECDSA(hashes.SHA256()))
        elif passkey.algorithm == -8:
            schluessel.verify(signatur, daten)
        else:
            schluessel.verify(signatur, daten, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise PasskeyFehler("Passkey-Unterschrift ungültig.") from e


def bestaetigen(db: Session, user: User, antwort: dict | None, zweck: str) -> UserPasskey:
    """Prueft die Antwort von `navigator.credentials.get()`. Wirft `PasskeyFehler`.

    Committet selbst (Zaehler und letzte Nutzung), damit ein gelungener
    Nachweis auch dann verbucht ist, wenn der Aufrufer danach scheitert.
    """
    if not isinstance(antwort, dict):
        raise PasskeyFehler("Passkey-Bestätigung fehlt.")
    if antwort.get("type") == "browser":
        return _browser_einloesen(db, user, antwort.get("vorgang"), zweck)
    inhalt = antwort.get("response")
    if antwort.get("type") != "public-key" or not isinstance(inhalt, dict):
        raise PasskeyFehler("Passkey-Antwort unvollständig.")
    roh_client, daten = _client_data(inhalt, "webauthn.get")
    payload = _challenge_verbrauchen(db, user, daten.get("challenge"), _ZWECK_PRUEFEN)
    if payload.get("zweck") != zweck:
        raise PasskeyFehler("Passkey-Bestätigung gehört zu einem anderen Vorgang.")

    kennung = b64url(_entb64url(antwort.get("rawId"), "rawId"))
    # §7.2 Schritte 5–6: nur ein Passkey dieses Kontos.
    passkey = (
        db.query(UserPasskey)
        .filter(UserPasskey.credential_id == kennung, UserPasskey.user_id == user.id)
        .first()
    )
    if passkey is None or passkey.rp_id != payload.get("rp_id"):
        raise PasskeyFehler("Dieser Passkey gehört nicht zu deinem Konto.")
    # §7.2 Schritt 6: meldet der Passkey ein Konto, muss es dieses sein.
    handle = inhalt.get("userHandle")
    if handle and handle != _user_handle(user):
        raise PasskeyFehler("Dieser Passkey gehört nicht zu deinem Konto.")
    _herkunft_passt(daten, passkey.rp_id)

    auth_data = _entb64url(inhalt.get("authenticatorData"), "authenticatorData")
    _, zaehler = _auth_data_kopf(auth_data, passkey.rp_id)
    signatur = _entb64url(inhalt.get("signature"), "signature")
    _unterschrift_pruefen(passkey, signatur, auth_data + hashlib.sha256(roh_client).digest())

    # §7.2 Schritt 22: Ein Zaehler, der nicht waechst, deutet auf eine Kopie des
    # Schluessels. Viele Plattform-Passkeys zaehlen gar nicht (immer 0) — dann
    # gibt es nichts zu vergleichen.
    if (zaehler or passkey.sign_count) and zaehler <= passkey.sign_count:
        raise PasskeyFehler("Passkey-Zähler passt nicht. Der Passkey wurde womöglich kopiert.")
    passkey.sign_count = zaehler
    passkey.last_used_at = _jetzt()
    db.commit()
    return passkey


# ── Der zweite Faktor, wie ihn die Endpunkte brauchen ───────────────────


def zweiter_faktor_bestaetigt(
    db: Session, user: User, *, otp_code: str | None, passkey: dict | None, zweck: str
) -> bool:
    """Genau der Weg, den das Konto eingerichtet hat — der andere zaehlt nicht.

    TOTP-Konto: nur der aktuelle Code. Passkey-Konto: nur der Passkey. Frueher
    oeffnete der Passkey-Weg auch TOTP-Konten, weil er gar nichts pruefte.
    """
    from services.auth_service import AuthService

    methode = user.two_factor_method
    if methode == "totp":
        return bool(otp_code) and AuthService.verify_current_2fa_code(user, otp_code)
    if methode == "passkey":
        if passkey is None:
            return False
        try:
            bestaetigen(db, user, passkey, zweck)
        except PasskeyFehler:
            return False
        return True
    return False


# ── Bestaetigen im Browser (fuer die Desktop-App) ─────────────────────────
#
# Die App laeuft unter `tauri.localhost`, der Passkey gilt fuer die Adresse
# des Panels. Die App legt deshalb einen Vorgang an und oeffnet das Panel im
# Browser; dort bestaetigt der Passkey unter seiner eigenen Adresse. Die App
# reicht danach nur die Kennung weiter, `bestaetigen` loest sie ein.
#
# Gegen Phishing (jemand mit gestohlenem Token schickt dem Opfer den Link)
# zeigt die App eine Zahl, die im Browser aus dreien gewaehlt wird. Eine
# falsche Wahl verbraucht den Vorgang. Die Kennung steht im Fragment der
# Adresse und landet so in keinem Log.

_ZWECK_BROWSER = "passkey_browser"


def _browser_vorgang(db: Session, vorgang: Any) -> tuple[LoginChallenge, dict]:
    if not isinstance(vorgang, str):
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")
    row = login_challenge_service.lookup_valid(db, vorgang, _ZWECK_BROWSER)
    if row is None:
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")
    return row, json.loads(row.payload_json or "{}")


def _vorgang_umschreiben(db: Session, row: LoginChallenge, werte: dict) -> bool:
    """Schreibt nur, wenn der Vorgang noch so steht wie gelesen (ein UPDATE)."""
    getroffen = (
        db.query(LoginChallenge)
        .filter(
            LoginChallenge.id == row.id,
            LoginChallenge.consumed_at.is_(None),
            LoginChallenge.payload_json == row.payload_json,
            LoginChallenge.expires_at > _jetzt(),
        )
        .update(werte, synchronize_session=False)
    )
    db.commit()
    return getroffen == 1


def browser_vorgang_anlegen(db: Session, user: User, zweck: str) -> dict:
    """Legt den Vorgang an. Die Kennung und die Zahl sieht nur die App."""
    if zweck not in ZWECKE_ANGEMELDET:
        raise PasskeyFehler("Unbekannter Vorgang.")
    if user.two_factor_method != "passkey":
        raise PasskeyFehler("Dieses Konto bestätigt nicht mit Passkey.")
    zahlen = secrets.SystemRandom().sample(range(10, 100), 3)
    vorgang = login_challenge_service.create_challenge(
        db,
        purpose=_ZWECK_BROWSER,
        user_id=user.id,
        payload={"zweck": zweck, "zahl": zahlen[0], "auswahl": sorted(zahlen), "bestaetigt": False},
    )
    return {"vorgang": vorgang, "zahl": zahlen[0]}


def browser_optionen(db: Session, vorgang: Any, origin: str | None) -> dict:
    """Was die Seite im Browser zeigt: Zweck, drei Zahlen, WebAuthn-Optionen."""
    row, payload = _browser_vorgang(db, vorgang)
    if payload.get("bestaetigt"):
        raise PasskeyFehler("Schon bestätigt. Du kannst zur App zurückkehren.")
    user = db.get(User, row.user_id)
    if user is None:
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")
    return {
        "zweck": payload["zweck"],
        "auswahl": payload["auswahl"],
        "optionen": bestaetigungs_optionen(db, user, origin, payload["zweck"]),
    }


def browser_bestaetigen(db: Session, vorgang: Any, zahl: int, antwort: dict) -> None:
    row, payload = _browser_vorgang(db, vorgang)
    if payload.get("bestaetigt"):
        raise PasskeyFehler("Schon bestätigt. Du kannst zur App zurückkehren.")
    if zahl != payload.get("zahl"):
        _vorgang_umschreiben(db, row, {LoginChallenge.consumed_at: _jetzt()})
        raise PasskeyFehler("Das war nicht die Zahl aus der App. Starte den Vorgang in der App neu.")
    user = db.get(User, row.user_id)
    if user is None:
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")
    passkey = bestaetigen(db, user, antwort, payload["zweck"])
    bestaetigt = json.dumps({**payload, "bestaetigt": True, "passkey_id": passkey.id})
    if not _vorgang_umschreiben(db, row, {LoginChallenge.payload_json: bestaetigt}):
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")


def browser_stand(db: Session, user: User, vorgang: Any) -> str:
    """`offen`, `bestaetigt` oder `verfallen` (abgelaufen, verbraucht, fremd)."""
    try:
        row, payload = _browser_vorgang(db, vorgang)
    except PasskeyFehler:
        return "verfallen"
    if row.user_id != user.id:
        return "verfallen"
    return "bestaetigt" if payload.get("bestaetigt") else "offen"


def _browser_einloesen(db: Session, user: User, vorgang: Any, zweck: str) -> UserPasskey:
    """Loest eine Bestaetigung aus dem Browser ein: einmal, fuer dieses Konto und diesen Zweck."""
    if zweck not in ZWECKE_ANGEMELDET:
        raise PasskeyFehler("Diese Bestätigung gilt hier nicht.")
    row, payload = _browser_vorgang(db, vorgang)
    if row.user_id != user.id or payload.get("zweck") != zweck or not payload.get("bestaetigt"):
        raise PasskeyFehler("Bitte zuerst im Browser bestätigen.")
    if not _vorgang_umschreiben(db, row, {LoginChallenge.consumed_at: _jetzt()}):
        raise PasskeyFehler("Bestätigung abgelaufen. Bitte in der App neu starten.")
    passkey = (
        db.query(UserPasskey)
        .filter(UserPasskey.id == payload.get("passkey_id"), UserPasskey.user_id == user.id)
        .first()
    )
    if passkey is None:
        raise PasskeyFehler("Dieser Passkey gehört nicht mehr zu deinem Konto.")
    return passkey


__all__ = [
    "browser_vorgang_anlegen",
    "browser_optionen",
    "browser_bestaetigen",
    "browser_stand",
    "ZWECKE",
    "ZWECKE_ANGEMELDET",
    "PasskeyFehler",
    "KeinPasskeyFuerHost",
    "rp_id_fuer",
    "anlege_optionen",
    "anlegen",
    "bestaetigungs_optionen",
    "bestaetigen",
    "zweiter_faktor_bestaetigt",
]
