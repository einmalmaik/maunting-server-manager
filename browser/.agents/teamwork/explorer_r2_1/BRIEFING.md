# BRIEFING — 2026-10-17T19:35:00Z

## Mission
Deep evidence-based code investigation of Requirement R2 (Wettbewerbsvergleich mit Chrome und Brave in den Kernbereichen) for Maunting Pontis.

## 🔒 My Identity
- Archetype: explorer
- Roles: investigator, analyzer, synthesizer
- Working directory: C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\.agents\teamwork\explorer_r2_1
- Original parent: 3c13e721-b607-4418-8753-a112abdd41be
- Milestone: Requirement R2 Deep Dive

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Evidence-based findings with exact file paths, line numbers, function names, and code snippets
- No marketing buzzwords or unsubstantiated superlatives; factual and technical accuracy
- Deliver complete handoff.md in working directory and notify orchestrator

## Current Parent
- Conversation ID: 3c13e721-b607-4418-8753-a112abdd41be
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `src-tauri/Cargo.toml`
  - `src-tauri/src/schild/` (`mod.rs`, `listen.rs`, `verfolgung.rs`, `sperre.rs`)
  - `src-tauri/src/tabs/` (`mod.rs`, `desktop.rs`, `desktop/webview2.rs`, `desktop/schlaf.rs`, `desktop/ohne_edge.rs`, `ruhe.rs`, `weg.rs`)
  - `frontend/src/browser/services/` (`tab.ts`, `tabsStore.ts`, `workspacesStore.ts`, `linkReiniger.ts`, `lesemodusService.ts`, `lesemodusStore.ts`)
  - `frontend/src/browser/seite/` (`Seitenflaeche.tsx`, `SnapZonenOverlay.tsx`, `SplitTrenner.tsx`, `LesemodusAnsicht.tsx`)
  - `frontend/src/browser/kopf/` (`VertikaleTableiste.tsx`, `LesemodusKnopf.tsx`, `SeitenSicherheitPopover.tsx`)
  - Unit tests across Rust and React/Vitest
- **Key findings**:
  1. Ad/Tracker blocking: Native Brave `adblock = "0.13"` in Rust with dual `werbung`/`tracker` engines. All requests intercepted via `ICoreWebView2_22::AddWebResourceRequestedFilterWithRequestSourceKinds("*", ALL, ALL)` returning HTTP 204 (ping) or 403.
  2. 3-tier resource management: Low memory target level, `TrySuspend` freezing (30 min idle), and full controller destruction (`verwerfen`) releasing 100% webview RAM (0 MB RAM) on memory saver (60 min) or workspace switch.
  3. Privacy: Two-layer Clean Copy (Rust navigation redirect in `tabs/weg.rs` & frontend clipboard in `linkReiniger.ts`), 1-click site reset via CDP `Storage.clearDataForOrigin` & `Network.deleteCookies`, zero telemetry with disabled Edge features.
  4. UX/Productivity: Recursive tree-style tabs with D&D/folding; 4-way snap-assist split screen with dual WebView2 `SetBounds` resizing; reader mode with CDP DOM extraction, MSM encrypted note export, and Singra AI draft prompt transfer.
- **Unexplored areas**: None for R2.

## Key Decisions Made
- All four core requirements of R2 verified with exact file paths, line numbers, and architectural comparisons to Chrome and Brave.

## Artifact Index
- DISPATCH.md — Initial dispatch instructions
- BRIEFING.md — Persistent state and findings index
- progress.md — Heartbeat and progress tracking
- handoff.md — Complete 5-component handoff report
