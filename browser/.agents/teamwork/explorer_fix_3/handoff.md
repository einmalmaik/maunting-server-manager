# Handoff Report: Explorer Fix 3 (Comprehensive Path, Command & Citation Inconsistency Audit)

**Agent:** Explorer Fix 3 (`teamwork_preview_explorer`)  
**Target Document:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\AUDIT_REPORT_PONTIS.md`  
**Working Directory:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\.agents\teamwork\explorer_fix_3`  
**Date:** 2026-10-17  

---

## 1. Observation

A full-text scan and cross-verification of all 587 lines of `AUDIT_REPORT_PONTIS.md` was conducted against the physical repository tree at `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\`.

### 1.1 Repository Layout Baseline
The physical directory structure of the repository is:
```
<repo-root>/ (maunting-server-manager/browser_competitiveness_security_audit)
├── browser/
│   ├── package.json
│   ├── AUDIT_REPORT_PONTIS.md
│   └── src-tauri/
│       ├── Cargo.toml
│       ├── build.rs
│       ├── capabilities/oberflaeche.json
│       ├── permissions/oberflaeche.toml
│       ├── gen/android/.../Schluesselfach.kt
│       └── src/
│           ├── lib.rs
│           ├── seite.js
│           ├── schild/
│           ├── tabs/
│           ├── erweiterungen/
│           └── downloads/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── locales/de.json
│       └── browser/
│           ├── kopf/
│           ├── seite/
│           └── services/
└── crates/
    └── schluesselfach/
        ├── Cargo.toml
        └── src/
            ├── lib.rs
            ├── biometrie.rs
            └── geheimnisse.rs
```

### 1.2 Path and Command Defect in Section 6.2 (Lines 546–560)
- **Report Text (Line 546):** `Ausführen im Arbeitsverzeichnis browser/frontend:`
  - **Empirical Reality:** Directory `browser/frontend` does **not** exist.
  - **Test Command Output:**
    ```powershell
    Test-Path browser/frontend  # False
    Test-Path ../frontend       # True (from browser/)
    Test-Path frontend          # True (from repo root)
    ```
- **Report Text (Lines 550, 553, 556, 559):** `npm run test:run <test-file>`
  - **Empirical Reality:** In `frontend/package.json`, lines 13–18:
    ```json
    "scripts": {
      "test": "vitest run",
      "test:watch": "vitest",
      "dev:browser": "vite --config vite.browser.config.ts",
      "build:browser": "tsc -b && vite build --config vite.browser.config.ts"
    }
    ```
    There is no script named `"test:run"`.
  - **Verbatim Error:**
    ```
    npm error Missing script: "test:run"
    npm error To see a list of scripts, run:
    npm error   npm run
    ```
  - **Working Syntax:** `npm test -- <path>` or `npx vitest run <path>`. All 4 tests pass 100% (52 passed).

### 1.3 Navigation Context Ambiguity in Section 6.3 (Lines 563–566)
- **Report Text (Lines 564–566):**
  ```powershell
  cd crates/schluesselfach
  git grep "unsafe"
  ```
  - **Empirical Reality:** If the reader executes Section 6.1 (in `browser/src-tauri`) or Section 6.2 (in `frontend`), running `cd crates/schluesselfach` fails with `The system cannot find the path specified`. The command is only valid when executed from the repository root, or with relative navigation (`cd ../crates/schluesselfach` from `frontend`, or `cd ../../crates/schluesselfach` from `browser/src-tauri`).

### 1.4 Line Citation Precision in Section 6.3 (Line 573)
- **Report Text (Line 573):**
  `` `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 135–139: ``
  `` `NATIV.with(|n| n.borrow_mut().remove(id))` in Kombination mit `super::herunterladen::schliessen(id, tab.controller);`. ``
  - **Empirical Reality:** In `browser/src-tauri/src/tabs/desktop/schlaf.rs`, lines 129–139:
    ```rust
    129: pub(super) fn verwerfen(app: &AppHandle, id: &str) {
    130:     app.state::<Tabs>().0.lock().unwrap().tabs.remove(id);
    131:     crate::schild::tab_vergessen(id);
    132:     super::rueckfragen::vergessen(id);
    133:     if let Some(tab) = NATIV.with(|n| n.borrow_mut().remove(id)) {
    134:         unsafe {
    135:             super::herunterladen::schliessen(id, tab.controller);
    136:         }
    137:     }
    138:     melden(app, TabEreignis::Verworfen { id: id.to_string() });
    139: }
    ```
    `NATIV.with(...)` starts on **line 133**, not 135. The relevant code block spans **lines 133–137** (or the complete function spans **lines 129–139**).

### 1.5 Inconsistent Path Prefixes in Section 3.1 Comparison Table (Lines 248–263)
The "Fundstelle im Pontis-Code" column mixes three distinct path formats:
1. **Full repo-relative path:**
   - Line 257: `frontend/src/locales/de.json:4924`
2. **Shortened frontend paths missing `frontend/src/browser/`:**
   - Line 255: `services/workspacesStore.ts:131–144` (file is `frontend/src/browser/services/workspacesStore.ts`)
   - Line 258: `linkReiniger.ts:86–152` (file is `frontend/src/browser/services/linkReiniger.ts`)
   - Line 259: `SeitenSicherheitPopover.tsx:53–65` (file is `frontend/src/browser/kopf/SeitenSicherheitPopover.tsx`)
   - Line 260: `kopf/VertikaleTableiste.tsx:64–105` (file is `frontend/src/browser/kopf/VertikaleTableiste.tsx`)
   - Line 261: `SnapZonenOverlay.tsx:31–53` (file is `frontend/src/browser/seite/SnapZonenOverlay.tsx`)
   - Line 262: `LesemodusAnsicht.tsx:148–175, lesemodusService.ts` (files are `frontend/src/browser/seite/LesemodusAnsicht.tsx` and `frontend/src/browser/services/lesemodusService.ts`)
3. **Backend paths missing `src-tauri/src/` or `src-tauri/`:**
   - Line 249: `Cargo.toml` (file is `src-tauri/Cargo.toml`)
   - Line 250: `Cargo.toml:28–34, schild/mod.rs:110–128` (files are `src-tauri/Cargo.toml` and `src-tauri/src/schild/mod.rs`)
   - Line 251: `schild/listen.rs:26–57` (file is `src-tauri/src/schild/listen.rs`)
   - Line 252: `tabs/desktop/webview2.rs:176–221` (file is `src-tauri/src/tabs/desktop/webview2.rs`)
   - Line 253: `tabs/desktop/webview2.rs:381–419` (file is `src-tauri/src/tabs/desktop/webview2.rs`)
   - Line 254: `tabs/ruhe.rs:99–104, schlaf.rs:128–140` (files are `src-tauri/src/tabs/ruhe.rs` and `src-tauri/src/tabs/desktop/schlaf.rs`)
   - Line 256: `tabs/desktop.rs:67, ohne_edge.rs:24–36` (files are `src-tauri/src/tabs/desktop.rs` and `src-tauri/src/tabs/desktop/ohne_edge.rs`)
   - Line 258: `schild/verfolgung.rs:10–47` (file is `src-tauri/src/schild/verfolgung.rs`)
   - Line 259: `desktop.rs:432–478` (file is `src-tauri/src/tabs/desktop.rs`)

### 1.6 Shortened Citations in Section 4.1 (Lines 379–398)
The 10 Vibecoding milestones list files without module prefixes:
- Line 380 (Item 1): `tabs/desktop.rs:161` -> `src-tauri/src/tabs/desktop.rs:161`
- Line 382 (Item 2): `schild/mod.rs:110` -> `src-tauri/src/schild/mod.rs:110`
- Line 384 (Item 3): `tabs/desktop/schlaf.rs:133` -> `src-tauri/src/tabs/desktop/schlaf.rs:133`
- Line 386 (Item 4): `erweiterungen/crx.rs`, `paket.rs` -> `src-tauri/src/erweiterungen/crx.rs`, `src-tauri/src/erweiterungen/paket.rs`
- Line 388 (Item 5): `verfolgung.rs:28`, `desktop.rs:434` -> `src-tauri/src/schild/verfolgung.rs:28`, `src-tauri/src/tabs/desktop.rs:434`
- Line 392 (Item 7): `VertikaleTableiste.tsx:64` -> `frontend/src/browser/kopf/VertikaleTableiste.tsx:64`
- Line 394 (Item 8): `SnapZonenOverlay.tsx` -> `frontend/src/browser/seite/SnapZonenOverlay.tsx`
- Line 396 (Item 9): `LesemodusAnsicht.tsx:148` -> `frontend/src/browser/seite/LesemodusAnsicht.tsx:148`
- Line 398 (Item 10): `MsmSuche.tsx` -> `frontend/src/browser/seite/MsmSuche.tsx`

### 1.7 Other Unprefixed Citations in Sections 2.2, 2.3, 3.3, 3.4, 4.2, 5
- **Section 2.2.1 (Lines 126–127):** `downloads/pruefung.rs`, `fenster.rs`, `kurzinfo.rs`, `mixer.rs`, `tabs/android/bruecke.rs` are all located under `src-tauri/src/`.
- **Section 2.3.2 (Line 184):** `(Schluesselfach.kt)` is located at `src-tauri/gen/android/app/src/main/java/com/mauntingstudios/secure_browser/Schluesselfach.kt`.
- **Section 3.3.1 (Line 295):** `desktop/schlaf.rs (Zeilen 128–140)` should be `src-tauri/src/tabs/desktop/schlaf.rs (Zeilen 128–140)`.
- **Section 3.4.3 (Lines 339, 341):** `(desktop.rs:67)` and `(ohne_edge.rs:24–36)` should be `(src-tauri/src/tabs/desktop.rs:67)` and `(src-tauri/src/tabs/desktop/ohne_edge.rs:24–36)`.
- **Section 4.2.1 (Line 407):** `(Cargo.toml:53)` should be `(src-tauri/Cargo.toml:53)`.
- **Section 4.2.3 (Line 420):** `(erweiterungen/mod.rs:2–3)` should be `(src-tauri/src/erweiterungen/mod.rs:2–3)`.
- **Section 5.1 (Line 459, 469):** `(tabs/desktop.rs:171–182)` and `schild/sperre.rs` should have `src-tauri/src/`.
- **Section 5.2 (Line 478):** `popup.rs:7–8` should be `src-tauri/src/tabs/desktop/popup.rs:7–8`.
- **Section 5.3 (Line 497):** `LesemodusAnsicht.tsx:169–175` should be `frontend/src/browser/seite/LesemodusAnsicht.tsx:169–175`.

---

## 2. Logic Chain

1. **Premise 1:** An audit deliverable that is intended to serve as a benchmark technical review must provide unambiguous, copy-paste reproducible file paths and commands.
2. **Premise 2:** If an auditor clones the repository and follows Section 6.2, they will execute `cd browser/frontend` and fail immediately because the directory does not exist. If they find `frontend` and run `npm run test:run`, npm aborts with an unhandled error because `"test:run"` does not exist.
3. **Premise 3:** Within the document, file paths vary between fully qualified paths (e.g. `frontend/src/locales/de.json:4924`, `src-tauri/src/tabs/desktop.rs`), partial directory paths (e.g. `services/workspacesStore.ts`, `kopf/VertikaleTableiste.tsx`), and bare file names (e.g. `linkReiniger.ts`, `MsmSuche.tsx`, `SnapZonenOverlay.tsx`).
4. **Premise 4:** Standardizing all file references to include their module prefix (`frontend/src/browser/...` and `src-tauri/src/...`) maintains consistent reader orientation across the 587-line document without altering the technical meaning of any finding.
5. **Deduction:** The Worker must apply the exact path, command, and citation alignments documented below.

---

## 3. Caveats

- **Explorer Fix 1 Scope:** Explorer Fix 1 is focusing specifically on Section 6.2/6.3. The recommendations in this report for Section 6.2/6.3 are fully compatible with and reinforce Explorer Fix 1's findings.
- **Explorer Fix 2 Scope:** Explorer Fix 2 is focusing specifically on Section 2.3.3 (`pruefe_fach()` 3-compartment whitelist vs. `geheimnisse.rs` internal token). Nothing in this report contradicts Explorer Fix 2.
- **Headless Environment:** Frontend Vitest unit tests run with Happy-DOM / Jest-DOM in a headless environment. All 52 target tests and 21 `MsmSuche.test.tsx` tests pass 100%.

---

## 4. Conclusion & Actionable Recommendations for the Worker

The following concrete replacement instructions provide the exact before/after blocks for the Worker to apply to `AUDIT_REPORT_PONTIS.md`.

### Recommendation 1: Fix Section 6.2 Working Directory and Command Syntax (Lines 545–561)

**Target Lines:** 545–561  
**Replace:**
```markdown
### 6.2 Frontend: Komponenten- und Servicetests
Ausführen im Arbeitsverzeichnis `browser/frontend`:

```powershell
# 1. Verifikation der hierarchischen Baum-Tabs und Kindzuweisung
npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx

# 2. Verifikation des Lesemodus (Wikipedia-Bereinigung, Link-Modi, MSM/KI-Export)
npm run test:run src/browser/seite/LesemodusAnsicht.test.tsx

# 3. Verifikation des 1-Klick Site Reset Popovers
npm run test:run src/browser/kopf/SeitenSicherheitPopover.test.tsx

# 4. Verifikation der Tab-Zustandsverwaltung
npm run test:run src/browser/services/tabsStore.test.ts
```
```

**With:**
```markdown
### 6.2 Frontend: Komponenten- und Servicetests
Ausführen im Arbeitsverzeichnis `frontend` (aus dem Repository-Hauptverzeichnis) bzw. `../frontend` (aus dem Verzeichnis `browser`):

```powershell
# 1. Verifikation der hierarchischen Baum-Tabs und Kindzuweisung
npm test -- src/browser/kopf/VertikaleTableiste.test.tsx

# 2. Verifikation des Lesemodus (Wikipedia-Bereinigung, Link-Modi, MSM/KI-Export)
npm test -- src/browser/seite/LesemodusAnsicht.test.tsx

# 3. Verifikation des 1-Klick Site Reset Popovers
npm test -- src/browser/kopf/SeitenSicherheitPopover.test.tsx

# 4. Verifikation der Tab-Zustandsverwaltung
npm test -- src/browser/services/tabsStore.test.ts
```
```

---

### Recommendation 2: Fix Section 6.3 Directory Navigation and Line Citation (Lines 563–576)

**Target Lines:** 563–576  
**Replace:**
```markdown
### 6.3 Statische Code-Inspektionen für Sicherheitsinvarianten
- **Unsafe-Freiheit in `schluesselfach` prüfen:**
  ```powershell
  cd crates/schluesselfach
  git grep "unsafe"
  ```
  *Befund:* Exakt 0 Treffer.
- **Deaktivierung von SmartScreen und Edge-Telemetrie prüfen:**
  `browser/src-tauri/src/tabs/desktop.rs`, Zeile 67:
  `const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";`
- **Physisches Verwerfen von WebView2-Controllern prüfen:**
  `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 135–139:
  `NATIV.with(|n| n.borrow_mut().remove(id))` in Kombination mit `super::herunterladen::schliessen(id, tab.controller);`.
```

**With:**
```markdown
### 6.3 Statische Code-Inspektionen für Sicherheitsinvarianten
- **Unsafe-Freiheit in `schluesselfach` prüfen (aus dem Repository-Hauptverzeichnis):**
  ```powershell
  cd crates/schluesselfach
  git grep "unsafe"
  ```
  *(Hinweis: Bei vorheriger Ausführung in `frontend` zuvor mit `cd ../crates/schluesselfach` navigieren).*  
  *Befund:* Exakt 0 Treffer in Rust-Quellen.
- **Deaktivierung von SmartScreen und Edge-Telemetrie prüfen:**
  `browser/src-tauri/src/tabs/desktop.rs`, Zeile 67:
  `const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";`
- **Physisches Verwerfen von WebView2-Controllern prüfen:**
  `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 133–138:
  `NATIV.with(|n| n.borrow_mut().remove(id))` in Kombination mit `super::herunterladen::schliessen(id, tab.controller);`.
```

---

### Recommendation 3: Standardize Path Prefixes in Section 3.1 Comparison Matrix (Lines 247–263)

**Target Lines:** 247–263  
**Replace:**
```markdown
| Bewertungsbereich | Google Chrome (v130+) | Brave Browser (v1.70+) | Maunting Pontis (v5.5.0) | Fundstelle im Pontis-Code |
| :--- | :--- | :--- | :--- | :--- |
| **Architekturmodell** | Monolithischer C++ Chromium Fork | Monolithischer C++/Rust Chromium Fork | Modulare Hybrid-Architektur (Tauri v2 + WebView2 Shell + Safe Rust) | `src-tauri/src/lib.rs`, `Cargo.toml` |
| **Adblock-Engine** | Keine native Engine; Manifest V3 DNR (strenge Regellimits) | Brave Shields (native `adblock-rust` Engine in C++ Core) | Brave `adblock` Engine (v0.13, MPL-2.0) direkt in Rust integriert | `Cargo.toml:28–34`, `schild/mod.rs:110–128` |
| **Filterlisten** | Nur statische Listen via WebExtensions | EasyList, EasyPrivacy, uBlock-Listen, autom. Updates | EasyList, EasyList Germany, EasyPrivacy, Fanboy Cookie Monster | `schild/listen.rs:26–57` |
| **Netzwerk-Abfangung** | Browser-interner Network Service | C++ Chromium Network Hooking | `ICoreWebView2_22` Pre-Network WebResourceRequested Filter | `tabs/desktop/webview2.rs:176–221` |
| **Kosmetisches Filtern** | Durch MV3 DNR drastisch limitiert | V8-Injektion von CSS & Scriptlets | CDP `Runtime.evaluate` mit `userGesture: false` auf `DOMContentLoaded` | `tabs/desktop/webview2.rs:381–419` |
| **Speicherverbrauch Inaktiver Tabs** | Chromium Tab Discarding (~40–120 MB RAM Restbelegung) | Chromium Memory Saver (~40–100 MB RAM Restbelegung) | **0,00 MB RAM** durch physische COM-Controller-Zerstörung (`verwerfen`) | `tabs/ruhe.rs:99–104`, `schlaf.rs:128–140` |
| **Workspace-Speicherverwaltung** | Tab-Gruppen belegen weiterhin Arbeitsspeicher | Tab-Gruppen belegen weiterhin Arbeitsspeicher | **0,00 MB RAM** für alle Tabs inaktiver Workspaces (Deep-Sleep) | `services/workspacesStore.ts:131–144` |
| **Telemetrie & Datensammlung** | Sehr hoch (Google-Dienste, Metrics, SafeBrowsing, Topics API) | Deaktiviert (Opt-in für anonymisierte P3A-Metriken) | **Vollständig abwesend** (SmartScreen & Edge-UI per Switch deaktiviert) | `tabs/desktop.rs:67`, `ohne_edge.rs:24–36` |
| **Nutzerkonto-Zwang** | Stark forciert (Google-Sync, Profil-Zwang) | Kein Zwang; optionale Brave Sync Kette | **Vollständig kontofrei**; keine zentralen Cloud-Sync-Endpunkte | `frontend/src/locales/de.json:4924` |
| **Clean Copy (URL-Bereinigung)** | Nicht vorhanden (Drittanbieter-Erweiterung nötig) | Vorhanden („Copy Link Clean“ im Kontextmenü) | **Vollautomatisch** bei jeder Navigation (Rust) und beim Kopieren (TS) | `schild/verfolgung.rs:10–47`, `linkReiniger.ts:86–152` |
| **1-Klick Site Reset** | Umständlich über Browsereinstellungen | Über Shields-Panel / Seiteneinstellungen | **1 Klick** im Adresszeilen-Popover via CDP Origin Data Wipe | `SeitenSicherheitPopover.tsx:53–65`, `desktop.rs:432–478` |
| **Vertikale Baum-Tabs** | Nicht vorhanden (nur horizontale Leiste) | Nicht hierarchisch (nur lineare vertikale Liste) | **Nativ hierarchisch** (Ein-/Ausklappen, Ast-Glyphen, D&D, Vererbung) | `kopf/VertikaleTableiste.tsx:64–105` |
| **Split-Screen Modus** | Nicht vorhanden (erfordert manuelle OS-Fensterkachelung) | Eingeschränkte Side-by-Side Kachelung | **4-Wege Snap-Assist** mit stufenlosem Trenner (20–80 %) via HWND | `SnapZonenOverlay.tsx:31–53`, `tabs_split_setzen` |
| **Lesemodus & KI-Anbindung** | Einfacher Reader Mode | Speedreader + Brave Leo (proprietäres Cloud-LLM) | Puristischer Reader + **verschlüsselter MSM-Notizexport + Singra-KI** | `LesemodusAnsicht.tsx:148–175`, `lesemodusService.ts` |
```

**With:**
```markdown
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
```

---

### Recommendation 4: Standardize Citations in Section 4.1 (Lines 379–398)

**Target Lines:** 379–398  
**Update citations within the 10 Vibecoding milestones:**
- Item 1 (Line 380): Change `tabs/desktop.rs:161` to `src-tauri/src/tabs/desktop.rs:161`.
- Item 2 (Line 382): Change `schild/mod.rs:110` to `src-tauri/src/schild/mod.rs:110`.
- Item 3 (Line 384): Change `tabs/desktop/schlaf.rs:133` to `src-tauri/src/tabs/desktop/schlaf.rs:133`.
- Item 4 (Line 386): Change `(erweiterungen/crx.rs, paket.rs)` to `(src-tauri/src/erweiterungen/crx.rs, src-tauri/src/erweiterungen/paket.rs)`.
- Item 5 (Line 388): Change `(verfolgung.rs:28, desktop.rs:434)` to `(src-tauri/src/schild/verfolgung.rs:28, src-tauri/src/tabs/desktop.rs:434)`.
- Item 7 (Line 392): Change `VertikaleTableiste.tsx:64` to `frontend/src/browser/kopf/VertikaleTableiste.tsx:64`.
- Item 8 (Line 394): Change `(SnapZonenOverlay.tsx, tabs_split_setzen)` to `(frontend/src/browser/seite/SnapZonenOverlay.tsx, src-tauri/src/tabs/desktop.rs:360)`.
- Item 9 (Line 396): Change `LesemodusAnsicht.tsx:148` to `frontend/src/browser/seite/LesemodusAnsicht.tsx:148`.
- Item 10 (Line 398): Change `(MsmSuche.tsx)` to `(frontend/src/browser/seite/MsmSuche.tsx)`.

---

### Recommendation 5: Minor Path Refinements in Remaining Sections
1. **Line 126–127:** Update `(downloads/pruefung.rs)`, `(fenster.rs, kurzinfo.rs)`, `(mixer.rs)` and `(tabs/android/bruecke.rs)` with the `src-tauri/src/` prefix.
2. **Line 184:** Change `Unter Android (Schluesselfach.kt)` to `Unter Android (src-tauri/gen/android/.../Schluesselfach.kt)`.
3. **Line 295:** Change `über desktop/schlaf.rs (Zeilen 128–140)` to `über src-tauri/src/tabs/desktop/schlaf.rs (Zeilen 128–140)`.
4. **Lines 339, 341:** Change `(desktop.rs:67)` and `(ohne_edge.rs:24–36)` to `(src-tauri/src/tabs/desktop.rs:67)` and `(src-tauri/src/tabs/desktop/ohne_edge.rs:24–36)`.
5. **Line 407:** Change `(Cargo.toml:53)` to `(src-tauri/Cargo.toml:53)`.
6. **Line 420:** Change `(erweiterungen/mod.rs:2–3)` to `(src-tauri/src/erweiterungen/mod.rs:2–3)`.
7. **Line 459:** Change `(tabs/desktop.rs:171–182)` to `(src-tauri/src/tabs/desktop.rs:171–182)`.
8. **Line 469:** Change `schild/sperre.rs` to `src-tauri/src/schild/sperre.rs`.
9. **Line 478:** Change `popup.rs:7–8` to `src-tauri/src/tabs/desktop/popup.rs:7–8`.
10. **Line 497:** Change `LesemodusAnsicht.tsx:169–175` to `frontend/src/browser/seite/LesemodusAnsicht.tsx:169–175`.

---

## 5. Verification Method

To verify these recommendations:
1. **Frontend Tests Execution:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\frontend
   npm test -- src/browser/kopf/VertikaleTableiste.test.tsx
   npm test -- src/browser/seite/LesemodusAnsicht.test.tsx
   npm test -- src/browser/kopf/SeitenSicherheitPopover.test.tsx
   npm test -- src/browser/services/tabsStore.test.ts
   npm test -- src/browser/seite/MsmSuche.test.tsx
   ```
   *Expected:* 5 test suites pass completely (73 tests passed, 0 failed).
2. **Path Verification:**
   All referenced paths resolve from the repository root:
   - `browser/src-tauri/...`
   - `frontend/src/browser/...`
   - `crates/schluesselfach/...`
