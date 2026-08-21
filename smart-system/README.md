# Singra Smart System

Der Desktop-Companion zum MSM-Panel: Konversation, Sprachbedienung und lokale
Assistenz auf dem eigenen Rechner — als schlanke Tauri-v2-App (Rust + React).

## Harte Architektur-Grenze

**Über diese App kann man unter keinen Umständen Server verwalten.** Das ist
keine UI-Entscheidung, sondern eine Scope-Grenze im Backend: der
Smart-System-Zugang stellt der KI keine Server-Werkzeuge zur Verfügung, egal
welche Rechte der eingeloggte Benutzer im Panel hat. Server-Verwaltung bleibt
zu 100 % dem Web-Panel vorbehalten. Details: `docs/smart-system-notes.md`
(lokal) und `docs/agent-rules/`.

## Aufbau

- `src/` — React-Frontend (Vite + Tailwind). Zwei Einstiege über dasselbe
  Bundle: das Hauptfenster (Chat/Einrichtung) und das Overlay
  (`?fenster=overlay`, frameloses Always-on-Top-Fenster für die Sprachblase).
- `src-tauri/` — Rust-Backend: Fensterverwaltung, System-Tray mit
  Statusfarben (Grün: bereit, Blau: hört zu, Lila: denkt, Gelb: spricht),
  globaler Hotkey (`Alt+Space`), Autostart.

## Entwicklung

```bash
npm install
npm run tauri dev
```

Tests: `npm run test` (Frontend) und `cargo test` in `src-tauri/`.

## Verbindung zum Panel

Die App verbindet sich mit einem selbst gewählten MSM-Backend (URL beim
ersten Start). Authentifizierung läuft über `Authorization: Bearer` gegen
dieselben Endpunkte wie das Panel (`/api/auth/login` mit `native_client=true`);
Tokens liegen nie im Klartext auf der Platte.
