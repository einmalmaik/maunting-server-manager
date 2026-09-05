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

### 3.6 Echter QR-Code-Scan & Dedizierter Authenticator-Modus
- **Kamera- und Bild-Upload:** Integrierter Scanner (`jsqr`) zur Erkennung von `otpauth://`-QR-Codes live per Webcam oder über Screenshot-/Datei-Upload.
- **2FA-Cockpit:** Eigener Tab für Authenticator-Tokens mit großen, gut ablesbaren Codes, Live-Countdown-Kreis und 1-Klick-Kopieren.

### 3.7 Kategorisierung, Favoriten & Zuletzt verwendet
- **Favoriten:** Wichtige Zugänge lassen sich per Stern als Favorit markieren und werden dauerhaft oben angeheftet.
- **Zuletzt verwendet:** Direkter Schnellfilter für kürzlich genutzte oder kopierte Zugangsdaten.
- **Kategorien:** Saubere Trennung zwischen Logins, Authenticators und sicheren Notizen.

### 3.8 Datenschutzfreundlicher Leak-Check (K-Anonymität)
- **Leaked-Password-Prüfung:** Ermittelt sekundenschnell, ob ein Passwort in bekannten Datenlecks auftaucht.
- **K-Anonymität:** Nur die ersten 5 Zeichen des SHA-1-Hashes verlassen das Gerät. Weder das Klartext-Passwort noch der vollständige Hash werden jemals übertragen.

### 3.9 Verschlüsselte Notizen & Dateianhänge
- **Sichere Notizen:** Geschützter Freitextbereich für sensible Dokumentationen, Wiederherstellungsschlüssel und Backup-Codes.
- **Verschlüsselte Dateien:** Sicheres lokales Anhängen und Entschlüsseln vertraulicher Dateien (bis 25 MB pro Anhang), integriert in den AES-256-GCM Blob.

### 3.10 Windows Computer-Use KI-Schutz (Human Error Guard)
- **Hardware- & Software-Schutz:** Verhindert das versehentliche Erfassen des Passwort-Managers durch Bildschirmaufnahmen der KI bei Computer-Use.
- **Doppelte Schutzschicht:**
  - OS-Ebene: `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` schließt das Fenster auf DWM-Ebene aus allen Screen-Captures aus.
  - Software-Ebene: `bildschirm.rs` maskiert das Anwendungsfenster im Capture automatisch mit solidem Schwarz, falls der Passwort-Manager geöffnet ist.

### 3.11 Passwort-Hinweis per E-Mail (10-Minuten-Schutz) & UX-Entschlackung
- **Master-Passwort-Hinweis:** Beim Einrichten oder in den Einstellungen kann optional eine persönliche Gedankenstütze hinterlegt werden.
- **E-Mail-Zustellung:** Im Sperrbildschirm kann der Hinweis mit 1 Klick an die verknüpfte E-Mail-Adresse des Kontos gesendet werden.
- **10-Minuten-Missbrauchsschutz:** Ein Server-seitiges Rate-Limit blockiert mehrfaches Anfordern innerhalb von 10 Minuten.
- **UX-Korrektur (Datenverlust-Schutz):** Wurde ein Master-Passwort bereits eingerichtet, ist der Link zur Neueinrichtung im Sperrbildschirm permanent ausgeblendet.
- **Unterdrückung doppelter Passwort-Augen:** Native Browser-/Windows-Augen-Symbole (`::-ms-reveal`) sind neutralisiert; es wird exakt ein einheitliches Icon angezeigt.
- **Radikale Textreduktion:** Sämtliche langen Textpassagen, Beipackzettel und Infoboxen wurden entfernt. Das UI ist minimalistisch, selbsterklärend und sauber.

---

## 4. Zentrale Panel-Verwaltung & KISS-Architektur

- Der Passwort-Manager arbeitet nach dem **KISS-Prinzip (Keep It Simple, Stupid)**: Anstatt unnötiger Multi-Node-Verbindungsüberbauten läuft der verschlüsselte Zero-Knowledge-Sync direkt, stabil und schlank über die zentrale Panel-Datenbank.
- Im Panel unter **Einstellungen → Allgemein** können Administratoren den Passwort-Manager panelweit aktivieren oder deaktivieren (`vault_enabled`).
- Dadurch werden sämtliche Tresor-Datensätze automatisch von den regulären Panel-Backups erfasst – ohne Datenverlustrisiko oder verwaiste Node-Tabellen.

---

## 5. Geänderte und neue Dateien

### Backend
- `backend/models/vault_entry.py`: Schlankes PostgreSQL-Datenmodell für anonyme Tresoreinträge (ohne Node-Spalten).
- `backend/models/__init__.py`: Registrierung des Modells.
- `backend/migrations/versions/20260902_02_vault_entries.py`: Alembic-Migration für `vault_entries` und Indizes.
- `backend/migrations/versions/20260905_01_drop_vault_entries_node_id.py`: Bereinigungs-Migration zur Entfernung verwaister Node-Spalten.
- `backend/schemas/vault.py`: Pydantic-Validierungs- und Datenübertragungsmodelle.
- `backend/services/vault_service.py`: Revisionsverwaltung und deterministische Synchronisationslogik.
- `backend/routers/vault.py`: REST-Endpunkt `/api/vault/sync`.
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
- `frontend/src/pages/settings/GeneralTab.tsx`: Panelweiter Aktivierungs-Schalter (`vault_enabled`).
- `frontend/src/locales/de.json` & `frontend/src/locales/en.json`: Übersetzungen.
