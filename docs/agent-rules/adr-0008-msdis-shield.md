# ADR-0008: @msdis/shield Dependency-Bewertung und Exit-Strategie

Status: Accepted
Date: 2026-07-04

## Context
Für die sichere Verschlüsselung von Server-Credentials, Passwörtern, OAuth-Secrets und E-Mails führen wir das dezentrale Sicherheitsmodell DIS (Decentralized Identity & Security) ein. Die kryptographischen Operationen laufen im isolierten `dis-sidecar` (Node.js). Wir binden das Sidecar an die offizielle DIS-Client-Library `@msdis/shield`.

## Entscheidung
Wir nutzen `@msdis/shield` in Version `0.2.0` (exakt gepinnt in `dis-sidecar/package.json` und gelockt über `package-lock.json`) zur Verschlüsselung und Verifizierung im Sidecar.

## Security-Bewertung
- **Einstufung**: Hohes Risiko. Die Bibliothek verarbeitet Plaintext-Secrets (Passwörter, API-Keys, TOTP-Secrets) und führt sensible Krypto-Operationen (AES-256-GCM, Argon2id, TOTP) aus.
- **Zweck**: Bietet standardisierte, auditierte und performante Implementierung der DIS-Spezifikation (Verschlüsselung mit AAD-Bindung, robustes Passwort-Hashing und TOTP-Generierung/Validierung).
- **Notwendigkeit**: Eigene Implementierung von AES-256-GCM, Argon2id und TOTP in Node.js von Hand zu schreiben ist fehleranfällig und erhöht das Sicherheitsrisiko (KISS-Prinzip bzgl. kryptographischer Standards).
- **Maintainer & Advisories**: Aktiv gepflegt durch das MSDis-Sicherheitsteam. Keine bekannten CVEs oder offenen Advisories für Version `0.2.0`.
- **Transitive Fläche**: Extrem gering (nur 4 Packages installiert). Keine unnötigen Komfort-Dependencies.

## Nutzung im Projekt
- **Kapselung**: Die Library wird ausschließlich im `dis-sidecar/server.mjs` geladen und instanziiert. Die Python-Anwendung hat keinen direkten Zugriff auf `@msdis/shield` und kommuniziert nur via REST-HTTP-Calls über die Kapselklasse `DisClient` (in `backend/services/dis_client.py`) mit dem Sidecar.
- **Tests**: Vollständige Integrationstests im Backend (`backend/tests/`) decken die Ver- und Entschlüsselung (inklusive AAD-Kontextprüfung) ab.
- **Install-Reproduzierbarkeit**: Im Installer (`install.sh`) wird `npm ci --omit=dev` verwendet, um exakte Versionstreue über das Lockfile zu garantieren.

## Exit-Plan
Falls `@msdis/shield` kompromittiert, unmaintained oder ersetzt werden muss:
1. Da das API-Gateway des Sidecars (`/encrypt`, `/decrypt`, `/hash-password`, etc.) standardisierte HTTP-Endpunkte anbietet, kann die interne Implementierung des Sidecars vollständig ausgetauschen werden.
2. Der Python-Code bleibt unberührt, da er nur an der REST-Schnittstelle des Sidecars hängt.
3. Die Library kann durch direkten Aufruf von Node.js-eigenen `crypto`-APIs oder eine alternative DIS-Implementierung ersetzt werden, ohne das Backend anpassen zu müssen.
