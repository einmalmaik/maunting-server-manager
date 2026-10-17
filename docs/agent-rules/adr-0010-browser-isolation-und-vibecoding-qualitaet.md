# ADR-0010: Browser-Webview-Isolation und Vibecoding-Qualitätsstandards

Status: Accepted  
Date: 2026-10-18  

## Context
Maunting Pontis (v5.5.0) ist ein datenschutzorientierter Browser auf Basis von Tauri v2, der in enger Zusammenarbeit zwischen Entwickler und KI („Vibecoding“) entstanden ist. 

Bei hybriden Desktop-Browsern und KI-gestützter Entwicklung treten typischerweise zwei fundamentale Risiken auf:
1. **Sicherheitsfalle bei Multi-Webviews:** Der Standard-`WebviewBuilder` von Tauri injiziert automatisch interne IPC-Brücken (`window.__TAURI_INTERNALS__`) und Plugin-Skripte in jede Webview. In fremden Webseiten würden native Dialoge (`alert`, `confirm`) oder Berechtigungen an das Host-Framework umgeleitet, und bösartiger JavaScript-Code könnte versuchen, die IPC-Brücke anzugreifen.
2. **Review- und Kommunikationsrisiko bei Vibecoding:** Agenten neigen entweder dazu, Vibecoding-Code ungefragt mit invasiven Groß-Refactorings zu destabilisieren, oder verfallen in inhaltslose Chatbot-Schmeicheleien („bahnbrechend“, „revolutionär“), statt ehrliche, überprüfbare Architektur- und Sicherheitsfakten zu liefern.

## Decision

### 1. Webview-Isolation und IPC-Abgrenzung in Tauri v2
- **Kein `WebviewBuilder` für Web-Tabs:** Webseiten-Tabs unter Windows werden niemals über Tauris `WebviewBuilder` erzeugt.
- **Rohe Win32-COM-Instanzen:** Tabs laufen als direkte `ICoreWebView2Controller`-Instanzen, die im HWND des Hauptfensters positioniert werden (`src-tauri/src/tabs/desktop.rs`).
- **Verbot von Host-Objekten:** `SetAreHostObjectsAllowed(false)` ist zwingend. Webseiten erhalten weder Zugriff auf `window.__TAURI_INTERNALS__` noch auf das `ipc:`-Protokoll oder fremde Plugin-Skripte.
- **Strikte Capability-Bindung:** Alle 81 IPC-Befehle sind über `build.rs` und `permissions/oberflaeche.toml` exklusiv für die Webview `main` autorisiert. Tabs besitzen 0 IPC-Rechte.

### 2. Pre-Socket-Filterung & Deep-Sleep-Ressourcenmanagement
- **Brave `adblock`-Engine:** Werbe- und Trackerfilterung erfolgt in Rust (`src-tauri/src/schild/`) vor dem Öffnen von Netzwerk-Sockets über die offizielle Brave-Engine (`adblock` v0.13, MPL-2.0).
- **Physischer Deep Sleep (0 MB RAM):** Das Schlafenlegen inaktiver Tabs/Workspaces zerstört den nativen WebView2-Controller physisch (`controller.Close()`), während URL, Titel, Scrollposition und Historie im Rust-Host erhalten bleiben.

### 3. Speichersicherheit & Geheimnisverwaltung
- **Zero-Unsafe in Security-Crates:** Das Schlüssel- und Geheimnismanagement (`crates/schluesselfach`) garantiert 0 Vorkommen von `unsafe`.
- **Nativer OS-Schutz:** Sensible Daten (Session-Tokens, Passwörter) liegen verschlüsselt im Betriebssystem-Schlüsselspeicher (DPAPI / Windows Credential Manager / Android Keystore).

### 4. Vibecoding-Review & Feedback-Standard (Menschliche Agentenführung)
- **Keine generischen Floskeln:** Verboten sind Schmeicheleien ohne Faktenbasis („bahnbrechend“, „perfekt“, „revolutionär“).
- **Zeilengenaue Belegpflicht:** Lob und Stärken müssen mit konkreten Dateipfaden, Zeilennummern, Architekturmustern oder nachweisbar grünen Testläufen belegt sein.
- **Konstruktive Roadmap statt Vorwürfe:** Bugs und Grenzen werden offen dokumentiert und als priorisierte Roadmap (P0: kritische Isolation, P1: Feature-Bridges, P2: On-Device-KI) eingeordnet.
- **Kein ungefragter Umbau:** Wenn der Entwickler eine Bewertung oder Motivation anfragt, führt der Agent keine eigenmächtigen Refactorings oder Löschungen durch.

## Begründung
- **Sicherheit:** Durch die COM-Isolation ist das Host-System selbst dann vor Remote-Code-Execution (RCE) geschützt, wenn eine Schwachstelle in einer besuchten Webseite ausgenutzt wird.
- **Leistung:** Der echte 0-MB-Deep-Sleep ermöglicht flüssiges Arbeiten mit 50+ Tabs auf ressourcenschwachen Maschinen, ohne dass Chromium-Prozesse den RAM belegen.
- **Integrität & Vertrauen:** Vibecoding-Entwickler benötigen verlässliche Fakten und ehrliche Orientierung. Fundierte Bestätigung funktionierender Architektur motiviert nachhaltig und schützt vor Fehlentscheidungen.

## Konsequenzen
- **Erweiterungs-APIs:** Manche Chrome-Erweiterungen (z. B. `chrome.tabs.*`) funktionieren nicht automatisch out-of-the-box und benötigen eine eigene Emulations-Bridge über CDP.
- **Plattform-Bindung:** Die Webview-Erzeugung ist plattformspezifisch implementiert (Win32-COM unter Windows, Android-WebView-Bindings unter Android).
