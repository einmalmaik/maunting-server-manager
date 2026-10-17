# Handoff Report — Challenger 2 (Empirical Test Verification)

**Deliverable Challenged:** `AUDIT_REPORT_PONTIS.md`  
**Review Type:** Empirical Challenger (critic / specialist)  
**Agent ID:** Challenger 2 (`challenger_2`)  
**Target Date:** 2026-10-17  
**Verdict:** **REQUEST_CHANGES** (Fix Section 6.2 test invocation commands and directory path; all underlying Rust and Frontend test suites are confirmed 100% PASSED)

---

## 1. Observation

### 1.1 Rust Backend Test Execution (`browser/src-tauri`)

#### 1.1.1 IPC Permission Coverage (`tests::rechte_decken_jeden_befehl`)
- **Command:** `cargo test --lib -- tests::rechte_decken_jeden_befehl`
- **Cwd:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri`
- **Exit Code:** `0`
- **Output:**
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 2.51s
     Running unittests src\lib.rs (target\debug\deps\maunting_secure_browser_lib-e3f013f452d550db.exe)

running 1 test
test tests::rechte_decken_jeden_befehl ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 170 filtered out; finished in 0.00s
```

#### 1.1.2 Content Security Policy & Eval Safety (`tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval`)
- **Command:** `cargo test --lib -- tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval`
- **Cwd:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri`
- **Exit Code:** `0`
- **Output:**
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 2.47s
     Running unittests src\lib.rs (target\debug\deps\maunting_secure_browser_lib-e3f013f452d550db.exe)

running 1 test
test tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 170 filtered out; finished in 0.00s
```

#### 1.1.3 Clean Copy Tracking Parameter Stripping (`schild::verfolgung::tests`)
- **Command:** `cargo test --lib -- schild::verfolgung::tests`
- **Cwd:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri`
- **Exit Code:** `0`
- **Output:**
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 2.46s
     Running unittests src\lib.rs (target\debug\deps\maunting_secure_browser_lib-e3f013f452d550db.exe)

running 2 tests
test schild::verfolgung::tests::laesst_alles_andere_wie_es_ist ... ok
test schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 169 filtered out; finished in 0.00s
```

#### 1.1.4 Deep-Sleep Thresholds and 0 MB RAM Discarding (`tabs::ruhe::tests`)
- **Command:** `cargo test --lib -- tabs::ruhe::tests`
- **Cwd:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri`
- **Exit Code:** `0`
- **Output:**
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 2.38s
     Running unittests src\lib.rs (target\debug\deps\maunting_secure_browser_lib-e3f013f452d550db.exe)

running 7 tests
test tabs::ruhe::tests::ausnahmen_gelten_auch_fuer_unterdomains ... ok
test tabs::ruhe::tests::der_vordere_und_der_im_vollbild_schlafen_nie ... ok
test tabs::ruhe::tests::nie_heisst_nie ... ok
test tabs::ruhe::tests::nach_vorne_geholt_zaehlt_die_zeit_neu ... ok
test tabs::ruhe::tests::verworfen_wird_nur_mit_speicher_sparen_und_nie_privat ... ok
test tabs::ruhe::tests::verborgene_tabs_schlafen_nach_der_wartezeit ... ok
test tabs::ruhe::tests::die_oberflaeche_schickt_nur_gueltiges ... ok

test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 164 filtered out; finished in 0.00s
```

#### 1.1.5 Additional Section 6.1 Tests
- `cargo test --lib -- tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung`: **PASSED** (1 passed, 0 failed)
- `cargo test --lib -- tabs::weg::tests`: **PASSED** (10 passed, 0 failed)
- `cargo test --lib -- schild::tests::filtert_nach_listen_und_ausnahmen`: **PASSED** (1 passed, 0 failed)
- Full crate lib test (`cargo test --lib`): **PASSED** (168 passed, 0 failed, 3 ignored, finished in 0.50s)

---

### 1.2 Cryptographic Key Storage (`crates/schluesselfach`)
- **Command:** `cargo test`
- **Cwd:** `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\crates\schluesselfach`
- **Exit Code:** `0`
- **Output:**
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 1.91s
     Running unittests src\lib.rs (target\debug\deps\schluesselfach-687ba786a21e8053.exe)

running 4 tests
test geheimnisse::tests::das_abgelegte_token_gilt_und_eine_liegengebliebene_datei_faellt ... ok
test geheimnisse::tests::die_klartextdatei_wandert_in_den_schluesselbund_und_verschwindet ... ok
test geheimnisse::tests::scheitert_das_ablegen_bleibt_die_datei_und_damit_die_kopplung ... ok
test geheimnisse::tests::ohne_token_und_ohne_datei_ist_nichts_da ... ok

test result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s

   Doc-tests schluesselfach

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
```
- **Unsafe Audit:** `git grep "unsafe"` in `crates/schluesselfach` returned exit code 1 with 0 matches. Confirms 0 occurrences of `unsafe`.

---

### 1.3 Frontend Test Verification (`frontend`)

#### 1.3.1 Verbatim Command Failure (Section 6.2 Documentation Defect)
In `AUDIT_REPORT_PONTIS.md`, Section 6.2, lines 548–560 state:
```powershell
Ausführen im Arbeitsverzeichnis browser/frontend:

# 1. Verifikation der hierarchischen Baum-Tabs und Kindzuweisung
npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx
```
Direct execution of this command yielded:
- **Command:** `npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx`
- **Cwd:** `frontend`
- **Exit Code:** `1`
- **Stderr:**
```
npm error Missing script: "test:run"
npm error
npm error To see a list of scripts, run:
npm error   npm run
```
Inspection of `frontend/package.json` confirmed:
- Lines 15–16:
  ```json
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    ...
  }
  ```
There is no `test:run` script. Furthermore, the directory is `frontend`, not `browser/frontend`.

#### 1.3.2 Execution with Valid Invocation Syntax (`npm test -- <path>`)
After installing dependencies (`npm ci`), the tests documented in Section 6.2 were executed using valid npm syntax:

1. **`src/browser/kopf/VertikaleTableiste.test.tsx`**:
   - **Command:** `npm test -- src/browser/kopf/VertikaleTableiste.test.tsx` (or `npx vitest run src/browser/kopf/VertikaleTableiste.test.tsx`)
   - **Result:** **PASSED** (1 test file, 8 passed, 0 failed)
2. **`src/browser/seite/LesemodusAnsicht.test.tsx`**:
   - **Command:** `npm test -- src/browser/seite/LesemodusAnsicht.test.tsx`
   - **Result:** **PASSED** (1 test file, 14 passed, 0 failed)
3. **`src/browser/kopf/SeitenSicherheitPopover.test.tsx`**:
   - **Command:** `npm test -- src/browser/kopf/SeitenSicherheitPopover.test.tsx`
   - **Result:** **PASSED** (1 test file, 3 passed, 0 failed)
4. **`src/browser/services/tabsStore.test.ts`**:
   - **Command:** `npm test -- src/browser/services/tabsStore.test.ts`
   - **Result:** **PASSED** (1 test file, 27 passed, 0 failed)

**Summary for the 4 targeted test suites:** 52 tests passed, 0 failed.

#### 1.3.3 Comprehensive Browser Frontend Test Suite
- **Command:** `npm test -- src/browser`
- **Cwd:** `frontend`
- **Result:**
```
Test Files  81 passed (81)
     Tests  524 passed (524)
  Duration  151.58s
```
All 81 test files and 524 test cases in the browser frontend passed with 0 failures.

---

## 2. Logic Chain

1. **Rust Tests:** Observation 1.1 confirms that all test filters cited in Section 6.1 exist in `src-tauri` and pass with 100% success rate (`168 passed; 0 failed; 3 ignored`). The claims regarding IPC command manifest registration, CSP eval/image restrictions, tracking parameter removal, and tab sleep thresholds are empirically verified.
2. **Keyring Crate:** Observation 1.2 confirms that `crates/schluesselfach` contains 0 `unsafe` code blocks and all 4 unit tests pass.
3. **Frontend Test Implementation:** Observation 1.3.2 and 1.3.3 confirm that the actual test implementations for Tree Tabs, Reader Mode, Site Reset Popover, and Tabs Store exist and pass with 100% success rate (52/52 target tests, 524/524 browser tests).
4. **Report Discrepancy:** Observation 1.3.1 reveals that Section 6.2 contains two defects:
   - Path error: refers to `browser/frontend` instead of `frontend/`.
   - Script name error: refers to `npm run test:run` instead of `npm test --` or `npx vitest run`.
5. **Impact Assessment:** While the technical claims of Pontis' architecture and test coverage are 100% factually accurate, the documentation of verification commands in Section 6.2 fails when executed verbatim by an auditor. An empirical audit report must provide copy-paste reproducible commands.

---

## 3. Caveats

- Win32 COM runtime rendering was tested via headless Rust unit test suites (`tests::*`, `tabs::*`), not via an interactive manual GUI display session.
- `machina@7.0.1` issued an npm engine warning during `npm ci` (`required: >=22.22, current: v22.16.0`), but this had no effect on test execution.

---

## 4. Conclusion

- **Technical Substantive Assessment:** **EXCELLENT**. All 168 Rust backend unit tests, all 4 `schluesselfach` unit tests, and all 524 frontend browser tests pass without a single failure. The security invariants stated in `AUDIT_REPORT_PONTIS.md` are empirically verified.
- **Formal Verification Section Defect:** Section 6.2 contains incorrect reproduction commands (`npm run test:run` and directory `browser/frontend`).
- **Verdict:** **REQUEST_CHANGES**
  - **Required Action:** Update Section 6.2 in `AUDIT_REPORT_PONTIS.md` to:
    ```markdown
    ### 6.2 Frontend: Komponenten- und Servicetests
    Ausführen im Arbeitsverzeichnis `frontend`:

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
  Once this correction is applied, the deliverable satisfies 100% copy-paste reproducibility and can be fully APPROVED.

---

## 5. Verification Method

To reproduce all findings independently:

1. **Rust Tests in `browser/src-tauri`:**
   ```powershell
   cargo test --lib -- tests::rechte_decken_jeden_befehl
   cargo test --lib -- tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval
   cargo test --lib -- schild::verfolgung::tests
   cargo test --lib -- tabs::ruhe::tests
   cargo test --lib
   ```
2. **Schluesselfach in `crates/schluesselfach`:**
   ```powershell
   cargo test
   git grep "unsafe"
   ```
3. **Frontend in `frontend`:**
   ```powershell
   # Observe the error in documented command:
   npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx
   # Observe the successful run with corrected command:
   npm test -- src/browser/kopf/VertikaleTableiste.test.tsx
   npm test -- src/browser/seite/LesemodusAnsicht.test.tsx
   npm test -- src/browser/kopf/SeitenSicherheitPopover.test.tsx
   npm test -- src/browser/services/tabsStore.test.ts
   npm test -- src/browser
   ```
