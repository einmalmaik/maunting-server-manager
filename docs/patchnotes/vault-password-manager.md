# Patchnotes — Integrierter Zero-Knowledge Passwort-Manager & 2FA Authenticator

**Branch:** `feature/password-manager`  
**Datum:** 2. September 2026  
**Status:** Abgeschlossen & Verifiziert  

---

## 1. Übersicht

Der Maunting Server Manager erhält einen integrierten, hochsicheren Passwort-Manager samt RFC-6238-konformem TOTP-Authenticator. 

Gemäß den Sicherheitsprinzipien von Maunting Studios („Schutz braucht Vertrauen“) ist das System so konzipiert, dass der Server-Betreiber oder Angreifer zu keinem Zeitpunkt Einsicht in Passwörter, Servicenamen, URLs oder Benutzernamen nehmen kann.

---

## 2. Architektur & Sicherheitsgarantien

### 2.1 Plattform-Isolation (Tauri Desktop & Mobile APK Only)
- **Kein Web-Tresor:** Der Passwort-Manager ist bewusst ausschließlich in der nativen Tauri-Desktop-App und der Android-APK aktiv. Web-Browser besitzen prinzipbedingt eine größere Angriffsfläche (XSS, bösartige Erweiterungen, unsichere Caches); daher führt die Webversion des Panels keinerlei Vault-Entschlüsselungs- oder Schlüsselableitungs-Code aus.
- **KI-Isolation:** Sämtliche KI-Systeme (Chat, Voice-Bridge, Autonomous Actions, Tools) besitzen weder Endpunkte noch Berechtigungen oder Kenntnis über Tresor-Inhalte. Es gibt kein KI-Tool für den Tresor.

### 2.2 Echte Anonymisierung & Zero-Metadata in der Datenbank
- **Keine Metadaten-Spalten:** Die PostgreSQL-Tabelle `vault_entries` speichert ausschließlich:
  - `bucket_id`: Ein 64-Zeichen Hex-Hash, der auf dem Endgerät blind abgeleitet wird (`SHA-256(PBKDF2-Subkey || "bucket-id")`). Er enthält keinen Verweis auf die User-ID oder Kontonamen.
  - `ciphertext`: Authentifizierter AES-256-GCM Ciphertext im standardisierten DIS-Umschlagformat `sv-vault-v1:<iv || ciphertext || tag>`.
  - `revision`: Monotoner Revisionszähler für konfliktfreie Synchronisation.
  - `is_deleted`: Tombstone-Flag für geräteübergreifendes Löschen.
- **Keine Plaintext-Informationen:** Servicenamen, Benutzernamen, Passwörter, URLs, Notizen und 2FA-Secrets befinden sich ausnahmslos innerhalb des verschlüsselten JSON-Objekts.

### 2.3 DIS Sidecar & Memory Hygiene
- **Kryptographisches Primitiv:** AES-256-GCM mit 96-Bit Zufalls-IVs und zusätzlicher Datenbindung (AAD) an die `entryId`.
- **Schlüsselableitung:** PBKDF2-HMAC-SHA-256 mit 100.000 Runden und 256-Bit Salt.
- **Memory Hygiene:** Schlüsselmaterial wird in `SecureBuffer`-Instanzen gekapselt. Nach Abschluss kryptographischer Operationen werden die Puffer per `.destroy()` im Arbeitsspeicher überschrieben.

---

## 3. Benutzeroberfläche & Bedienkomfort

### 3.1 Blitzschnelle Passworterstellung (<= 2 Klicks)
- Mit einem Klick auf „Neuer Eintrag“ wird automatisch ein hochsicheres, zufälliges Passwort nach NIST-Empfehlungen generiert.
- Nach Eingabe des Dienstnamens (z. B. „Gmail“) speichert ein Klick auf „Speichern“ den Eintrag sofort verschlüsselt ab.

### 3.2 Lokaler SVG-Markenkatalog
- Integrierte Vektorgrafiken für bekannte Dienste (Google, Discord, GitHub, Steam, Microsoft, Apple, Netflix, Spotify, Amazon, Reddit, Proton).
- Vollständig lokal und modular eingebettet: Keine externen Netzwerkanfragen oder Tracking-Aufrufe zum Abrufen von Favicons.

### 3.3 Integrierter 2FA Authenticator (RFC 6238)
- Automatische Berechnung von 6-stelligen Zeittakt-Codes (HMAC-SHA-1, 30 Sekunden Intervall).
- Visuelle, animierte Countdown-Anzeige für die Restgültigkeit.
- Unterstützung für manuelle Secret-Eingabe (Base32) sowie `otpauth://totp/`-URIs (z. B. aus QR-Codes).

### 3.4 Offline-First Synchronisation
- Einträge werden im lokalen, geschützten Speicher zwischengespeichert.
- Offline erstellte, geänderte oder gelöschte Einträge werden in einer lokalen Warteschlange vorgehalten und automatisch im Hintergrund mit dem MSM-Backend synchronisiert, sobald wieder Netzwerkempfang besteht.

### 3.5 Ersteinrichtungs-Assistent & Verifikations-Canary
- **Automatischer Einrichtungs-Modus:** Erkennt das System, dass auf dem Endgerät noch kein Master-Passwort hinterlegt wurde, öffnet sich direkt der Einrichtungs-Dialog („Passwort-Manager einrichten“) mit doppelter Passworteingabe zur Bestätigung.
- **Validierung:** Direkte Rückmeldung zu Mindestlänge (>= 8 Zeichen) und Übereinstimmung der beiden Passwörter.
- **Kryptographischer Canary:** Beim Einrichten wird ein verschlüsselter Prüfblock (`mss:vault_canary_<bucket_id>`) erzeugt. Beim späteren Entsperren prüft das System damit sofort, ob das eingegebene Master-Passwort korrekt ist, und weist Falscheingaben direkt mit einer klaren Meldung ab.
- **Flexibles Wechseln:** Über einen einfachen Link kann jederzeit zwischen Ersteinrichtung und Entsperren eines bereits bestehenden Tresors gewechselt werden.

---

## 4. Multi-Node Konfiguration im Panel Manager

- Im Panel unter **Einstellungen → Passwort-Manager** können Administratoren festlegen, welcher Node aus dem Multi-Node-Cluster für die Verwaltung und Speicherung der Tresor-Blobs zuständig ist.
- Die Auswahl erfolgt barrierefrei über die MauntingStudios Design-DNA Dropdown-Komponente (keine nativen HTML-Selects).

---

## 5. Geänderte und neue Dateien

### Backend
- `backend/models/vault_entry.py`: PostgreSQL-Datenmodell für anonyme Tresoreinträge.
- `backend/models/__init__.py`: Registrierung des Modells.
- `backend/migrations/versions/20260902_02_vault_entries.py`: Alembic-Migration für `vault_entries` und Indizes.
- `backend/schemas/vault.py`: Pydantic-Validierungs- und Datenübertragungsmodelle.
- `backend/services/vault_service.py`: Revisionsverwaltung, Synchronisationslogik und Node-Zuweisung.
- `backend/routers/vault.py`: REST-Endpunkte `/api/vault/sync` und `/api/vault/node-assignment`.
- `backend/routers/__init__.py`: Router-Export.
- `backend/main.py`: Einbindung des Vault-Routers unter `/api/vault`.
- `backend/tests/test_vault_router.py`: Pytest-Suite für Authentifizierung, Validierung und Synchronisation.

### Frontend
- `frontend/src/desktop/vault/vaultCrypto.ts`: DIS-Kryptographie, `SecureBuffer`, PBKDF2 und AES-256-GCM.
- `frontend/src/desktop/vault/vaultCrypto.test.ts`: Vitest-Suite für Ver- und Entschlüsselung.
- `frontend/src/desktop/vault/totpEngine.ts`: RFC 6238 TOTP-Generator und URI-Parser.
- `frontend/src/desktop/vault/totpEngine.test.ts`: Vitest-Suite für TOTP-Zeittakt und Base32.
- `frontend/src/desktop/vault/brandCatalog.tsx`: Lokaler SVG-Markenkatalog.
- `frontend/src/desktop/vault/vaultStore.ts`: Zustand Offline-First Store mit Revisionsabgleich.
- `frontend/src/desktop/vault/VaultView.tsx`: Tresor-Oberfläche mit Master-Passwort-Schutz und 2FA-Ring.
- `frontend/src/desktop/DesktopApp.tsx`: Navigation, Route `/tresor` und Drawer-Integration.
- `frontend/src/pages/settings/VaultSettingsTab.tsx`: Multi-Node-Konfiguration im Admin-Panel.
- `frontend/src/pages/Settings.tsx`: Neuer Tab „Passwort-Manager“.
- `frontend/src/locales/de.json` & `frontend/src/locales/en.json`: Übersetzungen.
