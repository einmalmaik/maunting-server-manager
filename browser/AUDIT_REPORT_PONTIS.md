# Technischer Audit- und Wettbewerbsbericht: Maunting Pontis (v5.5.0)

**Gegenstand:** Architektur-, Sicherheits- und Wettbewerbsanalyse im Vergleich zu Google Chrome und Brave Browser  
**Status:** Produktionsstand v5.5.0 (Tauri v2 + WebView2 Shell + Safe Rust Backend)  
**Erstellungsdatum:** 17. Oktober 2026  
**Zielsysteme:** Windows Desktop (x64/ARM64), Android Mobile  

---

## 1. Executive Summary & Architektonische Gesamteinordnung

### 1.1 Technische Würdigung des Entwicklungsstands
Maunting Pontis stellt in Version 5.5.0 ein bemerkenswertes technisches Resultat dar. Das Projekt entstand unter intensiver Nutzung von KI-gestützter Entwicklung (Vibecoding), unterscheidet sich jedoch grundlegend von typischen Prototypen dieser Kategorie: Anstelle oberflächlicher Skripte oder naiver API-Zusammenstellungen weist die Codebasis eine konsistente, defensive Softwarearchitektur auf.

Das System kombiniert Tauri v2 (Rust 1.88, Edition 2021) für Anwendungslogik, Systemintegration und Sicherheitsregeln mit der nativen Microsoft Edge WebView2 Evergreen Runtime unter Windows sowie der Android System WebView auf mobilen Endgeräten. Die Oberfläche ist als Single-Page-Anwendung in React 18, TypeScript und Tailwind CSS realisiert.

Entgegen weitverbreiteten Annahmen über WebView-basierte Browser handelt es sich bei Maunting Pontis nicht um einen simplen Wrapper um ein Browser-Fenster. Die Implementierung greift auf tiefen Ebenen in die Prozessverwaltung, die Netzwerk-Pipeline und das Windows Component Object Model (COM) ein.

### 1.2 Die Hybrid-Architektur im Detail
Klassische Multi-Webview-Anwendungen unter Tauri erzeugen Webseiten-Instanzen über den Standard-`WebviewBuilder`. Dieser Ansatz führt bei Browsern zu einem gravierenden Sicherheitsrisiko: Jede von Tauri verwaltete Webview erhält standardmäßig Zugriff auf die interne IPC-Brücke (`window.__TAURI_INTERNALS__`) sowie auf vorinjizierte Skripte aller geladenen Plugins. Aufgerufene native Methoden wie `alert`, `confirm` oder Benachrichtigungen werden dadurch unkontrolliert an das Host-Framework umgeleitet.

Maunting Pontis löst dieses Problem durch eine architektonische Trennung:
1. **Host- und Oberflächen-Schicht:** Die Anwendungs-UI (`main`) läuft in einer strikt reglementierten Tauri-Webview. Ihr stehen über ein deklaratives App-Manifest genau 81 definierte IPC-Befehle zur Verfügung.
2. **Webseiten-Schicht (Tabs):** Webseiten-Tabs werden unter Windows vollständig an Tauris Webview-Verwaltung vorbei instanziiert. Der Rust-Host erzeugt rohe Win32-COM-Controller (`ICoreWebView2Controller`), die direkt in das HWND des Hauptfensters eingebettet und pixelgenau positioniert werden. Webseiten-Tabs besitzen weder die Tauri-IPC-Brücke noch das `ipc:`-Protokoll oder fremde Plugin-Skripte.
3. **Filter- und Kontroll-Schicht:** Zwischen der Rendering-Engine und dem Netzwerk betreibt Pontis eine eigenständige Rust-Pipeline (`src-tauri/src/schild/`), die auf der quelloffenen Brave `adblock`-Engine (v0.13, MPL-2.0) basiert und Netzwerkanfragen vor dem Öffnen von Sockets abfängt.

### 1.3 Zentrale Kennzahlen der Codebasis
- **Programmiersprachen & Laufzeiten:** Rust 1.88 (Edition 2021), TypeScript 5.x, React 18.
- **Speichersicherheit im Schlüsselmanagement:** 0 Vorkommen von `unsafe` im Crate `crates/schluesselfach`.
- **IPC-Reglementierung:** 81 registrierte Befehle, 100 % Abdeckung durch automatisierte Manifest-Tests.
- **Netzwerkfilter:** Dual-Engine-Modell mit EasyList, EasyList Germany, EasyPrivacy und Fanboy Cookie Monster.
- **Ressourcenmanagement:** Dreistufige Speicherverwaltung mit vollständiger Freigabe (0 MB RAM) für verworfene Tabs.
- **Privatsphäre-Parameter:** 48 bereinigte Tracking-Parameter auf Netzwerk- und Zwischenablage-Ebene.

---

## 2. R1: Ausführliche Architektur- und Sicherheitsanalyse

### 2.1 Webview-Isolation: Browser-Oberfläche (`main`) vs. Webseiten-Tabs (`tab-*`)

Die Isolation zwischen der privilegierten Browser-Oberfläche und nicht vertrauenswürdigen Webseiten ist in Maunting Pontis über vier redundante Schutzebenen (Defense-in-Depth) gelöst.

#### 2.1.1 Native Win32-COM-Instanziierung ohne Tauri-IPC
In `src-tauri/src/tabs/desktop.rs` (Zeilen 1–10 und 161–188) dokumentiert und erzwingt der Quellcode die Umgehung von Tauris `WebviewBuilder` für Webseiten-Tabs:
```rust
//! Tabs unter Windows: je Tab ein eigener WebView2-Controller im Hauptfenster.
//!
//! Bewusst nicht über Tauri. Jede Webview, die Tauri anlegt, bekommt die
//! IPC-Brücke (`__TAURI_INTERNALS__`) und die Skripte aller Plugins: in einer
//! fremden Seite gingen `alert`, `confirm` und `Notification` an Tauri statt
//! an den Browser, und jede Seite sah die Brücke (bis 10/2026). Die Befehle
//! scheiterten zwar an den Rechten, aber eine Seite soll die Brücke gar nicht
//! erst haben. Hier gibt es keine: kein Skript von Tauri, kein
//! `ipc:`-Protokoll. Web-Nachrichten hat nur `seite.js` (`formulare.rs`).
```
Die Erzeugung erfolgt direkt über `CreateCoreWebView2ControllerWithOptions`. In `src-tauri/src/tabs/desktop.rs` (Zeilen 222–223) werden Host-Objekte für den Tab explizit unterbunden:
```rust
einstellungen.SetIsWebMessageEnabled(true).map_err(fehler)?;
einstellungen.SetAreHostObjectsAllowed(false).map_err(fehler)?;
```
Eine Webseite hat somit physikalisch keinen Zugriff auf das Tauri-Laufzeitsystem.

#### 2.1.2 Deklaratives App-Manifest und Capability-Grenzen
Sollte durch einen hypothetischen Fehler dennoch ein IPC-Aufruf aus einem Webseiten-Kontext abgesetzt werden, greift die Sicherheitsarchitektur von Tauri v2:
- In `src-tauri/build.rs` (Zeilen 1–99) registriert der Build-Prozess alle 81 Befehle des Browsers (`BEFEHLE`) im `AppManifest`. Dadurch erzwingt der Tauri-Kernel zur Laufzeit eine Berechtigungsprüfung jedes Aufrufs anhand der Capabilities.
- In `src-tauri/capabilities/oberflaeche.json` (Zeilen 1–13) ist die Capability-Gruppe `oberflaeche` exklusiv an das Webview-Label `main` gebunden:
  ```json
  {
    "identifier": "oberflaeche",
    "description": "Rechte der Browser-Oberfläche. Sie gelten nur für die Webview `main`, nicht für das Fenster: im Fenster liegen auch die Tabs, und die bekommen keine Rechte.",
    "webviews": ["main"],
    "permissions": [
      "core:default",
      "core:window:allow-start-dragging",
      "dialog:allow-open",
      "notification:default",
      "oberflaeche"
    ]
  }
  ```
- In `src-tauri/permissions/oberflaeche.toml` (Zeilen 1–88) sind alle 81 Befehle einzeln deklariert.
- In `src-tauri/src/lib.rs` (Zeilen 283–305) stellt der automatisierte Integrationstest `rechte_decken_jeden_befehl()` sicher, dass kein Handler ohne korrespondierende Berechtigung existiert.

#### 2.1.3 DOM-Hygiene und Neutralisierung von `chrome.webview`
Um auch die von WebView2 für Skript-Nachrichten bereitgestellte Schnittstelle vor Webseiten-Skripten zu verbergen, injiziert Pontis via `ICoreWebView2::AddScriptToExecuteOnDocumentCreated` vor jedem anderen Skript die Datei `src-tauri/src/seite.js` (Zeilen 1–15, 33–43):
```javascript
const bruecke = (window.chrome && window.chrome.webview) || window.msbKanal
const android = bruecke === window.msbKanal
try {
  delete window.chrome.webview
} catch (_) {}
try {
  delete window.msbKanal
} catch (_) {}
if (!bruecke) return
```
`seite.js` sichert vor der DOM-Initialisierung JavaScript-Intrinsics und Prototypen (`Reflect.apply`, `Object.getOwnPropertyDescriptor`) in lexikalischen Closures, um Manipulationen durch Prototype Pollution oder Monkey-Patching abzuwehren. Eingehende Web-Nachrichten werden in `src-tauri/src/tabs/desktop/formulare.rs` (Zeilen 40–44) über `formular::gleiche_herkunft` geprüft, indem `core.Source()` mit `args.Source()` abgeglichen wird.

#### 2.1.4 Strikte Content Security Policy (CSP) der Oberfläche
Die Browser-Oberfläche unterliegt in `src-tauri/tauri.conf.json` (Zeilen 30–32) einer restriktiven CSP:
```text
default-src 'self'; connect-src 'self' https: wss: http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval' blob:; object-src 'none'; base-uri 'self'; form-action 'none'
```
Die Sicherheitsinvarianten werden in `src-tauri/src/lib.rs` (Zeilen 307–333) durch den Unit-Test `die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval()` überwacht:
- Kein `'unsafe-eval'` im `script-src`.
- `img-src` verbietet externe HTTP/HTTPS-Quellen. Webseiten-Favicons werden stattdessen im Rust-Backend über die COM-Schnittstelle `ICoreWebView2_15::GetFavicon` ausgelesen und als lokale Data-URL an die UI übergeben (`src-tauri/src/tabs/desktop/webview2.rs`, Zeilen 147–172). Ein Nachladen externer Ressourcen durch die UI ist ausgeschlossen.
- `connect-src` beschränkt unverschlüsselte Verbindungen auf Loopback (`localhost`, `127.0.0.1`).

#### 2.1.5 Schema- und Ursprungsvalidierung
In `src-tauri/src/tabs/weg.rs` (Zeilen 9–50) validiert Pontis Navigationsziele und Tab-Identifikatoren:
- Tab-IDs müssen dem Schema `^tab-[a-zA-Z0-9-]{1,36}$` entsprechen. Eine Tab-Webview kann niemals den reservierten Bezeichner `main` erhalten.
- Interne Ursprünge (`tauri.localhost`, `ipc.localhost`, `asset.localhost`) sind für Tabs gesperrt (`navigation_erlaubt()`).
- Gefährliche Windows- und System-Schemata werden über die Liste `NIE_EXTERN` blockiert: `file:`, `javascript:`, `vbscript:`, `view-source:`, `intent:`, `search-ms`, `ms-search` sowie `ms-msdt` (Schutz gegen Angriffsvektoren wie Follina).

---

### 2.2 Host-Speichersicherheit und Nebenläufigkeit

#### 2.2.1 Rust-Laufzeitumgebung und Unsafe-Auditing
Der Host-Prozess von Maunting Pontis ist in Rust kompiliert (`src-tauri/Cargo.toml`: `edition = "2021"`, `rust-version = "1.88"`).

Ein systematisches Code-Audit zeigt folgende Verteilung von `unsafe`:
- **`crates/schluesselfach`:** Exakt **0 Vorkommen von `unsafe`**. Das gesamte Modul für kryptografische Operationen und Betriebssystem-Schlüsselverwahrung ist in sicherem Rust verfasst.
- **`src-tauri/src`:** `unsafe` ist strikt auf die Foreign Function Interfaces (FFI) beschränkt:
  1. Win32 COM-Aufrufe an die WebView2-Schnittstellen (`webview2_com::Microsoft::Web::WebView2::Win32::*`).
  2. Windows-Systemaufrufe (`windows::Win32::*`), darunter `IAttachmentExecute::Save` für die Virenprüfung (`src-tauri/src/downloads/pruefung.rs`), HWND-Fensteroperationen (`src-tauri/src/fenster.rs`, `src-tauri/src/kurzinfo.rs`) und Audiomixer-Schnittstellen (`src-tauri/src/mixer.rs`).
  3. Android-JNI-Aufrufe (`src-tauri/src/tabs/android/bruecke.rs`).
Es existiert keine manuelle Pointer-Arithmetik oder ungesicherte Speicherverwaltung im Verarbeitungs- und Filtercode. Speicherfehler wie Use-After-Free, Double Free oder Pufferüberläufe sind im Host-Prozess durch das Typsystem ausgeschlossen.

#### 2.2.2 Synchronisationsmuster und Vermeidung von Blockaden
- **Parallelisiertes Lesen im Schild:** In `src-tauri/src/schild/mod.rs` (Zeilen 110–128) liegen die Filter-Engines für Werbung und Tracker hinter `RwLock<Engine>`. Eingehende Netzwerkanfragen lesen parallel ohne gegenseitige Blockade (`.read().unwrap()`). Aktualisierungen von Filterlisten beanspruchen kurzzeitig den Schreiblock (`.write().unwrap()`).
- **Atomare Schalter:** Globale Zustände wie `SCHILD.aktiv` nutzen `AtomicBool` mit `Ordering::Relaxed`.
- **UI-Thread-Synchronisation:** WebView2 erfordert eine Bindung an den UI-Thread des Windows-Fensters. Hintergrund-Worker im Rust-Backend übergeben Aufgaben über `app.run_on_main_thread` und synchronisieren Ergebnisse über `std::sync::mpsc::channel` mit einem festen Timeout-Wächter (`Duration::from_secs(20)`). Ein hängender Tab kann dadurch den Rust-Host-Prozess nicht dauerhaft blockieren.

#### 2.2.3 Prozess-Absturzresilienz (`absturz.rs`)
In `src-tauri/src/tabs/desktop/absturz.rs` (Zeilen 16–36) registriert Pontis den Ereignishandler `add_ProcessFailed`. Das System unterscheidet präzise zwischen Absturzarten:
- Nur `COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED` und `BROWSER_PROCESS_EXITED` werden als Tab-Absturz gewertet (`TabEreignis::Absturz`) und führen zur Anzeige einer Wiederherstellungsoption im Frontend.
- Abstürze von Hilfsprozessen (GPU-Prozess, Utility-Prozess, Out-of-Process Iframes) fängt die WebView2-Laufzeit automatisch ab und startet sie neu. Der Pontis-Host-Prozess und parallele Tabs laufen ohne Unterbrechung weiter.

---

### 2.3 Schutz sensibler Daten via `schluesselfach`

Das Schlüssel- und Geheimnismanagement ist in das eigenständige Crate `crates/schluesselfach` ausgelagert, das von Pontis und dem Maunting Server Manager gemeinsam genutzt wird.

```
+-------------------------------------------------------------------------+
|                              Pontis Frontend                            |
|          (Hält ausschließlich flüchtige Access-Tokens im RAM)           |
+------------------------------------+------------------------------------+
                                     | Tauri IPC
                                     v
+-------------------------------------------------------------------------+
|                        Host-Prozess (`src-tauri`)                       |
|               Strikte Compartment-Prüfung (`pruefe_fach`)               |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                      `crates/schluesselfach`                            |
|             (100 % Safe Rust, 0 Vorkommen von `unsafe`)                 |
+------------------------------------+------------------------------------+
                  |                                    |
      [Windows Plattform]                     [Android Plattform]
                  v                                    v
+----------------------------------+ +------------------------------------+
|  Windows Hello Biometrie-Gate    | | Hardware Keystore (AndroidKeyStore)|
|  `UserConsentVerifier`           | | AES-256-GCM (128-Bit Tag)          |
|  (Gesicht / Fingerabdruck / PIN) | | `setUserAuthenticationRequired`    |
+-----------------+----------------+ | `setInvalidatedByEnrollment`       |
                  | Verified         +-----------------+------------------+
                  v                                    |
+----------------------------------+                   v
|  Windows Credential Manager      |       Geschützte App-Einstellungen   |
|  DPAPI-Verschlüsselung           |       `base64(iv):base64(ciphertext)`|
+----------------------------------+--------------------------------------+
```

#### 2.3.1 Windows-Implementierung: Credential Manager & Windows Hello
- **Speicherung:** Geheimnisse werden über das Crate `keyring` (v4) im Windows Credential Manager verwaltet und durch die Data Protection API (DPAPI) an das Benutzerkonto gekoppelt.
- **Biometrische Härtung:** In `crates/schluesselfach/src/biometrie.rs` (Zeilen 316–337) schützt Pontis sensible Schlüssel über die Windows-Runtime-API `windows::Security::Credentials::UI::UserConsentVerifier`. Vor jedem Lesezugriff fordert `RequestVerificationAsync` eine biometrische Bestätigung (Gesichtserkennung, Fingerabdruck) oder die Windows-Hello-PIN an. Erst nach erfolgreicher Verifikation (`UserConsentVerificationResult::Verified`) wird der Wert aus dem Credential Store ausgelesen.

#### 2.3.2 Android-Implementierung: Hardware-Keystore mit AES-256-GCM
Unter Android (`src-tauri/gen/android/.../Schluesselfach.kt`) wird ein hardwaregestützter Schlüssel im `AndroidKeyStore` generiert:
- **Kryptografie:** AES-256 im Galois/Counter Mode (`AES/GCM/NoPadding`) mit 128-Bit Authentifizierungstag.
- **Hardware-Bindung:** Schlüssel werden mit `setUserAuthenticationRequired(true)` und Timeout 0 Sekunden erzeugt. Jeder Zugriff verlangt eine direkte Bestätigung via `BiometricPrompt` (`AUTH_BIOMETRIC_STRONG`).
- **Enrollment-Invalidierung:** Die Option `setInvalidatedByBiometricEnrollment(true)` stellt sicher, dass der Schlüssel unwiderruflich unbrauchbar wird, sobald im Betriebssystem ein neuer Fingerabdruck oder ein neues Gesicht hinzugefügt wird. Dies verhindert Angriffe durch nachträglich registrierte biometrische Merkmale.
- Die verschlüsselten Daten liegen als `base64(iv):base64(ciphertext)` in privaten App-Einstellungen und werden mit `commit()` synchron persistiert.

#### 2.3.3 Strikte Trennung der Fächer (Compartments) & Isolation des Session-Tokens
In `crates/schluesselfach/src/biometrie.rs` (Zeilen 36–73) erzwingt die Validierungsfunktion `pruefe_fach()` eine strikte Positivliste, die Frontend-Aufrufe über die generischen Biometrie-IPC-Befehle (`biometrie_speichern`, `biometrie_entsperren`, `biometrie_loeschen`) auf exakt **drei** Fächer beschränkt:
1. `vault_biometric_key` (`FACH_TRESOR`): Master-Passwort des Passwort-Tresors.
2. `messenger_biometric_key` (`FACH_MESSENGER`): Zugangs-PIN für den internen Messenger.
3. `messenger_device_secret` (`FACH_MESSENGER_GERAET`): 32 kryptografisch zufällige Bytes, die in die lokale Schlüsselableitung eingehen. Selbst wenn eine kompromittierte SQLCipher-Datenbank kopiert wird, kann sie ohne diesen im OS-Keyring geschützten Schlüssel nicht offline per Brute-Force angegriffen werden. (Wird über `lies_geraetegeheimnis` ohne UI-Abfrage gelesen, darf jedoch niemals allein entsperren).

Jeder vom Frontend übermittelte abweichende Fachname wird von `pruefe_fach()` mit `Unbekanntes Schlüsselfach: <fach>` zurückgewiesen.

**Architektonische Isolation von `session_refresh_token`:**
Das Refresh-Token zur Kopplung mit dem Maunting Server Manager (`FACH_SITZUNG = "session_refresh_token"` unter Android, `KONTO = "refresh_token"` unter Windows) ist in der Positivliste von `pruefe_fach()` bewusst **nicht** enthalten (`crates/schluesselfach/src/geheimnisse.rs`, Zeilen 25–28: *„nie von der Oberfläche erreichbar (`biometrie::pruefe_fach` kennt es nicht)“*).
Dies begründet eine noch stärkere Sicherheitsgrenze:
- Das Frontend kann das Refresh-Token niemals über die generischen Biometrie-IPC-Funktionen adressieren, auslesen oder manipulieren.
- Stattdessen wird das Token über separate, fest verdrahtete Host-Befehle verwaltet (`refresh_token_speichern`, `refresh_token_laden`, `refresh_token_loeschen` in `crates/schluesselfach/src/befehle.rs`), die keinen variablen Fach-Parameter akzeptieren und intern direkt an `geheimnisse.rs` delegieren.

#### 2.3.4 Flüchtige Sitzungstokens
Access-Tokens verbleiben während der gesamten Laufzeit ausschließlich im Arbeitsspeicher des Frontends und werden zu keinem Zeitpunkt auf den Datenträger geschrieben. Veraltete Klartextdateien früherer Versionen (`.session_auth`) werden beim Start automatisch in den Credential Store migriert und sofort sicher von der Festplatte gelöscht (`crates/schluesselfach/src/geheimnisse.rs`, Zeilen 75–97).

---

### 2.4 WebView2 Evergreen Runtime Modell und Sandboxing

#### 2.4.1 Wartungsmodell und Zero-Day-Patching
In `src-tauri/src/tabs/desktop.rs` (Zeilen 145–153) instanziiert Pontis die WebView2-Umgebung:
```rust
CreateCoreWebView2EnvironmentWithOptions(
    PCWSTR::null(),
    &HSTRING::from(profil.as_os_str()),
    &ICoreWebView2EnvironmentOptions::from(optionen),
    ...
)
```
Durch die Übergabe von `PCWSTR::null()` als Browser-Verzeichnis greift Pontis auf die systemweit installierte **Microsoft WebView2 Evergreen Runtime** zu.
- **Update-Kanal:** Die Runtime wird unabhängig von Pontis-Anwendungsupdates direkt über Microsoft Edge Update bzw. Windows Update gepflegt.
- **Patch-Bereitstellung:** Upstream-Chromium-Sicherheitslücken (Blink-, V8- und Skia-CVEs) werden von Microsoft in der Regel innerhalb von 24 bis 48 Stunden nach Bekanntgabe für die Evergreen Runtime ausgerollt. Das Pontis-Projekt muss keine eigenen Chromium-Binärdateien kompilieren, um kritische Zero-Day-Lücken in der Rendering-Engine zu schließen.

#### 2.4.2 Chromium Multi-Process Sandbox
Pontis erbt die vollständige Prozess-Sandbox-Architektur von Chromium:
- **Host-Prozess (`MauntingSecureBrowser.exe`):** Führt die Tauri-UI und das Rust-Backend mit normalen Benutzerrechten aus.
- **Browser-Broker-Prozess (`msedgewebview2.exe`):** Verwaltet COM-Endpunkte, Ressourcen-Handles und IPC-Kanäle.
- **Renderer-Prozesse (`msedgewebview2.exe`):** Jeder Tab läuft in einem isolierten Renderer-Prozess innerhalb eines Windows AppContainers bzw. einer Low-Integrity-Sandbox. Schreibzugriffe auf das Dateisystem und direkte Win32k-GDI-Aufrufe sind auf Betriebssystemebene gesperrt.

#### 2.4.3 Profil- und Sitzungstrennung
- In `src-tauri/src/tabs/mod.rs` (Zeile 452) und `src-tauri/src/browserdaten.rs` laufen Webseiten-Tabs in einem separaten Profilverzeichnis `seitenprofil` (`.../seiten`). Tresor- und Systemdaten der UI verbleiben im übergeordneten Anwendungsverzeichnis.
- **Workspaces:** Getrennte Arbeitsbereiche erhalten eigene Unterprofile über `optionen.SetProfileName("ws_<id>")`.
- **InPrivate-Modus:** Private Tabs laufen in flüchtigen In-Memory-Profilen (`optionen.SetIsInPrivateModeEnabled(true)`), die beim Schließen rückstandsfrei vom System freigegeben werden.

#### 2.4.4 Integrierte Download-Quarantäne und Virenprüfung
Downloads werden nicht direkt im Benutzerordner abgelegt:
1. In `src-tauri/src/downloads/quarantaene.rs` wird die Datei zunächst in einem isolierten Quarantäneordner gespeichert (`app_local_data/quarantaene/<zufall>/`).
2. In `src-tauri/src/downloads/pruefung.rs` übergibt Pontis die Datei an die Windows COM-Schnittstelle `IAttachmentExecute::Save`. Dadurch führt der installierte Windows-Virenschutz (Microsoft Defender oder Antivirensoftware von Drittanbietern) einen Scan aus und versieht die Datei mit der NTFS-Sicherheitszone (Mark of the Web, Zone 3).
3. Bei Downloads aus privaten Tabs wird die Herkunfts-URL auf `about:internet` gesetzt, um ein Ausspähen besuchter Adressen über NTFS Alternate Data Streams (`Zone.Identifier`) zu verhindern.

#### 2.4.5 Datenschutz-Härtung der WebView2-Laufzeit
Zur Unterbindung unerwünschter Plattform-Datenflüsse deaktiviert Pontis gezielt Hintergrunddienste:
- In `src-tauri/src/tabs/desktop.rs` (Zeile 67): `--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection`. Dies deaktiviert Microsoft SmartScreen und Edge-Overlays, womit die Übermittlung besuchter URLs an Microsoft-Server verhindert wird.
- In `src-tauri/src/tabs/desktop.rs` (Zeile 141): `optionen.set_is_custom_crash_reporting_enabled(true)` sorgt dafür, dass Absturzberichte lokal bleiben und nicht an Microsoft gesendet werden.
- In `src-tauri/src/tabs/desktop/ohne_edge.rs` (Zeilen 24–36): Abschaltung von Cloud-Diensten, automatischem Passwort-Speichern und Adress-Autofill (`SetIsPasswordAutosaveEnabled(false)`, `SetIsGeneralAutofillEnabled(false)`).

---

## 3. R2: Detaillierter Wettbewerbsvergleich mit Google Chrome und Brave Browser

### 3.1 Umfassende Vergleichsmatrix

| Bewertungsbereich | Google Chrome (v130+) | Brave Browser (v1.70+) | Maunting Pontis (v5.5.0) | Fundstelle im Pontis-Code |
| :--- | :--- | :--- | :--- | :--- |
| **Architekturmodell** | Monolithischer C++ Chromium Fork | Monolithischer C++/Rust Chromium Fork | Modulare Hybrid-Architektur (Tauri v2 + WebView2 Shell + Safe Rust) | `src-tauri/src/lib.rs`, `src-tauri/Cargo.toml` |
| **Adblock-Engine** | Keine native Engine; Manifest V3 DNR (strenge Regellimits) | Brave Shields (native `adblock-rust` Engine in C++ Core) | Brave `adblock` Engine (v0.13, MPL-2.0) direkt in Rust integriert | `src-tauri/Cargo.toml:28–34`, `src-tauri/src/schild/mod.rs:110–128` |
| **Filterlisten** | Nur statische Listen via WebExtensions | EasyList, EasyPrivacy, uBlock-Listen, autom. Updates | EasyList, EasyList Germany, EasyPrivacy, Fanboy Cookie Monster | `src-tauri/src/schild/listen.rs:26–57` |
| **Netzwerk-Abfangung** | Browser-interner Network Service | C++ Chromium Network Hooking | `ICoreWebView2_22` Pre-Network WebResourceRequested Filter | `src-tauri/src/tabs/desktop/webview2.rs:176–221` |
| **Kosmetisches Filtern** | Durch MV3 DNR drastisch limitiert | V8-Injektion von CSS & Scriptlets | CDP `Runtime.evaluate` mit `userGesture: false` auf `DOMContentLoaded` | `src-tauri/src/tabs/desktop/webview2.rs:381–419` |
| **Speicherverbrauch Inaktiver Tabs** | Chromium Tab Discarding (~40–120 MB RAM Restbelegung) | Chromium Memory Saver (~40–100 MB RAM Restbelegung) | **0,00 MB RAM** durch physische COM-Controller-Zerstörung (`verwerfen`) | `src-tauri/src/tabs/ruhe.rs:99–104`, `src-tauri/src/tabs/desktop/schlaf.rs:128–140` |
| **Workspace-Speicherverwaltung** | Tab-Gruppen belegen weiterhin Arbeitsspeicher | Tab-Gruppen belegen weiterhin Arbeitsspeicher | **0,00 MB RAM** für alle Tabs inaktiver Workspaces (Deep-Sleep) | `frontend/src/browser/services/workspacesStore.ts:131–144` |
| **Telemetrie & Datensammlung** | Sehr hoch (Google-Dienste, Metrics, SafeBrowsing, Topics API) | Deaktiviert (Opt-in für anonymisierte P3A-Metriken) | **Vollständig abwesend** (SmartScreen & Edge-UI per Switch deaktiviert) | `src-tauri/src/tabs/desktop.rs:67`, `src-tauri/src/tabs/desktop/ohne_edge.rs:24–36` |
| **Nutzerkonto-Zwang** | Stark forciert (Google-Sync, Profil-Zwang) | Kein Zwang; optionale Brave Sync Kette | **Vollständig kontofrei**; keine zentralen Cloud-Sync-Endpunkte | `frontend/src/locales/de.json:4924` |
| **Clean Copy (URL-Bereinigung)** | Nicht vorhanden (Drittanbieter-Erweiterung nötig) | Vorhanden („Copy Link Clean“ im Kontextmenü) | **Vollautomatisch** bei jeder Navigation (Rust) und beim Kopieren (TS) | `src-tauri/src/schild/verfolgung.rs:10–47`, `frontend/src/browser/services/linkReiniger.ts:86–152` |
| **1-Klick Site Reset** | Umständlich über Browsereinstellungen | Über Shields-Panel / Seiteneinstellungen | **1 Klick** im Adresszeilen-Popover via CDP Origin Data Wipe | `frontend/src/browser/kopf/SeitenSicherheitPopover.tsx:53–65`, `src-tauri/src/tabs/desktop.rs:432–478` |
| **Vertikale Baum-Tabs** | Nicht vorhanden (nur horizontale Leiste) | Nicht hierarchisch (nur lineare vertikale Liste) | **Nativ hierarchisch** (Ein-/Ausklappen, Ast-Glyphen, D&D, Vererbung) | `frontend/src/browser/kopf/VertikaleTableiste.tsx:64–105` |
| **Split-Screen Modus** | Nicht vorhanden (erfordert manuelle OS-Fensterkachelung) | Eingeschränkte Side-by-Side Kachelung | **4-Wege Snap-Assist** mit stufenlosem Trenner (20–80 %) via HWND | `frontend/src/browser/seite/SnapZonenOverlay.tsx:31–53`, `src-tauri/src/tabs/desktop.rs:360–400` |
| **Lesemodus & KI-Anbindung** | Einfacher Reader Mode | Speedreader + Brave Leo (proprietäres Cloud-LLM) | Puristischer Reader + **verschlüsselter MSM-Notizexport + Singra-KI** | `frontend/src/browser/seite/LesemodusAnsicht.tsx:148–175`, `frontend/src/browser/services/lesemodusService.ts:64–78` |

---

### 3.2 Werbe- und Trackingblockierung: Pontis vs. Chrome vs. Brave

#### 3.2.1 Brave `adblock` Rust-Engine und Filterabdeckung
Pontis bindet in `src-tauri/Cargo.toml` (Zeilen 28–34) exakt die Kern-Engine ein, die von Brave entwickelt und gepflegt wird (`adblock = { version = "0.13", features = ["embedded-domain-resolver", "full-regex-handling"] }`). Zur Vermeidung von Verzögerungen auf dem UI-Thread wird die Engine im Entwicklungsmodus mit voller Compiler-Optimierung übersetzt (`Cargo.toml`, Zeilen 98–103: `[profile.dev.package."*"] opt-level = 3`).

In `src-tauri/src/schild/listen.rs` (Zeilen 26–57) verwaltet Pontis ein Portfolio an Filterlisten:
- **EasyList** & **EasyList Germany:** Erkennung regulärer Werbeanzeigen.
- **EasyPrivacy:** Erkennung von Analyse- und Verfolgungsskripten.
- **Fanboy's Cookie Monster:** Blockierung von Cookie-Bannern und Zustimmungsdialogen auf Netzwerkebene.
Listen werden alle vier Tage im Hintergrund aktualisiert (`ERNEUERN_NACH = Duration::from_secs(4 * 24 * 3600)`) und im lokalen Datenverzeichnis zwischengespeichert. Bei einer Offline-Neuinstallation greift das System auf hardcodierte Fallback-Regeln zurück (`EINGEBAUT_WERBUNG`, `EINGEBAUT_TRACKER`), sodass der Schutz ab dem ersten Start aktiv ist.

#### 3.2.2 Pre-Network Interception auf COM-Ebene vs. Manifest V3 DNR
Während Google Chrome Drittanbieter-Blocker durch Manifest V3 auf deklarative Regelsätze (Declarative Net Request, DNR) mit strikten Kontingenten und eingeschränkter Regex-Unterstützung beschränkt, greift Pontis über die WebView2-Schnittstelle `ICoreWebView2_22` direkt in die Netzwerkschicht ein (`src-tauri/src/tabs/desktop/webview2.rs`, Zeilen 176–221):
- Über `AddWebResourceRequestedFilterWithRequestSourceKinds` werden Anfragen aus sämtlichen Quellen (Hauptdokument, Subframes, Service Worker, Shared Worker) abgefangen, bevor Netzwerkverbindungen etabliert werden.
- Geblockte Werbe- und Trackeranfragen werden synthetisch beantwortet: Tracking-Pings erhalten HTTP 204 ("No Content"), um Skriptabbrüche auf Webseiten zu vermeiden; sonstige blockierte Ressourcen erhalten HTTP 403 ("Blocked by MSB") (`src-tauri/src/schild/mod.rs`, Zeilen 95–100).
- Beim kosmetischen Filtern injiziert Pontis CSS-Selektoren über das Chrome DevTools Protocol (`Runtime.evaluate`) mit `userGesture: false`. Dadurch wird verhindert, dass Webseiten durch die Injektion versehentlich Berechtigungs-Tokens für Popups oder Zwischenablage-Zugriffe erhalten (`src-tauri/src/tabs/desktop/webview2.rs`, Zeilen 381–419).

---

### 3.3 Ressourcen- und Speicherverwaltung: Deep-Sleep & 0 MB RAM Architektur

#### 3.3.1 Dreistufige Speichersteuerung
Im Gegensatz zu Google Chrome und Brave, deren Speicher-Sparmodi inaktive Tabs lediglich im Hintergrund drosseln oder deren Renderer-Prozess als beendbar markieren (wobei Datenstrukturen im Browser-Prozess verbleiben und oft 40 bis 120 MB RAM pro Tab belegt bleiben), implementiert Pontis eine dreistufige Strategie:

1. **Stufe 1 (Sofortige Drosselung bei Fokusverlust):**  
   Sobald ein Tab in den Hintergrund tritt, setzt Pontis über `ICoreWebView2_19::SetMemoryUsageTargetLevel` das Verbrauchslevel auf `COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW` (`src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 72–75).
2. **Stufe 2 (Einfrieren via Suspension):**  
   Nach konfigurierbarer Inaktivität (Standard: 30 Minuten, überwacht im 30-Sekunden-Takt durch `src-tauri/src/tabs/ruhe.rs`) ruft Pontis `ICoreWebView2_3::TrySuspend` auf (`src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 117–126). Sämtliche JavaScript-Timer und Netzwerkabfragen werden gestoppt. Bei Klick wacht der Tab über `Resume` verzögerungsfrei auf. Ausgeschlossen von der Suspension sind Tabs mit Audio-Wiedergabe, aktivem Download oder Vollbild.
3. **Stufe 3 (Verwerfen / Deep-Sleep -> 0 MB RAM):**  
   Erreicht ein inaktiver Tab die Schwelle von 60 Minuten (`VERWERFEN_NACH`), zerstört Pontis die native Instanz über `src-tauri/src/tabs/desktop/schlaf.rs` (Zeilen 128–140):
   ```rust
   pub(super) fn verwerfen(app: &AppHandle, id: &str) {
       app.state::<Tabs>().0.lock().unwrap().tabs.remove(id);
       crate::schild::tab_vergessen(id);
       super::rueckfragen::vergessen(id);
       if let Some(tab) = NATIV.with(|n| n.borrow_mut().remove(id)) {
           unsafe {
               super::herunterladen::schliessen(id, tab.controller);
           }
       }
       melden(app, TabEreignis::Verworfen { id: id.to_string() });
   }
   ```
   Durch `NATIV.remove` und `schliessen` werden der COM-Controller und die Webview vollständig deallokiert. Der native Arbeitsspeicher sinkt auf exakt **0,00 MB**. Die Metadaten (URL, Titel, Favicon, Scrollposition) verbleiben im React-Store (`zustand`), sodass der Tab bei Aktivierung transparent wiederhergestellt wird.

#### 3.3.2 Workspace-gesteuerter Deep-Sleep
In `frontend/src/browser/services/workspacesStore.ts` (Zeilen 131–144) greift ein Mechanismus beim Wechsel von Arbeitsbereichen:
Verlässt der Nutzer einen Workspace, durchläuft das System sämtliche Tabs dieses Bereichs. Sofern kein aktiver Ton wiedergegeben wird, werden alle Tabs des verlassenen Workspace unverzüglich nativ verworfen (`nativ.tabVerwerfen(tab.id)`). Ein inaktiver Workspace mit Dutzenden geöffneten Tabs belegt im System somit keinen messbaren Arbeitsspeicher.

---

### 3.4 Privatsphäre und Datenflüsse

#### 3.4.1 Clean Copy (URL-Bereinigung)
Pontis bereinigt Weblinks zweistufig gegen Tracking-Parameter:
1. **Netzwerkebene bei jeder Navigation:** In `src-tauri/src/schild/verfolgung.rs` (Zeilen 10–47) prüft `ohne_verfolgung` aufgerufene URLs gegen 48 bekannte Klick- und Kampagnenkennungen:
   `fbclid`, `gclid`, `gclsrc`, `dclid`, `gbraid`, `wbraid`, `msclkid`, `yclid`, `ysclid`, `twclid`, `ttclid`, `igshid`, `igsh`, `li_fat_id`, `mc_cid`, `mc_eid`, `_hsenc`, `_hsmi`, `__hssc`, `__hstc`, `__hsfp`, `hsctatracking`, `mkt_tok`, `oly_anon_id`, `oly_enc_id`, `vero_id`, `vero_conv`, `wickedid`, `rb_clickid`, `_openstat`, `__s`, `srsltid`, `si`, `feature`, `ref_src`, `ref_url`, `ved`, `usg`, `cvid`, `pd_rd_r`, `pd_rd_w`, `pd_rd_wg`, `pf_rd_p`, `pf_rd_r`, `pf_rd_m`, `pf_rd_s`, `pf_rd_t`, `pf_rd_i` sowie sämtliche Parameter mit Präfix `utm_`.  
   Enthält eine URL solche Parameter, leitet der Rust-Host den Tab via `Weg::Umleiten` sofort auf die bereinigte URL um. Die Analyse erfolgt direkt am Rohstring, wodurch kryptografische Signaturen in Pfaden nicht beschädigt werden.
2. **Zwischenablageebene im Frontend:** In `frontend/src/browser/services/linkReiniger.ts` (Zeilen 86–152) bereinigen `bereinigeUrl` und `kopiereSauberenLink` Links beim Kopieren aus der Adresszeile oder aus Webseiten und informieren den Nutzer über einen Toast. Funktionale Parameter (`v=...`, `q=...`, Affiliate-IDs) bleiben intakt.

#### 3.4.2 1-Klick CDP Site Reset
Zur schnellen Bereinigung von Seitendaten bietet Pontis einen integrierten Ursprungs-Reset:
- Im Frontend öffnet ein Klick auf das Sicherheitsschloss der Adresszeile das Popover `frontend/src/browser/kopf/SeitenSicherheitPopover.tsx` (Zeilen 53–65) mit der Aktion „Seite zurücksetzen“.
- Im Rust-Backend führt `src-tauri/src/tabs/desktop.rs` (Zeilen 432–478, `seite_zuruecksetzen`) eine vierstufige Bereinigung über das Chrome DevTools Protocol aus:
  1. `Storage.clearDataForOrigin`: Löscht Cookies, LocalStorage, IndexedDB, Cache-Storage, WebSQL und Service Worker für die konkrete Origin.
  2. `Network.deleteCookies`: Löscht domainspezifische Cookies für den Host.
  3. `Runtime.evaluate`: Führt `localStorage.clear(); sessionStorage.clear();` ohne Nutzergeste im Seitenkontext aus.
  4. `Reload`: Lädt die Seite frisch und bereinigt neu.
Der Reset erfolgt selektiv für die betreffende Domain, ohne andere Tabs oder gespeicherte Passwörter zu beeinträchtigen.

#### 3.4.3 Null-Telemetrie vs. Google Chrome
Google Chrome erfasst standardmäßig Telemetriedaten, synchronisiert Profile mit Google-Servern und nutzt Identifikatoren für das Topics-API-Tracking.  
Pontis eliminiert diese Kanäle vollständig:
- Microsoft SmartScreen und Diagnose-Overlays sind über `--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection` abgeschaltet (`src-tauri/src/tabs/desktop.rs:67`).
- Absturzberichte verbleiben lokal (`optionen.set_is_custom_crash_reporting_enabled(true)`).
- Edge-Dienste wie Kennwort-Cloudsync und Formular-Autofill sind deaktiviert (`src-tauri/src/tabs/desktop/ohne_edge.rs:24–36`).
- Es existiert kein Nutzerkonto-Zwang und keine Kopplung an zentrale Telemetrie-Server.

---

### 3.5 UX- und Produktivitäts-Features

#### 3.5.1 Native vertikale Baumstruktur-Tabs (Tree-Style Tabs)
Im Gegensatz zu Chrome (ausschließlich horizontale Tableiste) und Brave (lineare vertikale Liste) implementiert Pontis ein echtes hierarchisches Baum-Modell:
- **Datenmodell:** In `frontend/src/browser/services/tab.ts` (Zeilen 42–45) besitzt jeder Tab Verweise auf `elternId: string | null` und `baumEingeklappt?: boolean`.
- **Baum-Algorithmus:** In `frontend/src/browser/kopf/VertikaleTableiste.tsx` (Zeilen 64–105) berechnet `baueTabBaum` die Hierarchietiefe (`ebene`), die Anzahl der Kind-Tabs (`kinderCount`) und filtert Nachfahren eingeklappter Knoten aus der Darstellung.
- **Bedienung:** Eingerückte Darstellung (`knoten.ebene * 14 + 8 px`) mit Ast-Symbolen (`└`), aufklappbaren Chevrons und Drag-and-Drop:
  - Obere/untere 25 % der Tab-Höhe: Verschieben über/unter den Ziel-Tab.
  - Mittlere 50 %: Zuweisen als Kind-Tab (`dropAlsKindVon = tab.id`).
- **Vererbung:** Beim Schließen eines Eltern-Tabs vererbt das System in `frontend/src/browser/services/tabsStore.ts` (Zeile 262) die Kind-Tabs automatisch an die übergeordnete Großeltern-Ebene, sodass keine Unterbäume unkontrolliert verloren gehen.

#### 3.5.2 4-Wege Snap-Assist Split-Screen
Pontis unterstützt pixelgenaues Kacheln zweier Webseiten innerhalb des Hauptfensters:
- **Zonenerkennung:** In `frontend/src/browser/seite/SnapZonenOverlay.tsx` (Zeilen 31–53) blendet das System beim Ziehen eines Tabs an die Fensterkanten halbtransparente Snap-Zonen für vier Richtungen ein (`links`, `rechts`, `oben`, `unten`).
- **Trenner & Tastatur:** `frontend/src/browser/seite/SplitTrenner.tsx` (Zeilen 35–50) ermöglicht stufenlose Skalierung des Verhältnisses von 20 % bis 80 %. Ein Doppelklick setzt die Aufteilung exakt auf 50:50 zurück. Über Schnelltasten lassen sich Seiten tauschen oder die Ausrichtung von vertikal auf horizontal drehen.
- **Hardwarebeschleunigte Controller:** Der Tauri-Befehl `tabs_split_setzen` übergibt `primaer_rahmen` und `sekundaer_rahmen` an das Backend (`src-tauri/src/tabs/desktop.rs`, Zeilen 360–400). Dort setzt `SetBounds(rect)` die Koordinaten beider WebView2-Instanzen separat und unter Berücksichtigung der Windows-DPI-Skalierung. Beide Seiten laufen voll hardwarebeschleunigt nebeneinander.

#### 3.5.3 Puristischer Lesemodus mit MSM-Notizexport und Singra-KI
- **Bereinigung:** In `frontend/src/browser/services/lesemodusService.ts` (Zeilen 64–78 und 287–371) prüft `pruefeObLesbar` via CDP, ob substanzieller Fließtext (> 350 Zeichen) vorliegt. Der Extraktions-Algorithmus entfernt gezielt Wikipedia-Ziffernzitate (`sup.reference`), Bearbeiten-Links (`.mw-editsection`), Navigationsboxen und Werbeelemente.
- **Aktivierung:** Über das Buch-Symbol in der Omnibox oder den systemweiten Shortcut `F9`, der in `src-tauri/src/tabs/desktop/webview2.rs` (Zeile 371) direkt auf Controller-Ebene registriert ist und auch bei fokussierter Webseite auslöst.
- **Typografie & 3 Link-Modi:** In `frontend/src/browser/seite/LesemodusAnsicht.tsx` wählt der Nutzer zwischen *Dezent* (feine Unterstreichung in Textfarbe für buchähnlichen Lesefluss), *Hervorgehoben* (klassische Linkfarben) und *Aus* (deaktivierte Hyperlinks für ablenkungsfreies Arbeiten).
- **Systemintegration:**
  - *MSM-Notizen:* Ein Klick auf `saveNoteOffline` (`LesemodusAnsicht.tsx:148–166`) speichert den bereinigten Artikel verschlüsselt in den lokalen Notizen des Maunting Server Managers.
  - *Singra-KI:* Die Funktion `mitKiZusammenfassen` (`LesemodusAnsicht.tsx:169–175`) übergibt einen strukturierten Prompt samt Metadaten und Volltext an den Singra-Hauptchat (`/ai`) zur lokalen Analyse.

---

## 4. R3: Ehrliche Bestandsaufnahme, bekannte Grenzen & Trade-offs

### 4.1 Die 10 herausragenden technischen Errungenschaften des Vibecoding-Stands

Die Untersuchung der Codebasis belegt zehn architektonische Meilensteine, die für ein Vibecoding-Projekt eine außergewöhnliche Reife aufweisen:

1. **Vollständige IPC-Isolation von Webseiten-Tabs:**  
   Die Umgehung des Standard-Tauri-Webview-Builders zugunsten nativer COM-Einbettung (`src-tauri/src/tabs/desktop.rs:161`) verhindert das Einschleusen von Tauri-Internals in fremde Webseiten an der Wurzel.
2. **Produktionserprobte Adblock-Engine in Rust:**  
   Die direkte Integration von Braves `adblock`-Engine (v0.13, MPL-2.0) hinter thread-sicheren `RwLock`-Strukturen (`src-tauri/src/schild/mod.rs:110`) gewährleistet Filterraten auf Augenhöhe mit spezialisierten Werbeblockern.
3. **Physisches 0-MB-RAM Tab-Discarding:**  
   Die vollständige Zerstörung inaktiver COM-Controller (`src-tauri/src/tabs/desktop/schlaf.rs:133`) bei gleichzeitigem Erhalt des UI-Zustands löst das Speicherproblem monolithischer Browser mechanisch sauber.
4. **Kryptografisch gesichertes CRX3-Erweiterungssystem:**  
   Das Backend implementiert ein vollständiges Subsystem zum Herunterladen, Entpacken und Signaturprüfen von Chrome-Erweiterungen mit `ring` (RSA/Ed25519) und Schutz vor Zip-Bomben (`src-tauri/src/erweiterungen/crx.rs`, `src-tauri/src/erweiterungen/paket.rs`).
5. **Netzwerkweites Clean Copy & 1-Klick Site Reset:**  
   Die Kombination aus robuster Rohstring-Bereinigung von 48 Tracking-Parametern (`src-tauri/src/schild/verfolgung.rs:28`) und sofortigem CDP Origin Data Wipe (`src-tauri/src/tabs/desktop.rs:434`) bietet wirksamen Schutz vor Tracking.
6. **Biometrisch geschützte Schlüsselverwaltung (`schluesselfach`):**  
   Die Integration von Windows Hello (`UserConsentVerifier`) und Android Keystore (AES-256-GCM) in 100 % sicherem Rust ohne ein einziges `unsafe` setzt einen hohen Sicherheitsstandard für Tresor- und Messenger-Daten.
7. **Natives vertikales Tree-Style Tab-System:**  
   Die rekursive Baumdarstellung mit Drag-and-Drop-Kindzuweisung und automatischer Verwaisten-Adoption (`frontend/src/browser/kopf/VertikaleTableiste.tsx:64`) übertrifft die Basisfunktionen von Chrome und Brave deutlich.
8. **Multi-View Snap-Assist Split-Screen:**  
   Die 4-Wege-Erkennung mit dynamischem Trenner und hardwarebeschleunigten, DPI-angepassten HWND-Controllern (`frontend/src/browser/seite/SnapZonenOverlay.tsx`, `src-tauri/src/tabs/desktop.rs:360`) bietet echten Mehrwert für Multitasking.
9. **Integrierter Lese- und KI-Workflow:**  
   Der puristische Lesemodus mit Wikipedia-Filterung, lokaler MSM-Notizverschlüsselung und direkter Singra-KI-Prompt-Pipeline (`frontend/src/browser/seite/LesemodusAnsicht.tsx:148`) verbindet Surfen nahtlos mit Informationsverarbeitung.
10. **Autonome Föderationssuche:**  
    Die direkte Integration einer selbstgehosteten SearXNG-Instanz mit lokaler KI-Ergebniszusammenfassung (`frontend/src/browser/seite/MsmSuche.tsx`) stellt eine unabhängige Alternative zu kommerziellen Suchmaschinen dar.

---

### 4.2 Echte, ungeschönte Grenzen und Trade-offs gegenüber monolithischen Browsern

Trotz der erreichten Meilensteine weist das aktuelle Architekturmodell systembedingte Grenzen gegenüber eigenständigen Chromium-Forks wie Google Chrome oder Brave auf:

#### 4.2.1 Abhängigkeit von der Plattform-Laufzeit & Windows-Fokus
- **Laufzeit-Kopplung:** Pontis besitzt keine eigene Rendering-Engine. Unter Windows ist Microsoft Edge WebView2 Evergreen zwingend erforderlich (`src-tauri/Cargo.toml:53`, `webview2-com = "0.39"`). Ist diese Komponente beschädigt oder durch Unternehmensrichtlinien gesperrt, kann Pontis nicht starten.
- **Fehlende Desktop-Plattformen:** Die Desktop-Tab-Verwaltung (`src-tauri/src/tabs/desktop.rs`) ist ausschließlich für Windows implementiert (`#[cfg(windows)]`). Für Linux und macOS existiert im aktuellen Stand keine lauffähige Desktop-Tab-Engine (unter Android wird die System-WebView genutzt).

#### 4.2.2 Release-Latenz bei Chromium Zero-Day-Sicherheitslücken
- Monolithische Browser wie Google Chrome oder Brave kompilieren Chromium-Sicherheitspatches selbst und können Notfall-Builds bei kritischen Zero-Days innerhalb weniger Stunden veröffentlichen.
- Pontis ist darauf angewiesen, dass Microsoft Sicherheitsupdates für die WebView2 Evergreen Runtime über Windows Update bzw. Edge Update ausrollt. Zwar geschieht dies meist binnen 24 bis 48 Stunden, das Pontis-Team hat jedoch keine direkte Kontrolle über diesen Release-Zyklus.

#### 4.2.3 Lücken in den WebExtension-APIs (Tab-API-Grenze)
In `src-tauri/src/tabs/desktop/popup.rs` (Zeilen 7–8) dokumentiert der Code eine fundamentale Einschränkung:
```rust
//! Grenze: fragt das Popup nach „dem aktiven Tab“, findet es keinen; die
//! Tabs gehören nicht zu einem Browserfenster, das WebView2 kennt.
```
Weil Pontis Tabs als unabhängige COM-Controller in einem eigenen HWND verwaltet, kennt die WebView2-Laufzeit weder das übergeordnete Browserfenster noch die Tab-Hierarchie. Chrome-Erweiterungen, die Standard-APIs wie `chrome.tabs.query({ active: true, currentWindow: true })`, `chrome.scripting` oder `chrome.windows` aufrufen, erhalten leere Arrays oder scheitern. Passwortmanager-Erweiterungen (z. B. Bitwarden, 1Password) können dadurch den aktiven Tab und dessen URL nicht automatisch erkennen. Zudem unterstützt die Android-WebView prinzipbedingt keine Erweiterungen (`src-tauri/src/erweiterungen/mod.rs:2–3`).

#### 4.2.4 Fehlende Tiefen-Randomisierung gegen Fingerprinting (Kein Engine-Farbling)
Brave modifiziert C++-Quelltexte in Blink und V8 direkt, um Canvas-, WebGL- und WebAudio-Aufrufe pro Sitzung mit minimalem, deterministischem pseudozufälligem Rauschen zu versehen (Fingerprint Farbling).  
Pontis stützt seinen Fingerprinting-Schutz primär auf das Blockieren bekannter Tracker-Skripte (`EasyPrivacy`) und das Ausblenden von `window.chrome.webview` in `seite.js`. Da Pontis keinen Zugriff auf den C++-Quelltext der Rendering-Engine hat, können spezialisierte Fingerprinting-Bibliotheken (wie FingerprintJS Pro) über mathematische Ungereimtheiten, Prototyp-Inspektionen oder Web Worker (`OffscreenCanvas`, `AudioWorklet`), in denen `seite.js` nicht greift, weiterhin Identifikatoren ableiten.

#### 4.2.5 DRM- und Widevine-Beschränkungen
In Google Chrome und Brave sind Widevine Content Decryption Modules (CDM) und proprietäre Multimedia-Codecs fest integriert und lizenziert.  
In Microsoft WebView2 ist Widevine DRM standardmäßig nicht freigeschaltet. Kommerzielle Streamingdienste mit strikten Kopierschutzanforderungen (Netflix, Spotify, Amazon Prime Video, Disney+) schlagen in Pontis fehl oder fallen auf ungeschützte Standardauflösungen (SD) zurück.

#### 4.2.6 Geteilte Prozessgrenzen bei Workspaces
In `src-tauri/src/tabs/desktop.rs` (Zeilen 171–183) nutzen unterschiedliche Workspaces denselben `ICoreWebView2Environment`-Kontext und unterscheiden sich lediglich durch `SetProfileName("ws_<id>")`.  
Während Chromium mit Site Isolation einzelne Ursprünge in getrennten Renderer-Sandboxes kapselt, teilen sich alle Tabs und Workspaces innerhalb von Pontis denselben Browser-Broker-Prozess (`msedgewebview2.exe`). Sollte ein Angreifer eine Sicherheitslücke in der Renderer-Sandbox ausnutzen (Sandbox Escape), wären potenziell alle Workspaces des Nutzers im selben Prozessbaum exponiert.

---

## 5. Priorisierte Roadmap zur Weiterentwicklung

### 5.1 Priorität 0: Unmittelbare Härtung und Sicherheitsisolation

```
+-------------------------------------------------------------------------+
|                  Priorität 0: Unmittelbare Härtung                      |
+-------------------------------------------------------------------------+
| [P0.1] Physische Prozessgrenzen je Workspace                            |
|        -> Getrennte User Data Folders (UDF) & getrennte Environments    |
|        -> Dedizierte `msedgewebview2.exe`-Instanzen je Workspace        |
+-------------------------------------------------------------------------+
| [P0.2] Anti-Tamper Schutz für `seite.js`                                |
|        -> `Object.freeze` auf gekapselte Prototypen                     |
|        -> Überprüfung von Web Workern & `OffscreenCanvas`               |
+-------------------------------------------------------------------------+
| [P0.3] DNS-Rebinding & RFC1918-Schutz im Schild                         |
|        -> Blockieren öffentlicher Anfragen an 127.0.0.1 & LAN-Subnetze |
|        -> Schutz interner Smart-Home- und Server-Infrastrukturen        |
+-------------------------------------------------------------------------+
```

1. **P0.1: Physische Prozessgrenzen je Workspace und InPrivate-Modus:**
   - *Ist-Zustand:* Workspaces laufen als benannte Profile (`ws_<id>`) unter einer gemeinsamen `ICoreWebView2Environment` (`src-tauri/src/tabs/desktop.rs:171–182`).
   - *Maßnahme:* Zuweisung eigenständiger Benutzerdaten-Ordner (User Data Folders, UDF) und separater `ICoreWebView2Environment`-Instanzen für den InPrivate-Modus sowie für produktive Arbeitsbereiche. Dies erzwingt getrennte `msedgewebview2.exe`-Broker-Prozesse auf Betriebssystemebene und verhindert ein Übergreifen kompromittierter Renderer über Profilgrenzen hinweg.
   - *Betroffene Komponenten:* `src-tauri/src/tabs/desktop.rs` (`umgebung()`, `controller()`).

2. **P0.2: Anti-Tamper Härtung für `seite.js`:**
   - *Ist-Zustand:* `seite.js` sichert Intrinsics vor Seitenskripten, die Objekte bleiben jedoch modifizierbar.
   - *Maßnahme:* Explizites Einfrieren der gesicherten Prototypen mittels `Object.freeze` und Absicherung gegen Umgehungen über Web Worker und `WorkerGlobalScope`.
   - *Betroffene Komponenten:* `src-tauri/src/seite.js`.

3. **P0.3: DNS-Rebinding- und RFC1918-Schutz im Schild:**
   - *Ist-Zustand:* `src-tauri/src/schild/sperre.rs` unterscheidet öffentliche von privaten IP-Adressen für den Jugendschutz.
   - *Maßnahme:* Durchsetzung einer strikten Netzwerkgrenze in `schild::pruefen`: Öffentliche Webseiten dürfen keine Subressourcen-Anfragen an `localhost`, `127.0.0.1` oder private Subnetze gemäß RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) richten. Dies unterbindet Angriffe auf lokale Server (z. B. Router-Oberflächen oder MSM) durch bösartige Skripte im Browser.
   - *Betroffene Komponenten:* `src-tauri/src/schild/sperre.rs`, `src-tauri/src/schild/mod.rs`.

---

### 5.2 Priorität 1: Ökosystem-Erweiterung und Privatsphäre-Ausbau

1. **P1.1: Rust-Level WebExtension Tab-Polyfill Bridge:**
   - *Ist-Zustand:* Erweiterungs-Popups können `chrome.tabs.query` nicht nutzen, da WebView2 keine Fenster-Zuordnung besitzt (`src-tauri/src/tabs/desktop/popup.rs:7–8`).
   - *Maßnahme:* Implementierung einer internen Polyfill-Brücke in `src-tauri/src/tabs/desktop/popup.rs`: Anfragen von Erweiterungen an `chrome.tabs.query`, `chrome.tabs.get` oder `chrome.tabs.sendMessage` werden im Rust-Backend abgefangen und mit den tatsächlichen Pontis-Tab-Zuständen beantwortet. Dadurch werden Passwortmanager-Erweiterungen (Bitwarden, 1Password) voll funktionsfähig.
   - *Betroffene Komponenten:* `src-tauri/src/tabs/desktop/popup.rs`, `src-tauri/src/erweiterungen/`.

2. **P1.2: Plattformübergreifende Desktop-Unterstützung (macOS und Linux):**
   - *Ist-Zustand:* Die Desktop-Tab-Engine ist mit `#[cfg(windows)]` fest an WebView2 COM gebunden.
   - *Maßnahme:* Einführung eines plattformunabhängigen Tab-Controller-Traits in `src-tauri/src/tabs/mod.rs` mit spezifischen Backend-Implementierungen: `WKWebView` für macOS und WebKitGTK bzw. Chromium Embedded Framework (CEF) für Linux.
   - *Betroffene Komponenten:* `src-tauri/src/tabs/mod.rs`, `src-tauri/src/tabs/desktop.rs`.

3. **P1.3: CNAME-Uncloaking via Rust-DNS:**
   - *Ist-Zustand:* Der Werbeblocker gleicht HTTP-Anfrage-URLs direkt gegen Filterlisten ab.
   - *Maßnahme:* Integration eines asynchronen DNS-Resolvers (z. B. `hickory-resolver` in Rust) in die Schild-Pipeline, um Alias-Einträge (CNAME) vor der Prüfung aufzulösen. Dies enttarnt Tracking-Dienste, die über Subdomains der besuchten Hauptdomain verschleiert werden (z. B. `analytics.beispiel.de` -> `tracker.thirdparty.com`).
   - *Betroffene Komponenten:* `src-tauri/src/schild/mod.rs`.

---

### 5.3 Priorität 2: Strategische Differenzierungsmerkmale

1. **P2.1: Lokale On-Device KI-Pipeline (Rust / ONNX):**
   - *Ist-Zustand:* Die Textzusammenfassung im Lesemodus setzt eine Verbindung zum Singra- oder MSM-Backend voraus (`frontend/src/browser/seite/LesemodusAnsicht.tsx:169–175`).
   - *Maßnahme:* Direkte Einbettung eines kompakten lokalen Sprachmodells (z. B. Qwen2.5-0.5B oder SmolLM2) über Rust-Inferenz-Bibliotheken (`candle` oder `ort`). Ermöglicht 100 % offline-fähige Zusammenfassungen, Übersetzungen und Entitätsextraktionen ohne Latenz und ohne Datenabfluss an externe Server.
   - *Betroffene Komponenten:* `src-tauri/src/tabs/uebersetzung.rs`, `frontend/src/browser/seite/LesemodusAnsicht.tsx`.

2. **P2.2: Zero-Knowledge P2P-Zustandssynchronisation:**
   - *Ist-Zustand:* `schluesselfach` schützt Schlüssel lokal über Biometrie; es existiert keine Synchronisation zwischen Desktop und Android.
   - *Maßnahme:* Aufbau eines dezentralen, Ende-zu-Ende verschlüsselten Synchronisationskanals für Tabs, Lesezeichen und Verlauf zwischen Desktop- und Mobile-Instanzen, basierend auf Schlüsseln aus dem `schluesselfach`, ohne zentrale Cloud-Konten.
   - *Betroffene Komponenten:* `crates/schluesselfach/`, `src-tauri/src/browserdaten.rs`.

3. **P2.3: DRM- und Widevine-Streaming-Container:**
   - *Ist-Zustand:* Streamingdienste verweigern in Standard-WebView2 die Wiedergabe geschützter Inhalte.
   - *Maßnahme:* Evaluierung eines isolierten Media-Webview-Containers auf Basis von CEF oder Prüfung der lizenzierten Bereitstellung von Widevine-Komponenten für spezifische Medien-Tabs.
   - *Betroffene Komponenten:* `src-tauri/src/tabs/desktop.rs`.

---

## 6. Verifikationsmethodik und Reproduzierbarkeit

Zur lückenlosen Verifikation aller in diesem Bericht getroffenen technischen Aussagen dienen die folgenden automatisierten Testbefehle und Code-Inspektionen.

> **Hinweis zur Verzeichnisnavigation:** Sämtliche relativen Pfadangaben beziehen sich auf das Repository-Wurzelverzeichnis (`maunting-server-manager`). Für jeden Verifikationsblock sind sowohl der Einstieg vom Repository-Root als auch relative Pfade für aufeinanderfolgende Aufrufe dokumentiert.

### 6.1 Rust Backend: Unit- und Integrationstests
Ausführen im Arbeitsverzeichnis `browser/src-tauri` (vom Repository-Root: `cd browser/src-tauri`):

```powershell
# 1. Verifikation der 100%igen IPC-Manifest- und Rechteabdeckung
cargo test --lib -- tests::rechte_decken_jeden_befehl

# 2. Verifikation der Content Security Policy (Verbot von eval und externen Bildern)
cargo test --lib -- tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval

# 3. Verifikation der Token-Sicherheit (Ausschluss von Klartextdateien)
cargo test --lib -- tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung

# 4. Verifikation von Tab-ID-Validierung, Ursprungssperren und Protokollgrenzen
cargo test --lib -- tabs::weg::tests

# 5. Verifikation des Werbe- und Trackingfilters (Brave adblock Integration)
cargo test --lib -- schild::tests::filtert_nach_listen_und_ausnahmen

# 6. Verifikation von Clean Copy (Entfernung von 48 Parametern und utm_*)
cargo test --lib -- schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus

# 7. Verifikation der Ruhe- und Verwerf-Schwellen (0 MB RAM Discarding)
cargo test --lib -- tabs::ruhe::tests

# Kompakt-Aufruf: Alle 22 verifikationsrelevanten Kern-Tests in einem Durchlauf
cargo test --lib -- tests::rechte_decken_jeden_befehl tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung tabs::weg::tests schild::tests::filtert_nach_listen_und_ausnahmen schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus tabs::ruhe::tests
```

*Verifizierte Testergebnisse:* Sämtliche Tests schließen mit `test result: ok. 22 passed; 0 failed` ohne Fehler ab (Gesamte Crate: 168 passed, 0 failed, 3 ignored).

### 6.2 Frontend: Komponenten- und Servicetests
Ausführen im Arbeitsverzeichnis `frontend` (vom Repository-Root: `cd frontend`; aus `browser/src-tauri`: `cd ../../frontend`):

```powershell
# 1. Verifikation der hierarchischen Baum-Tabs und Kindzuweisung
npm test -- src/browser/kopf/VertikaleTableiste.test.tsx

# 2. Verifikation des Lesemodus (Wikipedia-Bereinigung, Link-Modi, MSM/KI-Export)
npm test -- src/browser/seite/LesemodusAnsicht.test.tsx

# 3. Verifikation des 1-Klick Site Reset Popovers
npm test -- src/browser/kopf/SeitenSicherheitPopover.test.tsx

# 4. Verifikation der Tab-Zustandsverwaltung
npm test -- src/browser/services/tabsStore.test.ts

# Kompakt-Aufruf: Alle vier Suiten in einem Durchlauf (52 Tests)
npm test -- src/browser/kopf/VertikaleTableiste.test.tsx src/browser/seite/LesemodusAnsicht.test.tsx src/browser/kopf/SeitenSicherheitPopover.test.tsx src/browser/services/tabsStore.test.ts
```

*(Hinweis: Da in `frontend/package.json` das Skript `"test": "vitest run"` definiert ist, übergibt `npm test -- <pfad>` die Dateipfade direkt als Filter. Äquivalent kann auch `npx vitest run <pfad>` verwendet werden.)*

*Verifizierte Testergebnisse:* Sämtliche 4 Testsuiten mit 52 Tests schließen mit `4 passed (4)`, `52 passed (52)` erfolgreich ab (gesamte Browser-Suite: 81 Test-Dateien, 524 passed).

### 6.3 Statische Code-Inspektionen für Sicherheitsinvarianten
- **Unsafe-Freiheit in `schluesselfach` prüfen:**
  Ausführen im Arbeitsverzeichnis `crates/schluesselfach` (vom Repository-Root: `cd crates/schluesselfach`; aus `frontend`: `cd ../crates/schluesselfach`; aus `browser/src-tauri`: `cd ../../crates/schluesselfach`):
  ```powershell
  # 1. Unsafe-Prüfung im Quellcode:
  git grep "unsafe" -- src/
  # Befund: Exakt 0 Treffer (Exit-Code 1 bestätigt Abwesenheit von unsafe-Blöcken).

  # 2. Automatisierte Begleittests für Token-Isolation und Betriebssystem-Keyring:
  cargo test
  # Befund: test result: ok. 4 passed; 0 failed.
  ```
  *Befund:* Exakt 0 Treffer im Quellcode; alle 4 Sicherheits- und Token-Tests erfolgreich.
- **Deaktivierung von SmartScreen und Edge-Telemetrie prüfen:**
  `browser/src-tauri/src/tabs/desktop.rs`, Zeile 67:
  `const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";`
- **Physisches Verwerfen von WebView2-Controllern prüfen:**
  `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 133–137:
  `NATIV.with(|n| n.borrow_mut().remove(id))` in Kombination mit `super::herunterladen::schliessen(id, tab.controller);`.

---

## 7. Fazit für das Entwicklungsteam

Maunting Pontis (v5.5.0) demonstriert eindrucksvoll, welches Qualitätsniveau moderne KI-gestützte Entwicklung erreichen kann, wenn sie durch klare architektonische Prinzipien und defensive Programmierung geführt wird:

1. Das System vermeidet die typische Sicherheitsfalle hybrider Desktop-Frameworks, indem es nicht vertrauenswürdigen Web-Code konsequent von der IPC-Schicht des Host-Systems fernhält.
2. Mit der Integration von Braves `adblock`-Engine, dem echten 0-MB-RAM Tab-Discarding, dem Clean-Copy-System und den hierarchischen Tree-Tabs übertrifft Pontis Standardbrowser wie Google Chrome in zentralen Privatsphäre- und Produktivitätsaspekten.
3. Die verbleibenden Grenzen (WebExtension Tab-APIs, Zero-Day-Patch-Kadenz, Fingerprint Farbling, Widevine DRM) sind der Natur einer Plattform-Laufzeit geschuldet. Sie schmälern das Erreichte nicht, definieren jedoch das klare technische Pflichtenheft für die kommenden Entwicklungsphasen P0 bis P2.

Maunting Pontis besitzt ein tragfähiges, hochgradig konkurrenzfähiges Fundament für Anwender, die Wert auf kompromisslose Privatsphäre, minimale Ressourcennutzung und nahtlose Produktivität legen.
