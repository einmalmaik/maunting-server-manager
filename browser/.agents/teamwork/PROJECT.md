# Project: Browser Competitiveness & Security Audit (Maunting Pontis)

## Architecture Overview
Maunting Pontis is a desktop and mobile privacy-centric browser built with Tauri v2, Rust (1.88, edition 2021), and Microsoft Edge WebView2 Evergreen on Windows (and Android System WebView on mobile).

Key architectural layers:
- **Host Process (`src-tauri`)**:
  - Pure Safe Rust for business logic, filter routing, tabs state, and `schluesselfach` credential management.
  - Windows COM FFI (`webview2-com`, `windows` crates) to directly embed `ICoreWebView2Controller` into the main window HWND without using Tauri's default WebviewBuilder.
  - Tauri v2 IPC commands (81 total) strictly isolated to the UI `main` webview via `capabilities/oberflaeche.json` and explicit registration in `build.rs` app manifest.
- **Rendering & Tab Processes**:
  - Native WebView2 controllers running in separate AppContainer/Low-Integrity sandboxed processes.
  - Raw Win32 COM embedding ensures untrusted website tabs have zero access to `__TAURI_INTERNALS__`, the `ipc:` protocol, or Tauri plugin scripts.
  - `seite.js` pre-injection script strips `window.chrome.webview` and seals prototype intrinsics before page scripts run.
- **Privacy & Security Shield (`src-tauri/src/schild/`)**:
  - Embedded Brave `adblock` Rust engine (v0.13, MPL-2.0) with dual `RwLock<Engine>` (EasyList + EasyPrivacy, Fanboy Cookie Monster).
  - Pre-network request filtering on `ICoreWebView2_22` catching all request sources (iframes, service workers, shared workers) and returning HTTP 403 / 204.
  - Clean Copy stripping 48 tracking parameters and `utm_*` upon navigation and clipboard copy.
  - 1-click CDP Site Reset (`Storage.clearDataForOrigin` + `Network.deleteCookies`).
  - Antivirus scanning via Windows `IAttachmentExecute::Save` with Mark-of-the-Web zone tracking.
- **Credentials & Biometrics (`crates/schluesselfach/`)**:
  - OS Keyring integration (Windows Credential Manager via DPAPI; Android Keystore with AES-256-GCM).
  - Windows Hello biometric gate (`UserConsentVerifier`) required before secrets are read.
- **Frontend (`browser/frontend`)**:
  - React 18 + TypeScript + Tailwind.
  - Native vertical Tree-Style Tabs (`VertikaleTableiste.tsx`) with hierarchical nesting and collapse.
  - 4-way Snap-Assist Split Screen (`SnapZonenOverlay.tsx`, `SplitTrenner.tsx`) with pixel-perfect dual controller positioning.
  - Purist Reader Mode (`LesemodusAnsicht.tsx`) with Wikipedia reference stripping, 3 link modes, offline MSM note export, and Singra AI integration.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R1.1 Webview Isolation | Separation of `main` UI webview and `tab-*` webviews via native COM & `capabilities/oberflaeche.json` | M1 | Survey R1 |
| 2 | R1.2 Rust Memory Safety | 100% Safe Rust in app logic, 0 unsafe in `schluesselfach`, crash resilience via `absturz.rs` | M1 | Survey R1 |
| 3 | R1.3 Keyring & Biometrics | `schluesselfach` with Windows Hello (`UserConsentVerifier`) & DPAPI / Android Keystore AES-256-GCM | M1 | Survey R1 |
| 4 | R1.4 WebView2 Evergreen | System runtime update model via Microsoft Edge/Windows Update, zero-day patching cadence, process sandboxing | M1 | Survey R1 |
| 5 | R2.1 Ad & Tracker Blocking | Brave `adblock` Rust engine (v0.13), dual engine, `ICoreWebView2_22` pre-network interception | M2 | Survey R2 |
| 6 | R2.2 Deep-Sleep & 0 MB RAM | 3-tier memory management (`LOW` -> `TrySuspend` -> `verwerfen` destroying native controller), workspace discard | M2 | Survey R2 |
| 7 | R2.3 Privacy & Data Flows | Clean Copy (48 params), 1-click CDP site reset, zero telemetry, no user account enforcement | M2 | Survey R2 |
| 8 | R2.4 UX & Productivity | Vertical Tree-Style Tabs, 4-way Snap-Assist Split Screen, Reader Mode with Singra AI & MSM Notes | M2 | Survey R2 |
| 9 | R3.1 Vibecoding Achievements | 10 foundational engineering achievements documented and verified in code | M3 | Survey R3 |
| 10 | R3.2 Trade-offs vs Chrome/Brave | WebView2 dependency, update latency, WebExtension `chrome.tabs` limitations, anti-fingerprinting, DRM, process model | M3 | Survey R3 |
| 11 | R3.3 Prioritized Roadmap | P0 (process boundary, seite.js freeze, DNS rebinding), P1 (extension polyfill, macOS/Linux, CNAME uncloak), P2 (local AI, P2P sync, DRM) | M3 | Survey R3 |
| 12 | Deliverable Report | Compilation of full audit into `AUDIT_REPORT_PONTIS.md` meeting all acceptance criteria | M4 | ORIGINAL_REQUEST |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Survey & Technical Foundation | Comprehensive code survey of R1, R2, R3 by parallel explorers | none | DONE |
| M2 | Report Drafting | Worker compiles comprehensive `AUDIT_REPORT_PONTIS.md` with complete evidence, tables, and roadmap | M1 | DONE |
| M3 | Review, Challenge & Forensic Audit | 2 Reviewers, 2 Challengers, 1 Forensic Integrity Auditor evaluate report | M2 | DONE |
| M4 | Gate & Final Delivery | Record gate verdicts in `GATE_STATUS.md` and handoff to Sentinel | M3 | DONE |

## Code Layout & Artifact Locations
- Audit Report Deliverable: `C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\AUDIT_REPORT_PONTIS.md`
- Original Request: `browser\.agents\teamwork\ORIGINAL_REQUEST.md`
- Project State: `browser\.agents\teamwork\PROJECT.md`
- Orchestrator Directory: `browser\.agents\teamwork\orchestrator_1\`
- Explorer R1 Handoff: `browser\.agents\teamwork\explorer_r1_1\handoff.md`
- Explorer R2 Handoff: `browser\.agents\teamwork\explorer_r2_1\handoff.md`
- Explorer R3 Handoff: `browser\.agents\teamwork\explorer_r3_1\handoff.md`
- Worker Directory: `browser\.agents\teamwork\worker_report_1\`
- Reviewer Directories: `browser\.agents\teamwork\reviewer_1\`, `browser\.agents\teamwork\reviewer_2\`
- Challenger Directories: `browser\.agents\teamwork\challenger_1\`, `browser\.agents\teamwork\challenger_2\`
- Auditor Directory: `browser\.agents\teamwork\auditor_1\`
