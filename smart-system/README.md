# MSS — Maunting Smart System

Der Desktop-Companion zum MSM-Panel: Konversation, Sprachbedienung und lokale Assistenz auf dem eigenen Rechner (als Tauri-v2-App mit Rust-Hülle und Oberfläche aus `frontend/`).

## Eine Oberfläche, zwei Bauten

Seit dem 21.08.2026 gibt es hier **keinen eigenen React-Code mehr**: Die App rendert dieselbe KI-Seite wie der Browser (Chat, Realtime-Modus, Guardian-Fenster, Aufgabenliste, Denkstufen-Wahl), gebaut aus `../frontend/` über den Desktop-Einstieg `frontend/desktop.html` → `frontend/src/desktop/` (`npm run build:desktop`, `vite.desktop.config.ts`, Ergebnis in `frontend/dist-desktop/`). Eine zweite Chat-Implementierung veraltete gegen die erste; deshalb wurde sie entfernt und vereinheitlicht. Die Web-Oberfläche ist die Single Source of Truth.

Der Unterschied zum Browser ist der Transport: Statt Cookies trägt jede Anfrage das Bearer-Token der Kopplung (`frontend/src/desktop/transport.ts` registriert es beim Panel-API-Client), und der Sprach-WebSocket legt es als Subprotokoll `msm.bearer` in den Handshake (nicht in die URL).

## Harte Architektur-Grenze

Die App hat als **Oberfläche** keine Serververwaltung: Nur die KI-Seite und die Desktop-Einstellungen, keine Serverliste, keine Konsole. Die KI darin ist jedoch derselbe Account mit denselben Rechten: Im Gespräch bedient sie Server genauso wie im Panel und erhält die Werkzeuge für den lokalen Rechner zusätzlich.

Die Grenze läuft in die andere Richtung: **Aus dem Panel erreicht kein Werkzeug den lokalen Rechner.** Dies ist eine Schranke im Backend (`services/ai_tool_registry.herkunft_schnitt` plus ein Spiegel je Aufruf), und die Herkunft steht im Token der gekoppelten Sitzung. Der Grund ist praxisnah: Die Übernahme von Maus und Tastatur wird über eine Karte in dieser App bestätigt; aus dem Browser abgeschickt liefe der Aufruf in ein Timeout.

## Anmelden: nur per Kopplung

Die App kennt weder Passwort noch 2FA-Code. Im Panel unter **Profil → KI → Geräte koppeln** entsteht ein Code (zwölf Zeichen, zehn Minuten, genau einmal), der hier eingetragen wird. Nötig ist das, weil `/api/auth/login` bei aktiviertem Captcha einen Turnstile-Token verlangt und Cloudflare-Schlüssel an Domains gebunden sind (`tauri.localhost` lässt sich dort nicht hinterlegen). So bleiben Passwort, 2FA und Captcha vollständig im Browser.

Einzutragen ist die **Adresse der API**, nicht die der Weboberfläche. Das Panel zeigt sie unter Einstellungen → Allgemein und noch einmal beim Koppeln.

## Aufbau

- `src-tauri/`: Die Rust-Basis der App mit Fensterverwaltung (Hauptfenster und Overlay), System-Tray (im Ruhezustand das App-Logo; aktiv: Blau = hört zu, Lila = denkt, Gelb = spricht), zwei konfigurierbare globale Hotkeys (Fenster: `Alt+Space`, Sprachsitzung im Overlay: `Alt+Shift+Space`, beide in den Einstellungen änderbar und einzeln abschaltbar), Autostart, Wake-Word (rustpotter, offline), Audiogeräte-Auswahl (Ein- und Ausgabegerät unabhängig vom Windows-Standard, `audio.rs`), Audio-Ducking (WASAPI), Sandbox-Dateizugriff, lesende Systemsicht (Laufwerke, Ordner, Speicherbelegung; `system.rs` enthält bewusst keinen schreibenden Aufruf; die Schreibgrenze bleibt der Sandbox-Ordner), Übernahme von Maus und Tastatur, Tresor (Anmeldeinformations-Manager). Ein Dialog fragt beim Schließen, ob die App in den Hintergrund wechselt (Standard) oder beendet wird.
- `frontend/src/desktop/` (im Nachbarordner) — der Desktop-Einstieg der
  gemeinsamen Oberfläche: Bootstrap mit Laufzeit-API-Adresse, Splash,
  Einrichtungs-Assistent, Desktop-Einstellungen (Reiter wie im Panel:
  Desktop-Integration, Wake-Word, Audio, Gefahrenzone), Overlay-Sprachblase,
  Auftragsschleife und Übernahmekarte.

## Wake-Word

Das Wake-Word ist **immer der Name des Assistenten** (Profil → KI im Panel,
oder im Chat: „nenn dich …") — ein eigenes Wortfeld gibt es bewusst nicht.
Nach einer Umbenennung schlägt die App einmal je Start die Neukalibrierung
vor; das ist optional, bis dahin hört das Modell auf den alten Namen.

Der Aktiv-Schalter ist persistent (`wakeword_aktiv` in konfig.json): „an"
überlebt den Neustart und startet das Lauschen beim App-Start, „aus" heißt
physisch aus — das Mikrofon ist frei, und kein Pfad schaltet das Lauschen
ohne den Schalter wieder ein. Ein erkanntes Wort öffnet das Overlay direkt
aus Rust (`wakeword.rs` → `sprachsitzung_starten`), über allen Fenstern.

Gegen Fehlgriffe stehen drei Tore hintereinander (`wakeword.rs`): eine
Stimmaktivitätsprüfung (VAD) verwirft Frames ohne Sprache, der Median über
alle Trainingsaufnahmen ersetzt den Ausreißer-anfälligen Bestwert, und eine
Mindestähnlichkeit zur gemittelten Vorlage (`avg_threshold`) filtert bloßes
Ähnlich-Klingen. Die Auslöseschwelle selbst stellt der Benutzer im
Wake-Word-Reiter ein (`wakeword_schwelle`, geklemmt auf 0,30–0,60); daneben
zeigt ein Pegelbalken live, was der Lausch-Thread hört. Ein Fehltrigger
hinterlässt kein offenes Mikrofon: bleibt die Overlay-Sitzung 20 Sekunden
still, schließt sie sich selbst.

## Mikrofon-Verarbeitung

Der Audio-Reiter stellt neben den Geräten auch die Verarbeitung der
Sprachsitzung: Echounterdrückung, Rauschunterdrückung und automatische
Pegelanpassung (Chromiums eingebaute WebRTC-Kette — lokal, nichts geht ins
Netz) sowie eine Software-Eingangsverstärkung (25–400 %). Testhören legt
das eigene Mikrofon mit genau dieser Verarbeitung auf den gewählten
Lautsprecher, mit Pegelbalken — am besten mit Kopfhörern. Das Wake-Word ist
davon unabhängig: seine Rust-Kette normalisiert den Pegel selbst.

## Logos

Alle Logos werden rund dargestellt. Die Dateien selbst bleiben quadratisch —
kreisförmig wird im Splash per CSS (`rounded-full`), beim App-Icon durch eine
Maske beim Erzeugen.

- `src-tauri/icons/` — App-Icon, erzeugt aus `frontend/public/logo.png`
  (Produktlogo, 1254 px). Kein `icon.icns`: die App ist Windows-only, eine
  macOS-Hülle ohne Inhalt braucht kein Icon.
- `frontend/src/desktop/assets/dis-logo.png`, `msm-logo.png` — Stufe 1 und 3
  der Boot-Sequenz.
- `frontend/public/firmen-logo.png` — **fehlt noch.** Ohne die Datei läuft die
  mittlere Stufe ("Ein Produkt von MauntingStudios") ohne Bild, nur mit
  Untertitel. Quadratisches PNG ab 512 px dort ablegen, mehr ist nicht nötig.

## Entwicklung

```bash
npm install
npm run tauri dev
```

`tauri dev` startet den Vite-Dev-Server im Nachbarordner (`npm --prefix
../frontend run dev:desktop`, Port 1430) und zeigt `desktop.html`. **CORS im
Dev-Modus:** das Fenster meldet sich als `http://localhost:1430`, was nicht in
`TAURI_ORIGINS` steht — API-Aufrufe gegen ein fremdes Panel blockt dessen
CORS. Für Betriebstests die gebaute App nehmen (Origin `tauri.localhost`).

Tests: UI-Belange unter `frontend/` (`npx vitest run src/desktop`), Rust unter
`src-tauri/` (`cargo test`).

## Verbindung zum Panel

Die App verbindet sich mit einem selbst gewählten MSM-Backend (API-Adresse beim
ersten Start). Die Sitzung entsteht über `/api/auth/devices/redeem`, danach
läuft alles über `Authorization: Bearer` gegen dieselben Endpunkte wie das
Panel. Das Refresh-Token liegt im Windows-Anmeldeinformations-Manager, das
Access-Token nur im Speicher — nichts davon im Klartext auf der Platte.
