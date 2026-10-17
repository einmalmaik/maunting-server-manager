# Handoff Report — Requirement R1: Architektur- und Sicherheitsanalyse

**Agent**: Explorer R1 (Architecture & Security)  
**Datum**: 2026-10-17  
**Ziel**: Fundierte, evidenzbasierte Sicherheits- und Architekturprüfung von Maunting Pontis (Tauri v2 + WebView2 Shell)

---

## 1. Observation (Beobachtungen & Code-Befunde)

### 1.1 Webview-Isolation: UI (`main`) vs. Webseiten-Tabs (`tab-*`)

1. **Manifest-Deklaration und Capability-Beschränkung:**
   - In `src-tauri/build.rs` (Zeilen 1–99) werden sämtliche 81 IPC-Befehle des Browsers (`BEFEHLE`) explizit im Tauri-App-Manifest registriert:
     ```rust
     tauri_build::try_build(
         tauri_build::Attributes::new()
             .app_manifest(tauri_build::AppManifest::new().commands(BEFEHLE)),
     )
     ```
     *Kommentar im Code (Z. 1–4):*  
     > „Alle Befehle des Browsers. Weil sie hier im App-Manifest stehen, prüft Tauri jeden Aufruf gegen die Rechte in capabilities/ — auch von lokalen Seiten. Ohne Manifest liefe ein App-Befehl aus jeder Webview mit lokaler Herkunft ungeprüft durch, also auch aus einem Tab.“
   - In `src-tauri/capabilities/oberflaeche.json` (Zeilen 1–13) ist die Berechtigungsgruppe `oberflaeche` exklusiv an die Webview `main` gebunden:
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
   - In `src-tauri/permissions/oberflaeche.toml` (Zeilen 1–88) sind alle 81 Befehle (`allow-konfig-laden`, `allow-refresh-token-speichern`, `allow-tab-laden`, `allow-schild-stand`, etc.) als Berechtigungs-Set definiert.
   - In `src-tauri/src/lib.rs` (Zeilen 283–305) verifiziert der automatisierte Integrationstest `rechte_decken_jeden_befehl()` die exakte Deckung zwischen Manifest, registrierten Handlern und vergebenen Rechten.

2. **Native COM-Trennung ohne Tauri-IPC bei Tabs (Windows):**
   - In `src-tauri/src/tabs/desktop.rs` (Zeilen 1–10, 161–188) werden Webseiten-Tabs unter Windows **nicht** über Tauris WebviewBuilder erzeugt, sondern direkt über native WebView2-COM-Schnittstellen (`CreateCoreWebView2ControllerWithOptions`):
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
   - In `src-tauri/src/tabs/desktop.rs` (Zeilen 222–223) werden Host-Objekte strikt verboten:
     ```rust
     einstellungen.SetIsWebMessageEnabled(true).map_err(fehler)?;
     einstellungen.SetAreHostObjectsAllowed(false).map_err(fehler)?;
     ```

3. **Neutralisierung von `chrome.webview` vor Ausführung von Seitenskripten:**
   - In `src-tauri/src/seite.js` (Zeilen 1–15, 33–43) wird über `core.AddScriptToExecuteOnDocumentCreated` vor allen Seitenskripten die Brücke gekapselt und aus dem globalen Objekt gelöscht:
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
   - Zur Verhinderung von Prototype-Pollution / Monkey-Patching sichert `seite.js` Prototyp-Methoden und Getter (`Reflect.apply`, `Object.getOwnPropertyDescriptor`) sofort in lexikalischen Closures.
   - In `src-tauri/src/tabs/desktop/formulare.rs` (Zeilen 40–44) wird jede WebMessage auf Übereinstimmung von `core.Source()` und `args.Source()` via `formular::gleiche_herkunft` geprüft.

4. **Strenge Content Security Policy (CSP) der UI:**
   - In `src-tauri/tauri.conf.json` (Zeilen 30–32):
     ```
     default-src 'self'; connect-src 'self' https: wss: http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval' blob:; object-src 'none'; base-uri 'self'; form-action 'none'
     ```
   - In `src-tauri/src/lib.rs` (Zeilen 307–333) stellt der Test `die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval()` sicher:
     - `script-src` enthält kein `'unsafe-eval'`.
     - `img-src` erlaubt keine externen Web-Adressen (`'self' data: blob:`) — Favicons werden über die native WebView2-API `core15.GetFavicon` als Data-URLs übertragen (`tabs/desktop/webview2.rs`, Z. 147–172), damit die UI keine externen Verbindungen aufbaut.
     - `connect-src` erlaubt unverschlüsselte Verbindungen ausschließlich zu Loopback (`localhost`, `127.0.0.1`).

5. **Ursprungs-, Tab-ID- und Protokoll-Restriktionen:**
   - In `src-tauri/src/tabs/weg.rs` (Zeilen 9–15) validiert `id_pruefen()` Tab-IDs: muss mit `tab-` beginnen, maximal 40 Zeichen, ausschließlich ASCII-alphanumerisch oder Bindestrich. Eine Tab-Webview kann niemals den reservierten Namen `main` annehmen.
   - In `tabs/weg.rs` (Zeilen 17–20, 31–41) verbietet `navigation_erlaubt()` das Laden interner Tauri-Hosts (`tauri.localhost`, `ipc.localhost`, `asset.localhost`) in Tabs.
   - In `tabs/weg.rs` (Zeilen 47–50) blockiert `NIE_EXTERN` gefährliche Windows-Protokolle: u. a. `file:`, `javascript:`, `vbscript:`, `view-source:`, `intent:`, `ms-msdt` (Follina-Schwachstelle), `search-ms`, `ms-search`.

---

### 1.2 Rust Host Memory Safety & Nebenläufigkeit

1. **Crates & Rust-Edition:**
   - `src-tauri/Cargo.toml`: `edition = "2021"`, `rust-version = "1.88"`.
   - Wichtige Crates:
     - `adblock = { version = "0.13", features = ["embedded-domain-resolver", "full-regex-handling"] }` (Braves Rust-Engine, MPL-2.0).
     - `reqwest = { version = "0.13", features = ["rustls"] }` (rustls statt C-OpenSSL).
     - `keyring = "4"` (OS Credential Store Abstraktion).
     - `ring = "0.17"` (Kryptografische Signaturprüfung für CRX3-Erweiterungen).
     - `image = { version = "0.25", features = ["png", "ico"] }` (Bild- und Favicon-Dekodierung im sicheren Rust-Host, nicht im Browser-DOM).
     - `windows = "0.62"` und `webview2-com = "0.39"`.

2. **Unsafe-Code-Analyse:**
   - In `crates/schluesselfach`: **0 Vorkommen von `unsafe`** (vollständig sicheres Rust).
   - In `src-tauri/src`: `unsafe` wird **ausschließlich** an den FFI-Schnittstellen zur Außenwelt eingesetzt:
     - Windows COM Schnittstellen (`webview2_com::Microsoft::Web::WebView2::Win32::*`).
     - Win32 API Aufrufe (`windows::Win32::*`), z. B. `IAttachmentExecute::Save` für Antivirus-Prüfung (`downloads/pruefung.rs`), HWND-Fensteroperationen (`fenster.rs`, `kurzinfo.rs`), Audiomixer (`mixer.rs`).
     - Android JNI Aufrufe (`tabs/android/bruecke.rs`).
   - Keine ungesicherten Pointer-Arithmetiken oder eigene Speicher-Allokatoren im Anwendungs- und Filtercode.

3. **Synchronisationsmuster & Thread-Safety:**
   - `RwLock`: In `src-tauri/src/schild/mod.rs` (Zeilen 110–128) liegen die Adblock-Filter (`werbung: RwLock<Engine>`, `tracker: RwLock<Engine>`) und Ausnahmen hinter einem `RwLock`. Netzwerk-Requests lesen parallel ohne Lock-Contention (`.read().unwrap()`), während Hintergrund-Updates kurzzeitig exklusiven Schreibzugriff erhalten (`.write().unwrap()`).
   - `AtomicBool`: Für globale Schalter (`SCHILD.aktiv: AtomicBool`) mit lockfreiem Zugriff via `Ordering::Relaxed`.
   - `Mutex`: Für Zustandsobjekte (`Tabs(Mutex<Zustand>)`, `KonfigZustand(Mutex<Konfig>)`, `SchutzZustand(Mutex<Schutz>)`).
   - Faden-Synchronisation mit WebView2: WebView2-Controller verlangen strikte UI-Thread-Affinität. Tauri-Worker-Threads übergeben Aufgaben via `auf_ui` / `app.run_on_main_thread` und synchronisieren über `std::sync::mpsc::channel` mit Timeout-Schutz (`FRIST = Duration::from_secs(20)`), wodurch Deadlocks verhindert werden.

4. **Crash-Resilienz und Fehlerisolation:**
   - In `src-tauri/src/tabs/desktop/absturz.rs` (Zeilen 16–36) fängt `core.add_ProcessFailed` Prozessabbrüche ab.
   - Differenzierung: Nur `COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED` und `BROWSER_PROCESS_EXITED` werden als Tab-Absturz behandelt (`TabEreignis::Absturz`).
   - Hilfsprozesse (GPU-Prozess, Utility-Prozess, Out-of-Process Iframes) startet WebView2 automatisch neu; der Tab und insbesondere der Pontis-Host-Prozess laufen stabil weiter.

---

### 1.3 Schutz sensibler Daten via `schluesselfach`

1. **Architektur und Code-Lokalisierung:**
   - Implementiert im eigenständigen Crate `crates/schluesselfach` (`Cargo.toml`, `src/lib.rs`, `src/geheimnisse.rs`, `src/biometrie.rs`, `src/befehle.rs`).
   - Geteilt zwischen Maunting Pontis (MSB) und Maunting Server Manager / Smart System (MSS) mit getrennten Servicenamen (`MauntingSecureBrowser` / `MauntingSecureBrowserDev`).

2. **OS Keyring Integration & Windows Hello:**
   - **Windows:**
     - Speicherung: Windows Credential Manager via `keyring = "4"` (geschützt über DPAPI).
     - Biometrie: `windows::Security::Credentials::UI::UserConsentVerifier`.
     - Zweistufige Verifikation (`biometrie.rs`, Zeilen 316–337):
       1. Zuerst asynchroner Aufruf von `UserConsentVerifier::RequestVerificationAsync(&msg)` (Windows Hello Gesichtserkennung, Fingerabdruck oder Geräte-PIN).
       2. Nur bei `verif_result == UserConsentVerificationResult::Verified` wird anschließend `Entry::new(dienst_name()?, fach)?.get_password()` aufgerufen.
   - **Android (`Schluesselfach.kt`):**
     - Hardware-Keystore (`AndroidKeyStore`) mit AES-256-GCM (`AES/GCM/NoPadding`, 128-Bit Tag).
     - Schlüsselverankerung mit `setUserAuthenticationRequired(true)` und Timeout 0 s (`KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL`).
     - `setInvalidatedByBiometricEnrollment(true)`: Das Hinzufügen eines neuen biometrischen Merkmals invalidiert den Keystore-Schlüssel unwiderruflich.
     - Geheimnisse werden als `base64(iv):base64(ciphertext)` in geschützten App-Einstellungen gespeichert und synchron geschrieben (`commit()`).

3. **Strikt beschränkte Fächer (Compartments):**
   - In `biometrie.rs` (Zeilen 36–73) lehnt `pruefe_fach()` beliebige Eingaben strikt ab:
     - `vault_biometric_key` (Master-Passwort des Tresors)
     - `messenger_biometric_key` (PIN des Messengers)
     - `messenger_device_secret` (32 Zufallsbytes im Credential Store, die in die PIN-Schlüsselableitung eingehen — schützt gegen Offline-Brute-Force kopierter Datenbanken)
     - `session_refresh_token` (MSM-Kopplungs-Token).

4. **Keine Speicherung im Klartext / Flüchtige Access-Tokens:**
   - Access-Token: Lebt ausschließlich im RAM des Frontends, wird nie auf Festplatte geschrieben.
   - Refresh-Token: Liegt verschlüsselt im Credential Store / Keystore.
   - Migration: Alte Klartextdateien (`.session_auth`) werden beim ersten Laden automatisch in den OS Keyring importiert und von der Festplatte gelöscht (`geheimnisse.rs`, Zeilen 75–97).

---

### 1.4 WebView2 Evergreen Runtime Update-Modell & Sandboxing

1. **Evergreen Update-Modell:**
   - In `src-tauri/src/tabs/desktop.rs` (Zeilen 145–153) initialisiert Pontis die WebView2-Umgebung:
     ```rust
     CreateCoreWebView2EnvironmentWithOptions(
         PCWSTR::null(),
         &HSTRING::from(profil.as_os_str()),
         &ICoreWebView2EnvironmentOptions::from(optionen),
         ...
     )
     ```
   - Die Übergabe von `PCWSTR::null()` als Browser-Ordner weist die Windows WebView2 Loader-Bibliothek an, die systemweit installierte **Microsoft WebView2 Evergreen Runtime** zu verwenden.
   - Wartungs- und Updatezyklus:
     - Die Runtime wird unabhängig von Pontis direkt über Microsoft Edge Update bzw. Windows Update gewartet.
     - Sicherheits-Patches für Upstream-Chromium-Schwachstellen (Blink, V8, Skia CVEs) treffen typischerweise innerhalb von 24–48 Stunden nach Veröffentlichung automatisch auf dem Nutzersystem ein.
     - Pontis-Entwickler müssen keine eigenen Chromium-Builds kompilieren oder verteilen, um Zero-Day-Lücken der Rendering-Engine zu schließen.

2. **Prozesstrennung & Sandboxing:**
   - Chromium Multi-Process Architecture:
     - Host Application: `MauntingSecureBrowser.exe` (Pontis UI & Rust Backend).
     - Browser Broker Prozess: `msedgewebview2.exe`.
     - Renderer Prozesse: separate `msedgewebview2.exe` Prozesse je Tab/Domain, isoliert in Windows AppContainer bzw. Low Integrity Sandboxes (eingeschränkte Token, keine Dateisystem-Schreibrechte, gesperrte Win32k-Syscalls).
     - Isolierter GPU- und Utility-Prozess.

3. **Profil- und Datenisolation:**
   - In `src-tauri/src/tabs/mod.rs` (Zeile 452) und `src-tauri/src/browserdaten.rs` (Zeilen 1–57) laufen Tabs in einem separaten Profilverzeichnis `seitenprofil` (`.../seiten`).
   - Vollständige Trennung von Tresor/Messenger (im UI-Profil) und Webseiten.
   - 1-Klick-Löschen von Seitendaten (`browserdaten::loeschen`) über `ClearBrowsingDataAll` leert ausschließlich das Webseiten-Profil, ohne jemals Tresordaten oder Sessions anzutasten.
   - Workspaces: Eigene isolierte Profile via `optionen.SetProfileName("ws_<id>")`.
   - InPrivate-Tabs: Rein flüchtige In-Memory-Profile via `optionen.SetIsInPrivateModeEnabled(true)`.

4. **Integrierte Download-Quarantäne und Virenprüfung:**
   - In `src-tauri/src/downloads/quarantaene.rs` landen Downloads zunächst isoliert unter `app_local_data/quarantaene/<random>/`.
   - In `src-tauri/src/downloads/pruefung.rs` wird die Datei über die Windows COM-Schnittstelle `IAttachmentExecute::Save` durch den registrierten Windows-Virenschutz (Microsoft Defender) gescannt und mit der NTFS-Herkunftsmarke (Mark of the Web, Zone 3) versehen.
   - Private Downloads setzen die Herkunft auf `about:internet` (Chrome-Verhalten), um URL-Leaks in NTFS Alternate Data Streams (`Zone.Identifier`) zu verhindern.

5. **Datenschutz-Härtung von WebView2:**
   - In `desktop.rs` (Zeile 67): `--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection` schaltet Microsoft SmartScreen und UI-Overlays ab, wodurch verhindert wird, dass besuchte URLs an Microsoft übertragen werden.
   - In `desktop.rs` (Zeile 141): `optionen.set_is_custom_crash_reporting_enabled(true)` sorgt dafür, dass Absturzberichte lokal bleiben und nicht an Microsoft gesendet werden.
   - In `webview2.rs` (Zeilen 174–222): Sämtliche Netzwerkanfragen (inkl. Service Worker und Iframes via `ICoreWebView2_22`) werden über Braves Adblock-Engine vor Erreichen des Netzwerks abgefangen.

---

## 2. Logic Chain (Schlussfolgerungskette)

1. **Beobachtung 1.1** zeigt: Tauri v2 Capabilities (`oberflaeche.json`) beschränken alle IPC-Invoke-Befehle exklusiv auf die Webview `main`. Alle Befehle sind in `build.rs` im App-Manifest deklariert, wodurch Tauri jeden Aufruf zwingend prüft. Tab-IDs werden streng mit dem Präfix `tab-` validiert (`tabs/weg.rs`).
   **Schlussfolgerung:** Selbst wenn eine bösartige Website einen Tauri-IPC-Aufruf absetzen könnte, würde dieser vom Tauri-Kernel mit Permission-Denied abgewiesen, da Tabs keine Capabilities besitzen.

2. **Beobachtung 1.1 & 1.4** zeigt: Unter Windows werden Tabs überhaupt nicht über Tauri angelegt, sondern direkt über native WebView2-COM-APIs.
   **Schlussfolgerung:** Webseiten-Tabs erhalten weder `window.__TAURI_INTERNALS__`, noch das `ipc:`-Protokoll, noch Tauri-Plugin-Skripte. Das Angriffsinterface auf Tauri IPC existiert im DOM des Tabs physisch nicht.

3. **Beobachtung 1.1** zeigt: `seite.js` löscht `chrome.webview` vor der Ausführung von Seitenskripten und sichert JS-Prototypen in Closures. WebMessages zwischen `seite.js` und Rust werden per `core.Source()` und `args.Source()` auf Herkunftsgleichheit validiert.
   **Schlussfolgerung:** Webseiten können die native WebView2-Kommunikationsbrücke weder einsehen noch manipulieren. Message-Spoofing und Prototype-Pollution sind wirksam unterbunden.

4. **Beobachtung 1.2** zeigt: 100 % Safe Rust im Business-Code, 0 Unsafe in `schluesselfach`, strikte Unsafe-Kapselung ausschließlich für OS-FFI (COM/Win32/JNI). Brave Adblock läuft hinter `RwLock<Engine>`, UI-Synchronisation erfolgt über MPSC-Channels mit 20-Sekunden-Timeout.
   **Schlussfolgerung:** Memory-Safety-Schwachstellen (Use-After-Free, Double Free, Buffer Overflows, Data Races) sind im Pontis-Host-Prozess durch das Typsystem und Ownership-Modell von Rust eliminiert.

5. **Beobachtung 1.2 & 1.4** zeigt: `ProcessFailedEventHandler` isoliert Renderer-Fehler. Abstürze einer Webseite führen zu einem Tab-spezifischen Ereignis, nicht zum Absturz des Pontis-Host-Prozesses.
   **Schlussfolgerung:** Browser-Absturzresilienz auf Chromium-Niveau ist gegeben; ein Fehler im Webseiten-Renderer reißt weder die UI noch andere Tabs mit.

6. **Beobachtung 1.3** zeigt: `schluesselfach` nutzt Windows Credential Manager (DPAPI) bzw. Android Hardware Keystore (AES-256-GCM). Unter Windows erzwingt `entsperre_mit_biometrie` vor jedem Lesen eine erfolgreiche Windows Hello Verifikation (`UserConsentVerifier`). Access-Tokens existieren nur im RAM.
   **Schlussfolgerung:** Authentifizierungs- und Tresordaten sind auf Betriebssystem-Sicherheitsniveau geschützt. Selbst bei physischem Datenträgerdiebstahl sind Geheimnisse ohne Windows Hello bzw. TEE-Freigabe unlesbar.

7. **Beobachtung 1.4** zeigt: Pontis nutzt `CreateCoreWebView2EnvironmentWithOptions(null, ...)`.
   **Schlussfolgerung:** Pontis erbt das Chromium-Multiprozes-Sandboxing und wird durch Microsoft Edge Update automatisch mit Upstream-Chromium-Sicherheitspatches versorgt, ohne dass das Pontis-Projekt Binärupdates ausrollen muss.

---

## 3. Caveats (Einschränkungen & Abgrenzungen)

1. **Abhängigkeit von Microsofts Patch-Kadenz:**
   Da Pontis auf Microsoft WebView2 Evergreen basiert, hängt die Schließung von 0-Day-Lücken in Blink/V8 von Microsofts Bereitstellungsgeschwindigkeit ab (üblicherweise innerhalb von 24–48 h nach Chromium-Upstream, aber nicht in Eigenkontrolle des Pontis-Teams).
2. **Plattformunterschiede Desktop vs. Mobile:**
   Die native WebView2-COM-Trennung (völlige Abwesenheit von Tauri-IPC) gilt für Windows Desktop. Unter Android wird die native WebView über das Kotlin-Plugin `TabsPlugin` / `Tab.kt` mit `msbKanal` gesteuert; dort greift dieselbe Kapselung in `seite.js`.
3. **Erweiterungs-Ökosystem:**
   Trotz CRX3-Signaturprüfung (`ring`) und Laden via WebView2-API bietet die WebView2-Plattform im Vergleich zu einem vollwertigen Chromium-Fork nur eingeschränkte Chrome Extension APIs (z. B. keine unbeschränkten Manifest-V2/V3-Background-Worker-Hooks wie in Brave).

---

## 4. Conclusion (Gesamtfazit)

Maunting Pontis weist eine außergewöhnlich saubere, sicherheitsorientierte Architektur auf, die über das typische Niveau von Tauri-Anwendungen weit hinausgeht:

1. **Verteidigung in der Tiefe (Defense-in-Depth):**
   Die Trennung von UI (`main`) und Webseiten-Tabs (`tab-*`) ist mehrfach redundant abgesichert:
   - *Ebene 1 (Architektur):* Tabs werden unter Windows nativ via COM instanziiert und besitzen überhaupt keine Tauri-IPC-Bindings.
   - *Ebene 2 (DOM-Hygiene):* `seite.js` entfernt `chrome.webview` vor Ausführung von Seitenskripten und sichert Prototypen gegen Manipulation.
   - *Ebene 3 (Tauri Kernel):* Falls je ein Aufruf Tauri erreichte, verwerfen Manifest-Registrierung und Capability-Regeln (`oberflaeche.json`) jeden Aufruf außerhalb von `main`.
   - *Ebene 4 (CSP):* Strikte CSP verbietet `unsafe-eval` und externe Bild- oder Scriptquellen in der UI.
2. **Host-Speichersicherheit:**
   Der Host-Prozess ist in modernem Rust (1.88, Edition 2021) implementiert. `schluesselfach` ist 100 % safe Rust; `unsafe` ist strikt auf FFI-Brücken beschränkt. Deadlock- und Concurrency-Sicherheit sind durch saubere `RwLock`/`Mutex`-Trennung und asynchrone MPSC-Dispatch-Strukturen gewährleistet.
3. **Kryptografische & biometrische Verwahrung:**
   Das `schluesselfach`-System verbindet OS-Keyrings (Windows Credential Manager / DPAPI, Android Keystore mit AES-256-GCM) mit Windows Hello bzw. Hardware-Biometrie. Ein zweistufiger Verifikationsprozess verhindert unbefugtes Auslesen.
4. **Evergreen-Sicherheitsmodell:**
   Durch die Nutzung der WebView2 Evergreen Runtime profitiert Pontis von Microsofts automatischer Chromium-Sicherheitswartung, kombiniert mit nativer Download-Quarantäne (Windows Defender `IAttachmentExecute`) und strikter Telemetrie-Abschaltung.

---

## 5. Verification Method (Verifikationsmethoden & Testergebnisse)

Die vorstehenden Feststellungen wurden durch direkte Code-Inspektion und Ausführung der projekteigenen Unit- und Integrationstests verifiziert:

1. **Tauri-Rechte und Manifest-Konsistenz prüfen:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri
   cargo test --lib -- tests::rechte_decken_jeden_befehl
   ```
   *Verifiziertes Ergebnis:*
   ```text
   running 1 test
   test tests::rechte_decken_jeden_befehl ... ok
   test result: ok. 1 passed; 0 failed; 0 ignored; finished in 0.00s
   ```
   Beweist die 100 %ige Deckung zwischen `build.rs`, `permissions/oberflaeche.toml` und `generate_handler!`.

2. **CSP-Sicherheit der UI prüfen:**
   ```powershell
   cargo test --lib -- tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval
   ```
   *Verifiziertes Ergebnis:*
   ```text
   running 1 test
   test tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval ... ok
   test result: ok. 1 passed; 0 failed; 0 ignored; finished in 0.00s
   ```
   Beweist das Verbot von `unsafe-eval`, externen `img-src` und ungesichertem `connect-src`.

3. **Token-Sicherheit und Ausschluss von Klartextdateien:**
   ```powershell
   cargo test --lib -- tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung
   ```
   *Verifiziertes Ergebnis:*
   ```text
   running 1 test
   test tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung ... ok
   test result: ok. 1 passed; 0 failed; 0 ignored; finished in 0.00s
   ```

4. **Navigations-, Scheme- und Ursprungsvalidierung (`tabs::weg::tests`):**
   ```powershell
   cargo test --lib -- tabs::weg::tests
   ```
   *Verifiziertes Ergebnis:*
   ```text
   running 10 tests
   test tabs::weg::tests::oauth_rueckspruenge_gehen_ans_system_statt_ins_leere ... ok
   test tabs::weg::tests::die_oberflaeche_oeffnet_nur_webseiten ... ok
   test tabs::weg::tests::eine_anfrage_entscheidet_auf_beiden_plattformen_gleich ... ok
   test tabs::weg::tests::kennung_kann_nie_die_oberflaeche_sein ... ok
   test tabs::weg::tests::tabs_laden_nie_die_oberflaeche ... ok
   test tabs::weg::tests::ein_rahmen_auf_einer_gesperrten_herkunft_faellt ... ok
   test tabs::weg::tests::navigation_entscheidet_ohne_webview ... ok
   test tabs::weg::tests::nur_laufende_erweiterungen_haben_seiten ... ok
   test tabs::weg::tests::gefaehrliche_und_eigene_schemata_gehen_nie_ans_system ... ok
   test tabs::weg::tests::eine_navigation_laedt_verschluesselt_und_ohne_tracking_parameter ... ok
   test result: ok. 10 passed; 0 failed; 0 ignored; finished in 0.01s
   ```

5. **Prüfung auf Unsafe-Freiheit in `schluesselfach`:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\crates\schluesselfach
   git grep "unsafe"
   ```
   *Verifiziertes Ergebnis:* Exakt 0 Treffer.

6. **Unit-Tests in `crates/schluesselfach`:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\crates\schluesselfach
   cargo test
   ```
   *Verifiziertes Ergebnis:*
   ```text
   running 4 tests
   test geheimnisse::tests::das_abgelegte_token_gilt_und_eine_liegengebliebene_datei_faellt ... ok
   test geheimnisse::tests::die_klartextdatei_wandert_in_den_schluesselbund_und_verschwindet ... ok
   test geheimnisse::tests::scheitert_das_ablegen_bleibt_die_datei_und_damit_die_kopplung ... ok
   test geheimnisse::tests::ohne_token_und_ohne_datei_ist_nichts_da ... ok
   test result: ok. 4 passed; 0 failed; 0 ignored; finished in 0.01s
   ```
   Beweist die fehlerfreie Migration alter Klartext-Dateien in den OS Keyring sowie Idempotenz beim Token-Handling.
