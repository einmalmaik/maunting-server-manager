# MSS — Maunting Smart System

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

## Logos

Alle Logos werden rund dargestellt. Die Dateien selbst bleiben quadratisch —
kreisförmig wird im Splash per CSS (`rounded-full`), beim App-Icon durch eine
Maske beim Erzeugen.

- `src-tauri/icons/` — App-Icon, erzeugt aus `frontend/public/logo.png`
  (Produktlogo, 1254 px). Kein `icon.icns`: die App ist Windows-only, eine
  macOS-Hülle ohne Inhalt braucht kein Icon.
- `public/dis-logo.png`, `public/msm-logo.png` — Stufe 1 und 3 der
  Boot-Sequenz.
- `public/firmen-logo.png` — **fehlt noch.** Ohne die Datei läuft die
  mittlere Stufe ("Ein Produkt von MauntingStudios") ohne Bild, nur mit
  Untertitel. Quadratisches PNG ab 512 px dort ablegen, mehr ist nicht nötig.

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
