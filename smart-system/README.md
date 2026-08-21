# MSS — Maunting Smart System

Der Desktop-Companion zum MSM-Panel: Konversation, Sprachbedienung und lokale
Assistenz auf dem eigenen Rechner — als schlanke Tauri-v2-App (Rust + React).

## Harte Architektur-Grenze

Die App hat als **Oberfläche** keine Serververwaltung: Dauerchat und
Sprachbedienung, keine Serverliste, keine Konsole, keine Dateiverwaltung. Die
KI darin ist aber derselbe Account mit denselben Rechten — im Gespräch bedient
sie Server genauso wie im Panel und bekommt die Werkzeuge für diesen Rechner
zusätzlich.

Die Grenze läuft in die andere Richtung: **aus dem Panel erreicht kein Werkzeug
den Rechner.** Sie ist keine UI-Entscheidung, sondern eine Schranke im Backend
(`services/ai_tool_registry.herkunft_schnitt` plus ein Spiegel je Aufruf), und
die Herkunft steht im Token der gekoppelten Sitzung — kein Client kann sie
behaupten. Der Grund ist praktisch: die Übernahme von Maus und Tastatur wird an
einer Karte in dieser App bestätigt, und aus dem Browser abgeschickt liefe der
Aufruf in eine Frist statt in eine Antwort.

Hier stand bis zum 21.08.2026 das Gegenteil („unter keinen Umständen Server
verwalten"). Das war eine Fehllesung eines älteren Beschlusses; gemeint war die
Oberfläche, nicht die Rechte der KI.

## Anmelden: nur per Kopplung

Die App kennt weder Passwort noch 2FA-Code. Im Panel unter **Profil → KI →
Geräte koppeln** entsteht ein Code (zwölf Zeichen, zehn Minuten, genau einmal),
der hier eingetragen wird. Nötig ist das, weil `/api/auth/login` bei
aktiviertem Captcha einen Turnstile-Token verlangt und Cloudflare-Schlüssel an
Domains hängen — `tauri.localhost` lässt sich dort nicht hinterlegen. So bleiben
Passwort, 2FA und Captcha vollständig im Browser.

Einzutragen ist die **Adresse der API**, nicht die der Weboberfläche. Das Panel
zeigt sie unter Einstellungen → Allgemein und noch einmal beim Koppeln.

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

Die App verbindet sich mit einem selbst gewählten MSM-Backend (API-Adresse beim
ersten Start). Die Sitzung entsteht über `/api/auth/devices/redeem`, danach
läuft alles über `Authorization: Bearer` gegen dieselben Endpunkte wie das
Panel. Das Refresh-Token liegt im Windows-Anmeldeinformations-Manager, das
Access-Token nur im Speicher — nichts davon im Klartext auf der Platte.
