# Adversarial Challenge & Fact-Checking Report: Maunting Pontis Audit

**Agent:** Challenger 1 (`teamwork_preview_challenger`)  
**Target Deliverable:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\AUDIT_REPORT_PONTIS.md`  
**Verdict:** **REQUEST_CHANGES** (Minor correction required in Section 6.2 reproduction instructions; all 6 technical core claims are verified and accurate)

---

## 1. Observation

Direct empirical evidence obtained by inspecting files, executing test suites, and running search queries:

### 1.1 Item 1: `crates/schluesselfach` Unsafe Audit
- **Command:** Ripgrep pattern search for `unsafe` across `crates/schluesselfach` excluding compilation artifacts (`**/target/**`).
- **Result:** Exactly 0 matches in `.rs` source code.
- **Files inspected:** `crates/schluesselfach/src/lib.rs`, `befehle.rs`, `biometrie.rs`, `geheimnisse.rs`.
- **Test execution:** `cargo test` in `crates/schluesselfach` executed 4 tests:
  ```
  test geheimnisse::tests::das_abgelegte_token_gilt_und_eine_liegengebliebene_datei_faellt ... ok
  test geheimnisse::tests::die_klartextdatei_wandert_in_den_schluesselbund_und_verschwindet ... ok
  test geheimnisse::tests::scheitert_das_ablegen_bleibt_die_datei_und_damit_die_kopplung ... ok
  test geheimnisse::tests::ohne_token_und_ohne_datei_ist_nichts_da ... ok
  test result: ok. 4 passed; 0 failed; finished in 0.01s
  ```
- **Finding:** Claim verified. 0 occurrences of unsafe code.

### 1.2 Item 2: Tauri IPC 81 Commands & Capability Scoping
- **`src-tauri/build.rs` (lines 9–91):**
  Defines `const BEFEHLE: &[&str] = &[ ... ];` containing items from line 10 (`"konfig_laden"`) through line 90 (`"update_installieren"`), totaling **81 commands**.
- **`src-tauri/permissions/oberflaeche.toml` (lines 3–88):**
  Defines permission set `oberflaeche` containing items from line 7 (`"allow-konfig-laden"`) through line 87 (`"allow-update-installieren"`), totaling **81 permissions**.
- **`src-tauri/capabilities/oberflaeche.json` (lines 1–13):**
  ```json
  {
    "identifier": "oberflaeche",
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
  Explicitly restricts the `oberflaeche` permission set exclusively to `"webviews": ["main"]`.
- **Test execution:** `cargo test --lib -- tests::rechte_decken_jeden_befehl` in `browser/src-tauri`:
  ```
  test tests::rechte_decken_jeden_befehl ... ok
  test result: ok. 1 passed; 0 failed; finished in 0.00s
  ```
- **Finding:** Claim verified. Exactly 81 commands registered and scoped to `main`.

### 1.3 Item 3: Brave Adblock v0.13 Integration
- **`src-tauri/Cargo.toml` (lines 31–34):**
  ```toml
  adblock = { version = "0.13", default-features = false, features = [
      "embedded-domain-resolver",
      "full-regex-handling",
  ] }
  ```
- **`src-tauri/src/schild/mod.rs` (lines 16–17, 110–128):**
  Uses `adblock::request::Request` and `adblock::Engine` behind thread-safe locks:
  ```rust
  pub struct Schild {
      werbung: RwLock<Engine>,
      tracker: RwLock<Engine>,
      aktiv: AtomicBool,
      ...
  }
  ```
- **Test execution:** `cargo test --lib -- schild::tests`:
  ```
  test schild::tests::android_raet_die_art_einer_anfrage ... ok
  test schild::tests::ein_geblockter_ping_bekommt_204 ... ok
  test schild::tests::ausnahmen_gelten_wie_in_der_oberflaeche ... ok
  test schild::tests::ein_selektor_schreibt_keine_eigenen_regeln ... ok
  test schild::tests::filtert_nach_listen_und_ausnahmen ... ok
  test result: ok. 5 passed; 0 failed; finished in 0.00s
  ```
- **Finding:** Claim verified. Brave `adblock` 0.13 is integrated and functional.

### 1.4 Item 4: Clean Copy 48 Parameters
- **`src-tauri/src/schild/verfolgung.rs` (lines 11–17):**
  ```rust
  const NAMEN: [&str; 48] = [
      "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "ysclid", "twclid", "ttclid",
      "igshid", "igsh", "li_fat_id", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "__hssc", "__hstc", "__hsfp",
      "hsctatracking", "mkt_tok", "oly_anon_id", "oly_enc_id", "vero_id", "vero_conv", "wickedid", "rb_clickid",
      "_openstat", "__s", "srsltid", "si", "feature", "ref_src", "ref_url", "ved", "usg", "cvid",
      "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r", "pf_rd_m", "pf_rd_s", "pf_rd_t", "pf_rd_i",
  ];
  ```
- **Lines 19–22:** `name.starts_with("utm_") || NAMEN.contains(&name.as_str())`.
- **Test execution:** `cargo test --lib -- schild::verfolgung::tests`:
  ```
  test schild::verfolgung::tests::laesst_alles_andere_wie_es_ist ... ok
  test schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus ... ok
  test result: ok. 2 passed; 0 failed; finished in 0.00s
  ```
- **Finding:** Claim verified. Exactly 48 parameters defined in `NAMEN` plus `utm_*` prefix.

### 1.5 Item 5: Deep-Sleep & Destruction of Native COM Controllers
- **`src-tauri/src/tabs/desktop/schlaf.rs` (lines 129–139):**
  `verwerfen` removes the tab from `NATIV` thread-local storage and invokes `super::herunterladen::schliessen(id, tab.controller)`.
- **`src-tauri/src/tabs/desktop/herunterladen.rs` (lines 100–109):**
  ```rust
  pub(super) unsafe fn schliessen(tab: &str, controller: ICoreWebView2Controller) {
      if laeuft(tab) {
          ...
      } else if let Err(e) = controller.Close() {
          eprintln!("[MSB] Download-Tab nicht geschlossen: {e}");
      }
  }
  ```
  Calls `controller.Close()`, explicitly releasing COM references and destroying the underlying WebView2 controller.
- **`frontend/src/browser/services/workspacesStore.ts` (lines 131–143):**
  On workspace change, iterates inactive tabs:
  ```typescript
  for (const tab of verlasseneTabs) {
    if (tab.nativDa && !tab.ton) {
      void nativ.tabVerwerfen(tab.id)
      useTabsStore.setState((s) => ({
        tabs: s.tabs.map((t) => (t.id === tab.id ? { ...t, nativDa: false, ruhe: 'verworfen' } : t)),
      }))
    }
  }
  ```
- **Test execution:**
  - `cargo test --lib -- tabs::ruhe::tests`: 7 passed.
  - `npx vitest run src/browser/services/workspacesStore.test.ts`: 8 passed.
- **Finding:** Claim verified. Deep-Sleep calls `controller.Close()` to destroy native controllers.

### 1.6 Item 6: Technical Accuracy of 10 Achievements and 6 Limitations
- All 10 Vibecoding achievements verified against code citations:
  1. IPC isolation: `desktop.rs:161`
  2. Adblock engine: `Cargo.toml:31`, `schild/mod.rs:110`
  3. 0 MB RAM discarding: `schlaf.rs:129`, `herunterladen.rs:106`
  4. CRX3 cryptosystem & zip bomb limits: `erweiterungen/crx.rs`, `paket.rs` (10,000 files, 512 MB limit)
  5. Clean copy & 1-click CDP reset: `verfolgung.rs:11`, `desktop.rs:434`
  6. Biometrics key vault: `crates/schluesselfach` (`UserConsentVerifier`, AES-256-GCM, 0 unsafe)
  7. Tree-style tabs & orphan adoption: `VertikaleTableiste.tsx:64`, `tabsStore.ts:262`
  8. 4-way Snap-assist & DPI bounds: `SnapZonenOverlay.tsx:31`, `desktop.rs:360`
  9. Reader mode, MSM notes & Singra AI prompt: `LesemodusAnsicht.tsx:148`
  10. SearXNG search integration: `MsmSuche.tsx` (all 21 unit tests passed)
- All 6 Limitations verified:
  1. Platform runtime coupling & `#[cfg(windows)]` in `tabs/mod.rs:31`
  2. Chromium Zero-Day patch release latency
  3. WebExtension Tab-API limitation (`popup.rs:7–8` verbatim quote)
  4. Absence of in-engine fingerpriting farbling
  5. DRM / Widevine limitations in WebView2
  6. Shared broker process across workspace profiles (`desktop.rs:171–183`)

### 1.7 Defect Discovered in Section 6.2 (Verification Reproduction Commands)
When attempting to follow Section 6.2 ("Frontend: Komponenten- und Servicetests") verbatim:
1. **Directory Path Error:**
   - Report text: `Ausführen im Arbeitsverzeichnis browser/frontend:`
   - Reality: Directory `browser/frontend` does not exist. The frontend repository directory is `frontend` at the worktree root (`.../browser_competitiveness_security_audit/frontend`).
2. **NPM Script Error:**
   - Report text:
     ```powershell
     npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx
     ```
   - Reality: Running `npm run test:run` in `frontend` fails with exit code 1:
     ```
     npm error Missing script: "test:run"
     npm error To see a list of scripts, run:
     npm error   npm run
     ```
   - `frontend/package.json` contains:
     ```json
     "scripts": {
       "test": "vitest run",
       "test:watch": "vitest"
     }
     ```
   - The correct command is `npx vitest run <file>` or `npm test -- <file>`.
   - When run with `npx vitest run`, all 4 specified test suites execute and pass completely (52 tests passed).

---

## 2. Logic Chain

1. **Premise 1:** An adversarial technical audit report must have 100% verifiable code references and 100% reproducible verification commands.
2. **Observation 1:** All core architectural assertions (0 unsafe, 81 commands, adblock 0.13, 48 tracking params, Deep-Sleep controller destruction, 10 achievements, 6 limitations) are thoroughly validated by inspecting source code and running test suites.
3. **Observation 2:** The reproduction instructions in Section 6.2 fail on copy-paste because the path `browser/frontend` is invalid and the npm script `test:run` does not exist in `frontend/package.json`.
4. **Deduction:** The report is technically high quality and accurate in its findings, but contains a copy-paste reproduction defect in Section 6.2 that must be corrected so that external reviewers and developers can execute the test steps without error.

---

## 3. Caveats

- End-to-end GUI interaction tests were verified via the Jest-DOM / Happy-DOM Vitest suite rather than launching a live physical GUI window, as the environment is headless. The Vitest suite covers components, stores, and mocked IPC interactions.
- All Rust tests were executed using `cargo test --lib` under Windows x86_64 debug profile.

---

## 4. Conclusion & Verdict

**Verdict:** **REQUEST_CHANGES**

### Required Action Items for the Author of `AUDIT_REPORT_PONTIS.md`:
In `AUDIT_REPORT_PONTIS.md`, Section 6.2 (lines 546–560), update the reproduction steps as follows:

```markdown
### 6.2 Frontend: Komponenten- und Servicetests
Ausführen im Arbeitsverzeichnis `frontend`:

```powershell
# 1. Verifikation der hierarchischen Baum-Tabs und Kindzuweisung
npx vitest run src/browser/kopf/VertikaleTableiste.test.tsx

# 2. Verifikation des Lesemodus (Wikipedia-Bereinigung, Link-Modi, MSM/KI-Export)
npx vitest run src/browser/seite/LesemodusAnsicht.test.tsx

# 3. Verifikation des 1-Klick Site Reset Popovers
npx vitest run src/browser/kopf/SeitenSicherheitPopover.test.tsx

# 4. Verifikation der Tab-Zustandsverwaltung
npx vitest run src/browser/services/tabsStore.test.ts
```
```

Once Section 6.2 is updated with the valid directory and script invocation, the report satisfies all verification criteria and is fully approved.

---

## 5. Verification Method

To independently verify the defect and the technical claims:

1. **Verify Section 6.2 Failure:**
   ```powershell
   # Observe missing folder:
   Test-Path C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\frontend
   # Output: False

   # Observe missing script in frontend:
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\frontend
   npm run test:run
   # Output: npm error Missing script: "test:run"
   ```

2. **Verify Working Frontend Tests:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\frontend
   npx vitest run src/browser/kopf/VertikaleTableiste.test.tsx src/browser/seite/LesemodusAnsicht.test.tsx src/browser/kopf/SeitenSicherheitPopover.test.tsx src/browser/services/tabsStore.test.ts
   # Output: 4 passed (52 tests passed)
   ```

3. **Verify Rust Test Suite:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri
   cargo test --lib -- tests::rechte_decken_jeden_befehl
   cargo test --lib -- schild::tests::filtert_nach_listen_und_ausnahmen
   cargo test --lib -- schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus
   cargo test --lib -- tabs::ruhe::tests
   ```

4. **Verify `schluesselfach` Unsafe Count:**
   ```powershell
   cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\crates\schluesselfach
   cargo test
   git grep "unsafe" -- src/
   # Output: 0 matches
   ```
