"""DIS Client — zentrale Fassade fuer alle Krypto-Operationen.

Kommuniziert mit dem lokalen DIS Sidecar (Node.js, @msdis/shield) ueber HTTP.
Das Panel selbst enthaelt keine eigene Kryptographie — alle Ver-/Entschluesselung,
Passwort-Hashing (Argon2id) und TOTP laufen ueber diesen Client und damit ueber
DIS.

Sicherheits-Invarianten:
- Fail-closed: wenn der Sidecar nicht erreichbar ist, schlagen Krypto-Operationen
  fehl. Es gibt KEINEN Fallback auf eigene Krypto.
- Plaintext-Daten werden nie geloggt.
- Der Sidecar lauscht nur auf 127.0.0.1; Bearer-Token verhindert Aufrufe durch
  andere lokale Prozesse.
"""
from __future__ import annotations

import httpx
from config import settings


class DisSidecarError(Exception):
    """Sidecar nicht erreichbar oder Fehler bei der Krypto-Operation."""


class DisZuGross(DisSidecarError):
    """Der Sidecar nimmt hoechstens 8 MiB JSON je Anfrage (`MAX_JSON_BODY`)."""


class DisKeinAltHash(DisSidecarError):
    """``/wrap-legacy-password`` kennt die Form nicht (nur passlib-Argon2id, v=19)."""


class DisDecryptionError(DisSidecarError):
    """Entschluesselung fehlgeschlagen (wrong key / tamper / AAD mismatch).

    Der Sidecar unterscheidet nicht zwischen diesen Ursachen (kein Oracle).
    """


# Fehlernamen, unter denen der Sidecar meldet, dass ein Ciphertext schon **vor**
# dem Entschluesseln unbrauchbar ist: "!!!" ist kein Base64 (atob wirft
# InvalidCharacterError), "AAAA" sind drei Bytes und damit kuerzer als der
# 12-Byte-IV (@msdis/shield wirft DisInvalidArgumentError, bevor es ueberhaupt
# zu entschluesseln beginnt).
#
# Warum die Abbildung hierhin gehoert und nicht zu jedem einzelnen Aufrufer:
# Ciphertexte kommen nicht nur aus der eigenen Datenbank, sondern auch von
# aussen - das OAuth-State-Cookie ist einer davon. Fuer den Aufrufer ist ein
# unbrauchbarer Ciphertext dasselbe wie ein falscher, beides heisst "der Wert
# taugt nicht". Ohne diese Abbildung schluepft der Fall als DisSidecarError an
# jedem `except DisDecryptionError` vorbei, denn DisDecryptionError ist dessen
# Unterklasse und nicht umgekehrt - aus einem abgeschnittenen Cookie wird so ein
# HTTP 500 statt einer Abweisung.
#
# Nur beim Entschluesseln: derselbe Fehlername beim Verschluesseln waere ein
# Fehler in unserem eigenen Aufruf und soll laut bleiben.
#
# Bewusst **nicht** hier: `DisUnsupportedFormatVersionError`, also ein Wert mit
# `msm-dis-v2:` oder spaeter. Er stammt von einem neueren Panel und ist nicht
# kaputt. Als DisDecryptionError gemeldet, hielten ihn einige Aufrufer fuer
# Altbestand im Klartext und schrieben ihn verschluesselt ueber.
_UNBRAUCHBARER_CIPHERTEXT = ("DisInvalidArgumentError", "InvalidCharacterError")

#: Womit jeder Wert aus `DisClient.encrypt` beginnt. Fehlt er, ist die Zeile
#: aelter als der Umschlag (oder gar kein DIS-Wert).
DIS_PRAEFIX = "msm-dis-v1:"


class DisClient:
    """Statische Fassade fuer DIS-Krypto-Operationen ueber den lokalen Sidecar."""

    _timeout = 15.0

    @staticmethod
    def _headers() -> dict[str, str]:
        if settings.dis_sidecar_token:
            return {"Authorization": f"Bearer {settings.dis_sidecar_token}"}
        return {}

    @staticmethod
    def _post(endpoint: str, payload: dict) -> dict:
        url = settings.dis_sidecar_url.rstrip("/") + endpoint
        try:
            resp = _client.post(url, json=payload, headers=DisClient._headers())
        except httpx.HTTPError as e:
            raise DisSidecarError(f"DIS Sidecar nicht erreichbar: {e}") from e
        if resp.status_code == 401:
            raise DisSidecarError("DIS Sidecar Auth fehlgeschlagen (Token falsch?)")
        if resp.status_code == 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error", "")
            # Der zweite Zweig ist der Grund fuer _UNBRAUCHBARER_CIPHERTEXT: ein
            # Wert, der nicht einmal die Form eines Ciphertext hat, ist fuer den
            # Aufrufer kein Serverfehler, sondern ein ungueltiger Wert.
            if err in ("DisDecryptionError", "DisIntegrityError") or (
                endpoint == "/decrypt" and err in _UNBRAUCHBARER_CIPHERTEXT
            ):
                raise DisDecryptionError("Entschluesselung fehlgeschlagen")
            if err == "DisLegacyHashError":
                raise DisKeinAltHash("Kein passlib-Argon2id-Hash")
            raise DisSidecarError(f"DIS Sidecar Fehler: {err or resp.status_code}")
        if resp.status_code == 413:
            raise DisZuGross("DIS Sidecar: Anfrage groesser als 8 MiB")
        if resp.status_code != 200:
            raise DisSidecarError(f"DIS Sidecar Fehler: HTTP {resp.status_code}")
        return resp.json()

    # ── Encryption (AES-256-GCM) ────────────────────────────────────────

    @staticmethod
    def encrypt(plaintext: str, aad: str | None = None) -> str:
        """Verschluesselt einen String mit DIS AES-256-GCM.

        Args:
            plaintext: Klartext.
            aad: Optionaler Context (Associated Authenticated Data) zum Binden
                 des Ciphertext an einen Context (verhindert Swap-Angriffe).

        Returns: ``msm-dis-v1:`` + Base64 (IV + encrypted + tag). Werte von
        vor dem 26.09.2026 stehen ohne Praefix in der Datenbank; ``decrypt``
        liest beide Formen.
        """
        payload: dict = {"plaintext": plaintext}
        if aad:
            payload["aad"] = aad
        chiffrat = DisClient._post("/encrypt", payload)["ciphertext"]
        # Ein Sidecar von vor dem 26.09.2026 liefert nacktes Base64. Das
        # hielte der Nachzug beim Start fuer Klartext und verschluesselte es
        # wieder und wieder; am 27.09. wuchs so ein Titel auf 9 MB.
        if not chiffrat.startswith(DIS_PRAEFIX):
            raise DisSidecarError("DIS Sidecar zu alt: Wert ohne msm-dis-v1:")
        return chiffrat

    @staticmethod
    def decrypt(ciphertext: str, aad: str | None = None) -> str:
        """Entschluesselt einen DIS-AES-256-GCM-Ciphertext.

        Raises DisDecryptionError bei falschem Key, Tampering, AAD-Mismatch oder
        einem Ciphertext, den der Sidecar gar nicht erst lesen kann (siehe
        _UNBRAUCHBARER_CIPHERTEXT).
        """
        payload: dict = {"ciphertext": ciphertext}
        if aad:
            payload["aad"] = aad
        return DisClient._post("/decrypt", payload)["plaintext"]

    @staticmethod
    def decrypt_many(items: list[tuple[str, str | None]]) -> list[str | None]:
        """Entschluesselt viele Werte in einem Aufruf.

        ``items`` sind Paare aus Ciphertext und AAD. Ein einzelner unlesbarer
        Wert kommt als ``None`` zurueck statt den ganzen Aufruf zu kippen;
        ein unbekanntes Format (``msm-dis-v2:``) scheitert laut wie bei
        ``decrypt``.
        """
        if not items:
            return []
        payload = {"items": [{"ciphertext": c, "aad": a or ""} for c, a in items]}
        return DisClient._post("/decrypt-many", payload)["plaintexts"]

    @staticmethod
    def blind_index(werte: list[str]) -> list[str]:
        """Gibt je Wert einen geheimen, festen Suchindex zurueck (HMAC, hex).

        Damit laesst sich ein verschluesselter Wert wiederfinden und eindeutig
        halten, ohne ihn lesbar abzulegen. Ohne den Schluessel des Sidecars
        laesst sich der Index nicht nachrechnen.
        """
        if not werte:
            return []
        return DisClient._post("/blind-index", {"values": werte})["indices"]

    #: Die Tests ersetzen `encrypt` durch eine eigene Form und setzen dann
    #: auch diesen Wert um. Deshalb steht er am Client und wird nicht als
    #: Modulkonstante gelesen.
    PRAEFIX = DIS_PRAEFIX

    @staticmethod
    def ist_verschluesselt(wert: str) -> bool:
        """Ob ``wert`` aus ``encrypt`` stammt und nicht Altbestand im Klartext ist."""
        return wert.startswith(DisClient.PRAEFIX)

    # ── Password Hashing (Argon2id) ──────────────────────────────────────

    @staticmethod
    def hash_password(password: str) -> str:
        """Hasht ein Passwort mit DIS Argon2id.

        Returns: Hash im Format msm-pw-v1:b64(salt):b64(hash):v2
        """
        return DisClient._post("/hash-password", {"password": password})["hash"]

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """Verifiziert ein Passwort gegen einen DIS-Hash (msm-pw-v1:...).

        Fuer legacy passlib-Hashes ($argon2...) siehe
        AuthService.verify_password (Migration-Pfad).
        """
        result = DisClient._post("/verify-password", {"password": password, "hash": stored_hash})
        return result.get("valid", False)

    @staticmethod
    def is_dis_hash(stored_hash: str) -> bool:
        """Prueft ob ein Hash im DIS-Format (msm-pw-v1:) ist."""
        return stored_hash.startswith("msm-pw-v1:")

    @staticmethod
    def wrap_legacy_password(passlib_hash: str) -> str:
        """Umhuellt einen passlib-Hash (``$argon2id$v=19$...``) mit DIS.

        Das Ergebnis beginnt mit ``msm-pw-v1:`` und endet auf
        ``:alt.<m>.<t>.<p>.<laenge>.<salz>``. Es prueft dasselbe Passwort wie
        der alte Hash, ohne dass der Klartext bekannt sein muss.
        """
        return DisClient._post("/wrap-legacy-password", {"hash": passlib_hash})["hash"]

    @staticmethod
    def braucht_neuen_hash(stored_hash: str) -> bool:
        """Ob der Hash beim naechsten Login neu erzeugt werden soll.

        Wahr fuer passlib-Hashes und fuer umhuellte (``:alt....``): nur ein
        frischer DIS-Hash endet auf ``:v2``.
        """
        return not (DisClient.is_dis_hash(stored_hash) and stored_hash.endswith(":v2"))

    # ── TOTP (2FA) ───────────────────────────────────────────────────────

    @staticmethod
    def generate_totp_secret() -> str:
        """Generiert ein neues Base32-TOTP-Secret (160-bit)."""
        return DisClient._post("/totp/generate-secret", {})["secret"]

    @staticmethod
    def verify_totp(secret: str, code: str) -> bool:
        """Verifiziert einen TOTP-Code gegen ein Secret (±30s Fenster)."""
        return DisClient._post("/totp/verify", {"secret": secret, "code": code}).get("valid", False)

    @staticmethod
    def build_totp_uri(issuer: str, label: str, secret: str) -> str:
        """Baut die otpauth://-URI fuer QR-Code-Generierung."""
        return DisClient._post("/totp/build-uri", {"issuer": issuer, "label": label, "secret": secret})["uri"]

    # ── ALTCHA (Proof-of-Work CAPTCHA) ──────────────────────────────────

    @staticmethod
    def create_altcha_challenge() -> dict:
        """Erzeugt eine frische ALTCHA Proof-of-Work Challenge ueber den DIS Sidecar."""
        return DisClient._post("/altcha/challenge", {})

    @staticmethod
    def verify_altcha(payload: str) -> bool:
        """Verifiziert eine geloeste ALTCHA Challenge ueber den DIS Sidecar."""
        try:
            resp = DisClient._post("/altcha/verify", {"payload": payload})
            return bool(resp.get("valid", False))
        except DisSidecarError:
            raise

    # ── Health ───────────────────────────────────────────────────────────

    @staticmethod
    def health_check() -> bool:
        """Prueft ob der Sidecar erreichbar ist."""
        try:
            url = settings.dis_sidecar_url.rstrip("/") + "/health"
            resp = _client.get(url, headers=DisClient._headers(), timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


# Ein gehaltener Client statt der Modulfunktion `httpx.post` je Aufruf: diese
# baut bei jedem Aufruf einen vollständigen httpx.Client auf, und dessen
# Konstruktor legt sofort einen SSL-Kontext an und liest dabei das komplette
# CA-Bündel — auch wenn das Ziel ein http://127.0.0.1 ist. Über diesen Weg läuft
# jede Krypto-Operation des Panels: jedes Passwort-Hashing, jede TOTP-Prüfung,
# jedes Lesen einer E-Mail-Adresse. Der Aufschlag fiel bisher vor jedem
# einzelnen Byte an den Sidecar an.
#
# Dasselbe Muster hält node_client.py mit `get_shared_sync_client` schon vor;
# httpx.Client ist threadsicher und passt damit zu den Aufrufen aus
# `asyncio.to_thread`. **Auf diese Zusage stützt sich inzwischen mehr als das
# Wiederverwenden:** `ai_memory_service._entschluesseln_nebenlaeufig` schickt
# beim Aufbau des Gedächtnisblocks mehrere `decrypt` gleichzeitig hier hinein,
# weil deren Zahl sonst eins zu eins in der Wartezeit des Benutzers landet. Wer
# diesen einen Client durch etwas ersetzt, das nur ein Aufrufer zur Zeit
# verträgt, nimmt dem Gedächtnis damit still die Nebenläufigkeit weg.
# Der Preis der Wiederverwendung: eine im Pool wartende
# Keep-alive-Verbindung überlebt einen Neustart des Sidecars nicht. Der
# betroffene Aufruf fällt dann in `except httpx.HTTPError` und damit
# fail-closed — richtig, aber einmal spürbar.
_client = httpx.Client(timeout=DisClient._timeout)
