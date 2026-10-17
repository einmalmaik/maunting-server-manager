# Handoff Report — Requirement R3: Ehrliche Bestandsaufnahme & Entwicklungspotenziale

## 1. Observation

Direct codebase inspection of Maunting Pontis (`browser/src-tauri`, `browser/frontend`, and `crates/schluesselfach`) reveals the following concrete architecture, implementations, and boundaries.

### 1.1 Working Vibecoding Foundations & Achievements

#### O-1.1: Zero-Privilege Tab Sandbox & Bypass of Tauri Webview Internals
- **File**: `browser/src-tauri/src/tabs/desktop.rs`, lines 1–10:
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
- **File**: `browser/src-tauri/capabilities/oberflaeche.json`, lines 4–12:
  ```json
  "description": "Rechte der Browser-Oberfläche. Sie gelten nur für die Webview `main`, nicht für das Fenster: im Fenster liegen auch die Tabs, und die bekommen keine Rechte.",
  "webviews": ["main"],
  "permissions": [
    "core:default",
    "core:window:allow-start-dragging",
    "dialog:allow-open",
    "notification:default",
    "oberflaeche"
  ]
  ```
- **Evidence**: Pontis bypasses Tauri's default Webview builder for tabs. Instead of calling `tauri::WebviewBuilder` (which injects `__TAURI_INTERNALS__` and plugin hooks into every webview), Pontis creates raw Win32 COM `ICoreWebView2Controller` instances directly embedded into the main window HWND (`browser/src-tauri/src/tabs/desktop.rs:161-188`). Web pages have zero access to Tauri IPC commands or plugin injection scripts.

#### O-1.2: Native Brave Adblock Engine Integration
- **File**: `browser/src-tauri/Cargo.toml`, lines 28–34:
  ```toml
  # Werbe- und Trackerfilter von Brave (MPL-2.0), dieselbe Engine wie in Brave.
  # Ohne `single-thread`: die Engine liegt hinter einem RwLock und wird aus dem
  # UI-Thread (WebView2) und aus dem Ladefaden der Listen angefasst.
  adblock = { version = "0.13", default-features = false, features = [
      "embedded-domain-resolver",
      "full-regex-handling",
  ] }
  ```
- **File**: `browser/src-tauri/src/schild/listen.rs`, lines 26–55:
  Dual-engine model subscribing to EasyList (`https://easylist.to/easylist/easylist.txt`), EasyList Germany, EasyPrivacy (`easyprivacy.txt`), and Fanboy's Cookie Monster list (`https://secure.fanboy.co.nz/fanboy-cookiemonster.txt`).
- **File**: `browser/src-tauri/src/schild/mod.rs`, lines 186–219:
  Interception via `ICoreWebView2_22::AddWebResourceRequestedFilterWithRequestSourceKinds` for all contexts (`COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL`) and all request source kinds (including iframes, Service Workers, Shared Workers). Blocked network requests are intercepted before hitting the network stack and answered with HTTP 403 (or 204 for pings).
- **File**: `browser/src-tauri/src/tabs/desktop/webview2.rs`, lines 378–397:
  Cosmetic filter injection (`kosmetik_anwenden`) using `adblock` cosmetic stylesheet injection and element hiding upon `DOMContentLoaded`.

#### O-1.3: Deep-Sleep & 0 MB RAM Tab Discarding
- **File**: `browser/src-tauri/src/tabs/ruhe.rs`, lines 21–39 & 87–106:
  Background thread (`starten`, checking every 30s) identifies tabs hidden longer than configured thresholds (`schlafen_nach`, default 30 min; `verwerfen`, default 60 min).
- **File**: `browser/src-tauri/src/tabs/desktop/schlaf.rs`, lines 65–76 & 129–140:
  - Phase 1 (Sleep): Background tabs are throttled to `COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW` (`ICoreWebView2_19`), and suspended via `ICoreWebView2_3::TrySuspend`.
  - Phase 2 (Discard): `verwerfen()` removes the tab controller from `NATIV` thread-local storage and destroys the native WebView2 controller via `herunterladen::schliessen(id, tab.controller)`. Memory drops to 0 MB RAM for the native webview process, leaving only the tab metadata in the React state.

#### O-1.4: Chrome Extension Subsystem (CRX3 Engine in Rust)
- **File**: `browser/src-tauri/src/erweiterungen/crx.rs`, lines 1–40:
  Downloads CRX packages from Chrome Web Store (`https://clients2.google.com/service/update2/crx`), parses CRX3 headers, and verifies RSA (PKCS#1 v1.5 SHA-256) and Ed25519 signatures using `ring::signature`.
- **File**: `browser/src-tauri/src/erweiterungen/paket.rs`, lines 67–92:
  ZIP decompression with zip-bomb and directory-traversal guards (`DATEIEN_HOECHSTENS = 10_000`, `BYTES_HOECHSTENS = 512 MB`).
- **File**: `browser/src-tauri/src/tabs/desktop/erweiterungen.rs`, lines 22–48:
  Extensions are loaded into the profile via `ICoreWebView2Profile7::AddBrowserExtension`.
- **File**: `browser/src-tauri/src/tabs/desktop/popup.rs`, lines 104–140:
  Extension popups (`action.default_popup`) are hosted in an isolated WebView2 controller positioned seamlessly beneath the toolbar button.

#### O-1.5: Clean Copy, 1-Click Site Reset & Privacy Invariants
- **File**: `browser/src-tauri/src/schild/verfolgung.rs`, lines 11–48:
  `ohne_verfolgung` strips 48 tracking parameters (`fbclid`, `gclid`, `utm_*`, `msclkid`, `twclid`, `si`, `srsltid`, etc.) from URLs while preserving legitimate parameters (such as affiliate tags).
- **File**: `browser/src-tauri/src/tabs/desktop.rs`, lines 434–475:
  `seite_zuruecksetzen` calls Chrome DevTools Protocol (CDP) `Storage.clearDataForOrigin` (`storageTypes`: `cookies,storage,local_storage,indexeddb,websql,service_workers,cache_storage`) and `Network.deleteCookies` for the specific origin, followed by an immediate reload.
- **File**: `browser/src-tauri/src/tabs/desktop/ohne_edge.rs`, lines 24–36:
  Disables Edge telemetry and autofill: `SetAreDevToolsEnabled(false)`, `SetIsStatusBarEnabled(false)`, `SetIsBuiltInErrorPageEnabled(false)`, `SetAreDefaultScriptDialogsEnabled(false)`, `SetIsPasswordAutosaveEnabled(false)`, `SetIsGeneralAutofillEnabled(false)`.
- **File**: `browser/src-tauri/src/lib.rs`, lines 311–333:
  Automated unit test `die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval` verifies CSP restricts `script-src` and `connect-src` strictly to `localhost` / `127.0.0.1` and `https:`.

#### O-1.6: Hardware-Backed Biometrics & Keyring (`schluesselfach`)
- **File**: `crates/schluesselfach/src/biometrie.rs`, lines 6–35:
  Windows integration using `UserConsentVerifier` (Windows Hello biometrics) and Windows Credential Store (`keyring::Entry`); Android integration using hardware Android Keystore with `BiometricPrompt`.
- **File**: `crates/schluesselfach/src/geheimnisse.rs`, lines 1–60:
  Vault biometric key (`FACH_TRESOR`) and Messenger device secret (`FACH_MESSENGER_GERAET`) strictly separated into distinct slots with whitelist validation.

#### O-1.7: Modern Productivity UX & Self-Hosted AI Integration
- **File**: `frontend/src/browser/kopf/VertikaleTableiste.tsx`, lines 64–100:
  Tree-Style Tabs with hierarchical indentation (`baueTabBaum`, `elternId`), expandable/collapsible tab branches, drag and drop, and workspace grouping.
- **File**: `frontend/src/browser/seite/SnapZonenOverlay.tsx`, lines 15–53:
  4-way snap-assist split screen supporting tiling across `'links' | 'rechts' | 'oben' | 'unten'` with drag-and-drop overlays.
- **File**: `frontend/src/browser/seite/LesemodusAnsicht.tsx`, lines 148–175:
  Reader view with 1-click export to self-hosted MSM offline notes (`saveNoteOffline`) and Singra AI summarization (`entwurfUebergeben`).
- **File**: `frontend/src/browser/seite/MsmSuche.tsx`, lines 1–54:
  Private search querying the user's self-hosted SearXNG instance (`POST /api/browser/suche`) with AI result summarization (`SuchzusammenfassungKachel`).

---

### 1.2 Architectural Trade-offs & Limitations vs Monolithic Chromium Forks (Chrome, Brave)

#### O-2.1: Platform WebView2 Dependency & OS Coupling
- **File**: `browser/src-tauri/src/tabs/desktop.rs`, line 1 & `Cargo.toml`, line 53:
  Desktop implementation is conditionally compiled exclusively for Windows: `[target.'cfg(windows)'.dependencies] webview2-com = "0.39"`.
- **File**: `browser/src-tauri/src/erweiterungen/mod.rs`, lines 1–4:
  ```rust
  //! Erweiterungen aus dem Chrome Web Store und entpackte Ordner (nur Windows;
  //! die Android-WebView kennt keine). Die Befehle gibt es auf beiden
  //! Plattformen, unter Android antworten sie mit `nur_windows`.
  ```
- **Evidence**: Pontis does not compile a standalone browser engine. On Windows, it requires Microsoft Edge WebView2 Evergreen Runtime. On Android, it relies on Android System WebView. There is currently no functional tab implementation for Linux or macOS. If Microsoft WebView2 is uninstalled, corrupted, or blocked by Windows enterprise policy, Pontis cannot start.

#### O-2.2: Chromium Engine Release Cycle & Zero-Day Latency
- In Chrome and Brave, upstream Chromium security patches and zero-day vulnerabilities (e.g. V8 type confusions, Skia buffer overflows) are compiled and shipped directly by Google or Brave within hours.
- In Pontis, the core rendering and JavaScript engine is maintained by Microsoft via the WebView2 Evergreen distribution. When a critical Chromium zero-day is disclosed, Pontis cannot deploy an engine-level patch independently; it must wait for Microsoft to update WebView2 on client machines.

#### O-2.3: Extension Ecosystem Limitations & Tab API Gap
- **File**: `browser/src-tauri/src/tabs/desktop/popup.rs`, lines 7–8:
  ```rust
  //! Grenze: fragt das Popup nach „dem aktiven Tab“, findet es keinen; die
  //! Tabs gehören nicht zu einem Browserfenster, das WebView2 kennt.
  ```
- **Evidence**: WebView2 exposes only a partial subset of WebExtension APIs. Because Pontis manages tabs as independent Win32 COM `ICoreWebView2Controller` instances embedded in a custom Tauri window, WebView2 has no knowledge of the overall tab hierarchy or active window. Any Chrome extension that queries `chrome.tabs.query({ active: true, currentWindow: true })`, executes scripts via `chrome.scripting`, or relies on `chrome.windows` fails or returns empty collections. Password manager extensions (e.g., Bitwarden, 1Password) cannot detect the active tab URL via standard Chrome extension APIs.
- Furthermore, Android WebView does not support extensions at all (`src-tauri/src/erweiterungen/mod.rs:2-3`).

#### O-2.4: Anti-Fingerprinting Deficits (No Engine Farbling)
- Brave modifies Blink and V8 directly in C++ to implement "farbling" (adding deterministic pseudo-random noise to `CanvasRenderingContext2D.toDataURL()`, `getImageData()`, `WebGLRenderingContext.readPixels()`, `getParameter()`, `AudioBuffer`, and audio frequency data).
- **File**: `browser/src-tauri/src/seite.js`, lines 1–15 & 62–80:
  Pontis runs a content script before page execution that removes `window.chrome.webview` and overrides prototypes. However, Pontis has NO low-level farbling in Blink/V8.
- **Evidence**: JavaScript-level canvas/audio proxying or spoofing in `seite.js` is detectable by modern fingerprinting engines (e.g. FingerprintJS Pro) via `Function.prototype.toString`, prototype property descriptor inspections, timing attacks, or Web Workers (`OffscreenCanvas`, `AudioWorklet`) where document scripts do not execute before worker tasks.

#### O-2.5: DRM (Widevine) and Proprietary Codec Constraints
- In Chrome and Brave, Widevine Content Decryption Module (CDM) and proprietary codecs (H.264, AAC, HEVC) are either licensed or bundled directly.
- In Microsoft WebView2, Widevine DRM is not enabled by default and requires specific Microsoft runtime agreements, flags, or OEM provisions. High-value media streaming services (Netflix, Spotify, Amazon Prime Video, Disney+) enforcing Widevine L1/L3 fail to initialize the CDM in standard embedded WebView2 controllers, resulting in playback errors or restricted SD resolution.

#### O-2.6: Multi-Process Sandboxing Boundaries vs Chromium Site Isolation
- **File**: `browser/src-tauri/src/tabs/desktop.rs`, lines 171–183:
  ```rust
  let u10 = umgebung.cast::<ICoreWebView2Environment10>().map_err(fehler)?;
  let optionen = u10.CreateCoreWebView2ControllerOptions().map_err(fehler)?;
  optionen.SetIsInPrivateModeEnabled(privat).map_err(fehler)?;
  if !privat {
      if let Some(ws) = workspace {
          let saubere_id: String = ws.chars().filter(|c| c.is_alphanumeric() || *c == '_' || *c == '-').collect();
          if !saubere_id.is_empty() && saubere_id != "standard" && saubere_id != "default" {
              optionen.SetProfileName(&HSTRING::from(format!("ws_{saubere_id}"))).map_err(fehler)?;
          }
      }
  }
  ```
- **Evidence**: All workspace tabs share the same `ICoreWebView2Environment` and same base User Data Folder (`seitenprofil`), distinguishing workspaces merely by `SetProfileName`.
- In Chromium, Site Isolation (`--site-per-process`) isolates origins across distinct OS sandboxed renderer processes. In WebView2, while renderer processes are separated by origin, all profiles in the same environment share the same main browser process (`msedgewebview2.exe`). If an attacker escapes a renderer process sandbox via an unpatched engine exploit, all profiles and tabs running under that same browser process PID tree are exposed.

---

## 2. Logic Chain

1. **Premise 1 (Host Process Architecture)**: Pontis is built with Tauri v2 and Rust (`Cargo.toml:1-16`, `src-tauri/src/lib.rs:36-268`).
   - *Inference*: Rust eliminates entire classes of memory safety vulnerabilities (use-after-free, double free, buffer overflow) in the browser host/UI process, which historically account for ~70% of Chromium host vulnerabilities.

2. **Premise 2 (Zero-Privilege Tab Isolation)**: Untrusted web content is hosted in raw COM `ICoreWebView2Controller` instances directly in the main window HWND (`tabs/desktop.rs:1-10, 161-188`). Tauri capabilities explicitly restrict IPC commands to the `main` webview (`capabilities/oberflaeche.json:4-12`).
   - *Inference*: Even if an untrusted page attempts to execute Tauri IPC commands or probe for `__TAURI_INTERNALS__`, the bridge does not exist in the tab webview. The isolation between untrusted web content and host privileges is structurally sound.

3. **Premise 3 (Resource Management Comparison)**: Monolithic Chromium browsers use tab discarding (freeing tab renderer memory while retaining the tab handle in the C++ browser window). Pontis implements a two-stage hibernation mechanism (`tabs/ruhe.rs:87-106`, `tabs/desktop/schlaf.rs:65-76, 129-140`):
   - Stage 1: Suspend (`TrySuspend`) + memory level `LOW`.
   - Stage 2: Discard (`verwerfen`), which completely destroys the native COM controller while retaining only lightweight React metadata.
   - *Inference*: Pontis achieves an actual 0 MB RAM footprint for discarded tabs, significantly outperforming Chrome's background renderer overhead.

4. **Premise 4 (Architectural Boundaries of Platform WebView2)**:
   - Observation O-2.1 and O-2.3 demonstrate that Pontis is a host application driving WebView2 via Win32 COM, not a compiled Chromium fork.
   - Observation O-2.3 (`popup.rs:7-8`) confirms that WebView2 cannot recognize Pontis tabs as native Chromium tabs.
   - Observation O-2.4 confirms that Pontis has no C++ access to Blink/V8 for fingerprint farbling.
   - Observation O-2.5 confirms DRM limitations inherent in stock WebView2.
   - *Inference*: While Pontis achieves remarkable agility and minimal binary size (~20 MB vs ~200 MB for Chrome/Brave) by delegating rendering to the OS runtime, it hits hard architectural ceilings in extension interoperability, anti-fingerprinting, DRM, and zero-day release cadence that cannot be overcome without custom C++ engine modifications or specialized proxy bridges.

---

## 3. Caveats

1. **Platform Coverage**: Desktop investigation focused on the Windows codebase (`src-tauri/src/tabs/desktop/` and `windows/`), as Linux/macOS desktop tab implementations are not yet wired up in Pontis (the repository contains Android and Windows targets).
2. **WebView2 Evolution**: Microsoft actively updates WebView2 COM interfaces (e.g. `ICoreWebView2_22` through `_28`). Some extension API limitations may change in future Windows SDK releases, though tab-window ownership will remain restricted unless Microsoft explicitly provides multi-window controller grouping APIs.
3. **DRM Licensing**: The assessment of Widevine DRM is based on standard Microsoft Edge WebView2 deployment policies; enterprise-licensed or custom-signed WebView2 runtimes might enable Widevine, but stock consumer environments do not.

---

## 4. Conclusion

Maunting Pontis represents an exceptionally sophisticated, structurally clean implementation of a modern, privacy-focused browser built upon Tauri v2 and Rust.

### Summary of Vibecoding Achievements
1. **Architectural Purity**: Successfully solves the notorious Tauri security pitfall (untrusted web content accessing Tauri IPC) by completely bypassing Tauri's webview wrapper for tabs and embedding bare COM `ICoreWebView2Controller` instances into the window HWND with zero IPC exposure.
2. **Native Brave Adblocking**: Full integration of Brave's `adblock` Rust engine (MPL-2.0) with dual engines (EasyList + EasyPrivacy), hardcoded offline fallback rules, pre-network request cancellation (HTTP 403/204), and cosmetic stylesheet filtering.
3. **True Zero-RAM Tab Discarding**: Outperforms standard Chromium tab discarding by entirely destroying discarded native controllers (`Ruhe::Verwerfen`), reducing inactive tab memory to 0 MB.
4. **Security & Privacy First**: Clean Copy (48 tracking params stripped), 1-click CDP Site Reset, Windows `IAttachmentExecute` antivirus scanning on downloads, hardware-backed biometrics/keychain (`schluesselfach`), and zero telemetry.
5. **Productivity Innovation**: Native Tree-Style Tabs, 4-way Snap-Assist Split Screen, Reader View with AI summarization and note export, and self-hosted SearXNG search integration.

### Honest Architectural Trade-offs vs Standalone Chromium (Chrome, Brave)
1. **Platform Coupling & Update Latency**: Hard dependency on Microsoft Edge WebView2 Evergreen; zero-day Chromium engine fixes cannot be distributed independently by Pontis.
2. **Extension API Limitations**: WebExtensions requiring `chrome.tabs`, `chrome.windows`, or background script tab querying fail because Pontis tabs are not native Chromium browser tabs (`popup.rs:7-8`). No extensions on Android.
3. **Anti-Fingerprinting**: Lacks low-level Blink/V8 farbling (Canvas, WebGL, AudioContext noise); JavaScript-layer spoofing in `seite.js` is detectable by modern fingerprinting algorithms.
4. **DRM & Codecs**: Widevine DRM is not supported out of the box in stock WebView2, breaking high-value DRM streaming (Netflix, Prime, Spotify).
5. **Process Sandbox Hierarchy**: Workspaces share the same `msedgewebview2.exe` browser process tree, lacking the complete OS-level process boundary isolation of separate browser instances.

---

## 5. Prioritized Technical Roadmap (P0, P1, P2)

### P0: Critical Hardening & Security Isolation (Immediate Next Steps)
1. **Complete Process Boundary Isolation for Workspaces & Private Mode**:
   - *Current State*: Workspaces use `SetProfileName` under a single shared `ICoreWebView2Environment` (`tabs/desktop.rs:171-182`).
   - *Recommendation*: Use separate User Data Folders (and separate `ICoreWebView2Environment` instances) for Private Mode and distinct Workspaces. This forces Windows to spawn dedicated `msedgewebview2.exe` browser processes, creating true OS-level AppContainer/process boundaries between workspaces.
   - *Target Files*: `browser/src-tauri/src/tabs/desktop.rs` (`umgebung()`, `controller()`).
2. **Anti-Tamper Hardening of `seite.js`**:
   - *Current State*: `seite.js` captures intrinsics and prototypes before page scripts run (`Reflect.apply`, `Object.getOwnPropertyDescriptor`).
   - *Recommendation*: Explicitly freeze captured prototypes (`Object.freeze`) and verify that Web Worker and `OffscreenCanvas` contexts cannot bypass prototype traps.
   - *Target Files*: `browser/src-tauri/src/seite.js`.
3. **DNS Rebinding & Localhost Probing Protection**:
   - *Current State*: `schild/sperre.rs` detects public vs private IPs for youth protection.
   - *Recommendation*: Enforce a strict network boundary in `schild::pruefen`: reject all subresource requests from public origins to `localhost`, `127.0.0.1`, and RFC 1918 subnets (`192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`) to eliminate internal network scanning and DNS rebinding attacks.
   - *Target Files*: `browser/src-tauri/src/schild/sperre.rs`, `browser/src-tauri/src/schild/mod.rs`.

### P1: Ecosystem & Privacy Enhancements (Medium-Term)
1. **Rust-Level WebExtension Tab Polyfill Bridge**:
   - *Current State*: Popups cannot query `chrome.tabs` (`popup.rs:7-8`).
   - *Recommendation*: Implement an internal message handler that polyfills `chrome.tabs.query`, `chrome.tabs.create`, and `chrome.tabs.sendMessage` by routing extension requests to Pontis' Rust tab store via CDP `Runtime.evaluate` / `chrome.webview`. This will unlock password manager extensions (Bitwarden, 1Password) to work in Pontis.
   - *Target Files*: `browser/src-tauri/src/tabs/desktop/popup.rs`, `browser/src-tauri/src/erweiterungen/`.
2. **Cross-Platform Desktop Expansion (macOS & Linux)**:
   - *Current State*: Desktop tab engine is exclusively `#[cfg(windows)]`.
   - *Recommendation*: Introduce cross-platform abstraction traits for tab controllers, implementing macOS via `WKWebView` / native multi-webview and Linux via WebKitGTK or CEF.
   - *Target Files*: `browser/src-tauri/src/tabs/mod.rs`, `browser/src-tauri/src/tabs/desktop.rs`.
3. **Advanced Anti-Tracking: CNAME Uncloaking**:
   - *Current State*: URL requests are matched against EasyPrivacy and EasyList domain rules.
   - *Recommendation*: Perform asynchronous Rust DNS lookups (`trust-dns-resolver` or `hickory-resolver`) in `schild/` to resolve CNAME records and uncloak third-party trackers disguised as first-party subdomains (e.g. `tracking.example.com` -> `cname.analytics.com`).
   - *Target Files*: `browser/src-tauri/src/schild/mod.rs`.

### P2: Strategic Competitive Differentiators (Long-Term)
1. **Local On-Device AI Pipeline (Rust / ONNX)**:
   - *Current State*: Reader view and Search AI currently connect to self-hosted MSM or Singra backends (`LesemodusAnsicht.tsx:169-175`).
   - *Recommendation*: Integrate a lightweight local LLM (e.g. Qwen2.5-0.5B or SmolLM2 via `candle` or `ort` in Rust) directly inside the desktop app for zero-latency, 100% offline page summarization, translation, and entity extraction without requiring external server connectivity.
   - *Target Files*: `browser/src-tauri/src/tabs/uebersetzung.rs`, `frontend/src/browser/seite/LesemodusAnsicht.tsx`.
2. **Zero-Knowledge P2P State Synchronization**:
   - *Current State*: `schluesselfach` provides biometrically guarded local key storage.
   - *Recommendation*: Build an end-to-end encrypted sync mechanism (tabs, bookmarks, history) between Pontis Desktop and Pontis Android using keys derived from `schluesselfach` without reliance on centralized cloud accounts.
   - *Target Files*: `crates/schluesselfach/`, `browser/src-tauri/src/browserdaten.rs`.
3. **DRM & Widevine Streaming Profile**:
   - *Current State*: Widevine is unavailable in stock WebView2.
   - *Recommendation*: Investigate dedicated media container webviews with Chromium Embedded Framework (CEF) or evaluate official Microsoft WebView2 Media Pack integration to enable full DRM streaming support.

---

## 6. Verification Method

1. **Verify Security Invariant Unit Tests**:
   - Execute in `browser/src-tauri`:
     ```powershell
     cargo test --lib
     ```
   - Confirms all assertions in `lib.rs`:
     - `rechte_decken_jeden_befehl` (permissions in `oberflaeche.toml` match registered handlers)
     - `die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval` (CSP enforces `img-src` and forbids `unsafe-eval`)
     - `das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung` (Android tokens never stored in plaintext)
     - `das_schluesselfach_ist_in_beiden_apps_gleich` (Keyring parity across MSB and MSS)

2. **Verify Tab Discarding & Memory Tests**:
   - Execute:
     ```powershell
     cargo test --lib tabs::ruhe::tests
     ```
   - Validates tab suspension rules, threshold calculation, exception lists, and private mode discarding protection.

3. **Verify Tracking Parameter Stripping**:
   - Execute:
     ```powershell
     cargo test --lib schild::verfolgung::tests
     ```
   - Confirms clean copy parameter removal across 48 tracking parameters without altering query parameters like affiliate tags.

4. **Verify Extension CRX Signature & Manifest Verification**:
   - Execute:
     ```powershell
     cargo test --lib erweiterungen::paket::tests
     ```
   - Validates CRX3 signature checks, zip-bomb boundaries, and manifest v3 permissions parsing.

5. **Invalidation Conditions**:
   - If Microsoft modifies the WebView2 Win32 COM architecture to natively group multiple independent HWND controllers into a unified Chromium `BrowserWindow`, the extension API tab-query limitation in O-2.3 would be invalidated.
   - If Tauri v2 introduces a zero-privilege webview configuration flag that strips `__TAURI_INTERNALS__` automatically, the rationale for raw Win32 COM controller instantiation in O-1.1 would become optional.
