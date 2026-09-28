# ADR-0009: WebAuthn-Prüfung ohne `webauthn`-Bibliothek

Status: Accepted
Date: 2026-09-27

## Context
„2FA per Passkey“ war bis 09/2026 eine Abfrage im Browser. Danach ging `passkey_verified: true` an den Server, und der Server glaubte dem Feld. Wer das Passwort kannte, setzte das Feld selbst und übersprang die 2FA jedes Kontos. Betroffen waren Login, OAuth-Login, Gerätekopplung, das Abschalten der 2FA und der E2EE-Geräte-Reset.

Die Lösung ist eine echte WebAuthn-Relying-Party auf dem Server. Zur Wahl standen `webauthn` (py_webauthn, Duo Labs) und eigener Code auf `cryptography`, das schon im Stack ist.

## Decision
Wir prüfen WebAuthn selbst in `backend/services/passkey_service.py`. Dafür nutzen wir nur `cryptography` für die Signaturen und die vorhandene Tabelle `login_challenges` für die Einmal-Challenges. Eine neue Abhängigkeit kommt nicht dazu.

Diese Entscheidung erfindet kein Protokoll. Umgesetzt sind die Prüfschritte aus W3C WebAuthn Level 3, §7.1 „Registering a New Credential“ und §7.2 „Verifying an Authentication Assertion“. Die Kommentare im Code nennen die Schritte.

## Begründung
- **Kein CBOR.** Beim Anlegen liefert der Browser den Schlüssel selbst als SPKI/DER (`AuthenticatorAttestationResponse.getPublicKey()`) und die Authenticator-Daten über `getAuthenticatorData()`. Laut MDN ist das seit Oktober 2023 in allen Browsern verfügbar („Baseline widely available“). Attestation wird nicht verlangt (`"none"`). Damit entfällt der Teil, für den eine Bibliothek am meisten Code spart: CBOR/COSE parsen und Attestation-Formate prüfen.
- **Supply-Chain.** py_webauthn zieht `cbor2`, `asn1crypto` und `pyOpenSSL` nach. Das wären drei Pakete mehr in einem Auth-Pfad, und jedes Update wäre ein Sicherheitsrisiko. Gleiche Abwägung wie in ADR-0007.
- **Kleine Fläche.** Übrig bleiben rund 150 Zeilen, die sich linear lesen lassen: Client-Daten (Typ, Challenge, Herkunft, keine Einbettung), Authenticator-Daten (RP-ID-Hash, UP, UV, BE/BS), Signatur, Zähler und userHandle.
- **Tests.** `backend/tests/test_passkey_2fa.py` baut einen Authenticator in Software nach und prüft jeden Ablehnungsgrund einzeln: fremder Schlüssel, fehlende Nutzerprüfung, fremde Herkunft, fremder RP-Hash, Einbettung, fremdes Konto, rückläufiger Zähler, Wiederholung und falscher Zweck. Zusätzlich lief der ganze Ablauf am 27.09.2026 in echtem Chrome mit virtuellem Authenticator (CDP `WebAuthn.addVirtualAuthenticator`): einrichten, koppeln, abmelden, mit Passkey anmelden, abschalten.

## Konsequenzen
- **Keine Attestation.** Welches Gerät den Schlüssel hält, prüfen wir nicht. Für einen zweiten Faktor, den der angemeldete Benutzer selbst einrichtet, genügt das. Soll später eine Geräteklasse verlangt werden, ist das der Anlass, diese ADR neu zu bewerten.
- **Ein Passkey gilt nur unter seiner Adresse.** Die RP-ID ist der Host der Seite, auf der der Passkey angelegt wurde. Ein Passkey aus dem Browser taugt in der Desktop-App (`tauri.localhost`) nicht. Dort bleibt der Backup-Code.
- **Algorithmen.** ES256, EdDSA und RS256 (ab 2048 Bit). Andere Algorithmen lehnt `getPublicKey()` im Browser mit `null` ab, und der Server lehnt sie ebenfalls ab.

## Alternativen
- `webauthn` (py_webauthn): würde Attestation-Prüfung und CBOR mitbringen, die wir nicht brauchen, und drei Pakete mehr im Auth-Pfad. Verworfen.
- `fido2` (python-fido2, Yubico): noch größere Fläche, auch mit Client-Teil. Verworfen.

## Review
Diese ADR ist neu zu bewerten, sobald:
- Attestation oder eine erlaubte Geräteliste verlangt wird
- Passkeys zur alleinigen Anmeldung werden sollen (ohne Passwort, mit auffindbaren Credentials)
- ein Browser `getPublicKey()` für einen verbreiteten Algorithmus nicht liefert
