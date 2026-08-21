# Self-Hosting, Deployment-Artefakte und Nodes

Diese Seite ist die kanonische Betriebsdokumentation für die Installation der
MSM-Komponenten. Das Repository ist ein Monorepo, die ausgelieferten Komponenten
sind trotzdem getrennte Deployment-Einheiten.

## Welche Komponente läuft wo?

Eine normale Installation verwendet einen Control-Plane-Server und beliebig
viele Nodes:

| Rolle | Enthält | Benötigt das vollständige Repository? |
| --- | --- | --- |
| Panel/Control Plane | Backend, DIS-Sidecar, Panel-PostgreSQL, Frontend und lokaler Agent | Nein, das Panel-Release enthält nur die benötigten Projektteile. |
| Separates Frontend | Fertig gebautes statisches Vite-Bundle | Nein, `msm-frontend-<VERSION>.tar.gz` genügt. |
| Remote-Node | Agent, Rootless Docker, TLS und node-eigene Serverdaten | Nein, der Node lädt sein Agent-Paket direkt vom Panel. |

Das Monorepo ist die gemeinsame Quelle für Entwicklung und Releases. Es ist
kein Zwang, jede Komponente per `git clone` auszuliefern.

## Empfohlene Panel-Installation

Eine frische Produktionsinstallation benötigt eine HTTPS-Domain, deren DNS
bereits auf den Panel-Server zeigt:

```bash
curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh \
  | sudo bash -s -- --domain panel.example.com
```

Der Bootstrap lädt bevorzugt das getestete `msm-panel-<VERSION>.tar.gz` des
neuesten GitHub-Releases. Nur wenn noch kein passendes Release existiert, nutzt
er als Kompatibilitätsfallback einen flachen Git-Checkout. Anschließend ruft er
`install.sh --simple` auf. Vor dem Entpacken wird das Archiv zwingend gegen den
mitgelieferten Eintrag in `SHA256SUMS` geprüft.

`install.sh` bleibt die zentrale Installationslogik. Der einfache Bootstrap-
Modus richtet PostgreSQL, Redis, Rootless Docker, DIS, lokalen Agent, Caddy und
systemd automatisch ein. E-Mail wird anschließend im Browser-Setup konfiguriert.
Wer bewusst den interaktiven Expertenweg benötigt, kann das Repository oder
Panel-Artefakt entpacken und `sudo bash install.sh` ausführen.

Alle drei Einstiege – direkter interaktiver Aufruf, `--simple` und öffentlicher
Bootstrap – durchlaufen dieselbe idempotente Systemvorbereitung. Sie installiert
die benötigten Basispakete auch auf einem minimalen Ubuntu-/Debian-System,
repariert eine unvollständige Caddy-Paketquelle sicher und erhält vorhandene
Caddy-Konfigurationen. Der Installer aktualisiert die von MSM benötigten Pakete,
führt aber bewusst kein pauschales Betriebssystem-`dist-upgrade` und keinen
automatischen Reboot fremder Systeme durch.

### Abgebrochene Erstinstallation fortsetzen

Hat ein abgebrochener Lauf bereits die lokale PostgreSQL-Rolle und Datenbank
`msm`, aber noch keine `backend/.env` angelegt, bleibt der normale Installer aus
Sicherheitsgründen stehen. Nach Prüfung des Zustands kann der Lauf ohne Löschen
der Datenbank ausdrücklich fortgesetzt werden:

```bash
sudo bash install.sh --simple --domain panel.example.com --resume-partial
```

Der Resume-Pfad akzeptiert ausschließlich die Datenbank `msm` mit Eigentümer
`msm`, eine unprivilegierte Rolle ohne Mitgliedschaften und ohne weitere eigene
Datenbanken. Er erzeugt ein neues Passwort, überträgt es ausschließlich über
`stdin` an PostgreSQL und schreibt es anschließend in die geschützte `.env`.
Abweichende oder fremde PostgreSQL-Zustände werden nicht verändert.

PostgreSQL ist die einzige unterstützte Panel-Runtime-Datenbank. SQLite-Code im
Installer dient ausschließlich dazu, bestehende Altinstallationen einmalig und
geprüft nach PostgreSQL zu migrieren; neue SQLite-Installationen gibt es nicht.

## Getrennte GitHub-Release-Artefakte

Der Workflow `.github/workflows/release-artifacts.yml` erzeugt für `v*`-Tags:

- `msm-panel-<VERSION>.tar.gz`: installierbare Control Plane inklusive Backend,
  DIS, Frontend, Agent-Paket und Installationsskripten.
- `msm-frontend-<VERSION>.tar.gz`: ausschließlich das gebaute `frontend/dist`
  plus öffentliche `.env.example`.
- `msm-agent-<VERSION>.tar.gz`: Agent-Quellen und Agent-Installer für
  kontrollierte Offline-/Automationsfälle.
- `SHA256SUMS`: Prüfsummen aller drei Archive.

Bei einem Tag werden die Dateien an einen zunächst als Entwurf angelegten
GitHub-Release gehängt. Ein manueller Workflow-Lauf stellt sie 30 Tage als
GitHub-Actions-Artefakt bereit.

### Separates Frontend

Für ein getrenntes Frontend wird nur das Frontend-Archiv benötigt:

```bash
VERSION=v1.8.0
curl -fLO "https://github.com/einmalmaik/maunting-server-manager/releases/download/${VERSION}/msm-frontend-${VERSION}.tar.gz"
tar -xzf "msm-frontend-${VERSION}.tar.gz"
```

Der Inhalt unter `dist/` wird von einem statischen Webserver oder Hostingdienst
ausgeliefert. `VITE_API_URL` und optional `VITE_WS_URL` sind Build-Werte und
müssen deshalb bereits beim Workflow-/Frontend-Build korrekt gesetzt sein. Ein
manueller Workflow-Lauf bietet dafür die Eingaben `vite_api_url` und
`vite_ws_url`; Tag-Releases ohne diese Werte verwenden relative URLs für ein
Frontend am gleichen Origin wie das Backend. In `VITE_*` dürfen niemals Secrets
stehen. Das Backend benötigt für getrenntes Hosting passende CORS- und
Cookie-Einstellungen; Details stehen in `frontend/.env.example` und
`backend/.env.example`.

### MSS — Maunting Smart System (Desktop-App, optional)

Der Workflow `.github/workflows/smart-system-release.yml` baut den
Windows-Installer der Desktop-App. Er läuft auf **`smart-v*`-Tags** und
bewusst nicht auf `v*`: auf `v*` liegen bereits zwei Workflows, die je einen
Draft-Release zum selben Tag anlegen, und die Desktop-App folgt ohnehin einem
eigenen Takt — ein Panel-Update erzwingt keinen neuen Installer.

- `Maunting Smart System_<VERSION>_x64-setup.exe`: NSIS-Installer, nur Windows.
  Die App ist Windows-spezifisch (Audio-Ducking über WASAPI, Übernahme von
  Maus und Tastatur, Anmeldeinformations-Manager als Tresor).
- `SHA256SUMS.txt`: Prüfsumme des Installers, als Actions-Artefakt.

**Der Installer ist nicht signiert.** Windows SmartScreen meldet deshalb einen
unbekannten Herausgeber; über „Weitere Informationen“ lässt er sich starten.
Die Prüfsumme aus dem Release ist der Ersatz für die fehlende Signatur — sie
gehört vor der Installation verglichen.

Was die App zum Betrieb braucht:

- **Die Adresse der API**, nicht die der Weboberfläche. Bei einer
  Standardinstallation ist das dieselbe; liegt das Frontend getrennt (eigener
  Hoster, `VITE_API_URL` gesetzt), ist es der Wert aus `VITE_API_URL`. Das
  Panel zeigt ihn unter **Einstellungen → Allgemein** und noch einmal direkt
  beim Koppeln, jeweils mit Kopierknopf. Trägt man die Oberfläche ein,
  antwortet sie mit einer Webseite statt mit Daten, und die App sagt das auch
  so.
- Einen Benutzer mit `ai.chat.use`.
- Für die Werkzeuge auf dem eigenen Rechner zusätzlich `ai.desktop.use`. Ohne
  dieses Recht ist die App ein Chatfenster: sie holt keine Aufträge ab, und
  die KI bekommt die Desktop-Werkzeuge gar nicht erst angeboten.
- Panelseitig müssen `tauri://localhost`, `http://tauri.localhost` und
  `https://tauri.localhost` als Origins erlaubt sein. Das ist in
  `backend/config.py` fest hinterlegt (`TAURI_ORIGINS`) und braucht keine
  Konfiguration. **Nur im Entwicklungsmodus** (`npm run tauri dev`) lädt das
  Fenster vom Vite-Server und meldet sich mit `http://localhost:1430`; das ist
  nicht hinterlegt und muss bei Bedarf über `MSM_CORS_ALLOWED_ORIGINS` dazu.

**Anmelden geht nur über Kopplung.** Die App kennt weder Passwort noch
2FA-Code. Wer angemeldet ist, öffnet im Panel **Profil → KI → Geräte koppeln**,
erzeugt dort einen Code (zwölf Zeichen, zehn Minuten, genau einmal einlösbar)
und trägt ihn in der App ein. Der Grund ist nicht Bequemlichkeit: bei
aktiviertem Captcha verlangt `/api/auth/login` einen Turnstile-Token, und ein
Captcha-Widget in einem Tauri-Fenster scheitert daran, dass Cloudflare-Schlüssel
an Domains gebunden sind. Passwort, 2FA und Captcha bleiben damit vollständig
im Browser.

In der Datenbank steht nur der SHA-256 des Codes. Gekoppelte Geräte stehen
unter demselben Punkt im Profil; **Zugang entziehen** widerruft die
Refresh-Familie genau dieses Geräts und lässt alle anderen Sitzungen laufen.
Das gerade gültige Access-Token bleibt bis zu seinem Ablauf brauchbar —
dieselbe Regel wie überall sonst, ein Widerruf wirkt spätestens beim nächsten
Erneuern.

**Die Oberfläche der App ist die des Panels.** Seit dem 21.08.2026 gibt es
keine zweite Chat-Implementierung mehr: die App rendert dieselbe KI-Seite wie
der Browser (Chat, Realtime-Modus, Guardian-Fenster, Aufgabenliste,
Denkstufen-Wahl), gebaut aus `frontend/` über einen eigenen Einstieg
(`frontend/desktop.html`, `npm run build:desktop`,
`frontend/vite.desktop.config.ts`). In `smart-system/` liegt nur noch die
Tauri-Hülle (Rust + Konfiguration). Der Unterschied zum Browser ist der
Transport: statt Cookies trägt jede Anfrage das Bearer-Token der Kopplung,
und der Sprach-WebSocket legt es als Subprotokoll (`msm.bearer`) in den
Handshake — nie in die URL. Ob das Backend diesen Weg kennt, meldet
`GET /api/ai/voice/config` als `bearer_ws: true`; scheitert der Handshake,
fragt die App dort nach und zeigt auf einem älteren Backend „Das Panel ist
zu alt für den Sprachmodus der App" statt eines nichtssagenden
Verbindungsfehlers. Die Abhilfe ist dann schlicht: Panel aktualisieren.

Was die App **als Oberfläche** nicht hat: eine Serververwaltung. Sie zeigt die
KI-Seite und die Desktop-Einstellungen, keine Serverliste und keine Konsole.
Die KI darin ist aber derselbe Account mit denselben Rechten — sie kann Server
also sehr wohl bedienen, im Gespräch, und bekommt die Werkzeuge für den eigenen
Rechner zusätzlich. Umgekehrt gilt die Grenze: **aus dem Browser erreicht kein
Werkzeug den Rechner.** Der Katalog wird nach der Herkunft geschnitten, und die
Ausführung weist jeden solchen Aufruf zusätzlich einzeln ab
(`services/ai_tool_registry.herkunft_schnitt`). Die Herkunft steht im Token der
gekoppelten Sitzung — ein Client kann sie nicht behaupten. Auch die
Sprachläufe der App tragen sie: der Voice-WebSocket liest den `geraet`-Anspruch
aus demselben Token, mit dem er authentifiziert.

Deinstalliert wird in der App selbst (Einstellungen → Gefahrenzone) oder über
Windows. Der Weg in der App räumt zusätzlich auf, was der Windows-Uninstaller
stehen lässt: Einstellungen, die Stimmaufnahmen des Wake-Words, den Eintrag im
Anmeldeinformations-Manager und den Autostart. Der Sandbox-Ordner bleibt — er
gehört dem Benutzer.

## Was die Oberfläche im Browser nachlädt

**Keine Schriften von Dritten.** Die vier Familien der Oberfläche — Inter,
Manrope, IBM Plex Sans und JetBrains Mono — liegen als `woff2` im gebauten
`dist/assets/` und kommen vom selben Origin wie das Panel. Es geht keine
Anfrage an `fonts.googleapis.com` oder `fonts.gstatic.com`, weder beim ersten
Aufruf noch später.

Das ist eine Datenschutzentscheidung und keine Optimierung. Eine zur Laufzeit
eingebundene Google-Schrift überträgt die IP-Adresse jedes Besuchers an einen
Dritten, bevor die erste Zeile Oberfläche steht — das LG München I hat darin am
20.01.2022 (Az. 3 O 17493/20) eine Verletzung des Persönlichkeitsrechts
gesehen. Dieses Risiko hätte nicht der Betreiber gewählt, sondern das Panel für
ihn. Also trägt das Panel es nicht ein. Der Nebeneffekt ist die
Offlinefähigkeit: ein Panel im abgeschotteten Netz sieht aus wie eines mit
Internetzugang, statt auf `system-ui` zurückzufallen.

Die Kosten: 63 Dateien, rund 660 KB im Build. Ein Aufruf zieht davon nur, was
`unicode-range` verlangt — eine deutschsprachige Oberfläche lädt zehn Dateien
mit zusammen etwa 200 KB, eine russische zusätzlich die kyrillischen Schnitte.
Die Caddy-Site liefert `/assets/*` mit `max-age=31536000, immutable` aus; ein
Besucher lädt sie damit einmal pro Release, nicht einmal pro Seitenaufruf.

Prüfen lässt sich das am gebauten Frontend. Die Ausgabe muss leer bleiben:

```bash
grep -rl "fonts.googleapis.com\|fonts.gstatic.com" frontend/dist/
```

Was das **nicht** heißt: der Browser spricht deshalb mit niemandem sonst. Drei
Ausnahmen bleiben, und alle drei sind sichtbar:

- Das **Support-Widget** (Crisp, Tawk.to oder Singra) lädt ein fremdes Skript,
  sobald ein Betreiber es unter *Einstellungen → Support-Widget* einschaltet.
  Ohne diese Einstellung wird nichts geladen; die erlaubten Herkünfte stehen im
  Code und in der CSP, nicht in einem Eingabefeld.
- Die **Versionsanzeige** fragt `api.github.com` nach dem neuesten Release —
  aus dem Browser, ohne Einstellung und auch auf der Loginseite, also bevor
  jemand angemeldet ist. Wer das nicht möchte, gehört mit dieser Information in
  die eigene Datenschutzerklärung.
- **KI und Sprachmodus** reden mit OpenRouter, OpenAI, ElevenLabs oder Azure,
  sobald ein Betreiber Zugänge hinterlegt. Bei Azure ist die Gegenstelle
  `https://<deine-ressource>.services.ai.azure.com` — der einzige Fall, in dem
  ein Stück der Zieladresse aus einer Einstellung kommt und nicht aus dem Code.
  Was dabei übertragen wird, steht unter *Was an den Anbieter geht* weiter
  unten.

## Bestehende All-in-one-Installation aufteilen

Für eine bereits installierte MSM-Instanz ist der interaktive Assistent der
Standardweg:

```bash
sudo /opt/msm/helper-scripts/migrate-panel-components.sh
```

Er fragt unabhängig voneinander nach drei Aktionen und führt sie anschließend
in einer sicheren Reihenfolge aus:

1. **Externes Frontend verbinden:** Das statische Frontend muss beim Anbieter
   bereits mit `VITE_API_URL=https://api.example.com` gebaut und über eine
   HTTPS-Origin erreichbar sein. Der Assistent prüft die Origin, stellt Backend,
   CORS, Cross-Site-Cookies und die API-only-Caddy-Site ein und prüft den
   Preflight. Er lädt selbst nichts zu Vercel, Wurzel oder einem anderen
   Hostinganbieter hoch, weil dafür anbieterspezifische Zugangsdaten nötig wären.
2. **Ausgewählte Gameserver verschieben:** Eine komma-separierte Liste wie
   `1,2,3,8` wird nacheinander auf einen bereits registrierten Zielnode kopiert.
   Jeder Server muss gestoppt sein. Das vollständige Serververzeichnis enthält
   Saves, Konfiguration, Mods, Workshop-Dateien, Blueprint-erzeugte Laufzeitdaten
   und Backups. Zugeordnete node-eigene PostgreSQL-Datenbanken werden als Dumps
   mitgesichert und auf dem Ziel wiederhergestellt. Die DB-Zuordnung wechselt
   erst nach erfolgreicher Zielprüfung; Quelldaten werden nicht automatisch
   gelöscht.
3. **Backend/Control Plane verschieben:** Das Ziel muss ein frischer
   Ubuntu-/Debian-Server sein, erreichbar per Root-SSH oder über einen Benutzer
   mit passwortlosem `sudo`. Der Assistent installiert zuerst parallel eine
   Backend-only-Control-Plane. Befindet sich auf der Quelle noch der lokale
   Agent, wird er über das normale, vom Owner zu bestätigende TLS-Enrollment in
   einen eigenständigen Node umgewandelt. Die bestätigte Node-ID übernimmt der
   Assistent direkt aus der geschützten Enrollment-Antwort; sie muss nicht
   abgelesen oder erneut eingegeben werden. Eine kurzlebige Challenge pro
   Serververzeichnis beweist, dass der neue Agent exakt dieselben Daten und den
   erwarteten Rootless-Docker-Runtime sieht; erst dann wird die Node-Zuordnung
   atomar geändert.

Beim finalen Backend-Cutover stoppt nur die alte Control Plane kurz. Danach
werden ein konsistenter PostgreSQL-Dump, die Backend-Konfiguration, dieselben
DIS-/Anwendungs-Secrets, Panel-Backups, Community-Blueprints und der
Setup-Zustand übertragen. Das Ziel behält sein frisch generiertes lokales
PostgreSQL-Passwort; Datenbank-URLs der Quelle werden nicht übernommen. Nach
Restore, DIS-, Backend-, Caddy- und Health-Prüfung bleibt die alte Control Plane
deaktiviert, während ihr eigenständiger Agent weiterläuft. Die Quelle wird nicht
gelöscht und kann bewusst als Rollback-Basis erhalten bleiben.

Vor dem Commit des Zielzustands startet jeder Fehler die alte Control Plane
wieder. Nach erfolgreicher lokaler Zielprüfung gibt es absichtlich keinen
automatischen Split-Brain-Fallback mehr: Es darf nie gleichzeitig auf zwei
Panel-Datenbanken geschrieben werden. Falls die öffentliche Health-Prüfung noch
nicht grün ist, bleibt das Ziel maßgeblich und der Assistent fordert zur Prüfung
von DNS und Cloud-Firewall auf.

### Voraussetzungen und unvermeidbare externe Schritte

- Die Quellinstallation läuft und verwendet PostgreSQL.
- Backend-Ziel: frisches unterstütztes Linux, mindestens die vom Preflight
  berechnete freie Kapazität, SSH-Host-Key-Prüfung und Root beziehungsweise
  passwortloses `sudo`.
- Gameserver-Ziel: bereits im Panel registrierter, online geprüfter TLS-Node mit
  genügend Speicher und freien Zielports.
- Das Frontend muss bereits beim gewählten Hostinganbieter gebaut sein. In
  `VITE_*` stehen ausschließlich öffentliche Origins, niemals Secrets.
- Den DNS-A/AAAA-Eintrag der API-Domain muss der Betreiber beim DNS-Anbieter auf
  den Zielserver setzen. Ohne Zugang zu diesem externen Anbieter darf und kann
  MSM diesen Schritt nicht vortäuschen.
- Die einmalige Owner-Freigabe eines neuen Agents bleibt eine bewusste
  Sicherheitsgrenze und wird auch mit `--yes` nicht umgangen.

Eine reine Vorprüfung ohne Änderungen ist möglich. Sie prüft die lokale
Installation und – passend zur Auswahl – Frontend-Erreichbarkeit,
Gameserver-Zustände oder die SSH-Erreichbarkeit der Ziel-Control-Plane. Sie
erstellt keine Archive, stoppt keine Dienste und verändert keine Daten:

```bash
sudo /opt/msm/helper-scripts/migrate-panel-components.sh \
  --migrate-backend \
  --backend-target root@203.0.113.10 \
  --api-domain api.example.com \
  --dry-run
```

Alle Automationsoptionen zeigt
`sudo /opt/msm/helper-scripts/migrate-panel-components.sh --help`. SSH-Passwörter,
Private Keys, Datenbankpasswörter, Agent-Tokens und DIS-Secrets werden weder als
Argumente angenommen noch ausgegeben. Temporäre Klartext-Konfigurationen und
Dumps liegen ausschließlich in Verzeichnissen mit Modus `0700`, Dateien mit
`0600`, und werden nach dem Ziel-Cutover entfernt.

## Einen neuen Node verbinden

Der Standardweg benötigt weder einen Repository-Clone noch manuelles Kopieren
von Agent-Token oder TLS-Fingerprint:

1. Als Owner im Panel **Nodes** öffnen und **Node hinzufügen** wählen.
2. Den angezeigten secret-freien Installationsbefehl kopieren.
3. Den Befehl einmal als Root auf dem neuen Ubuntu-/Debian-Node ausführen.
4. Der Installer lädt das zur Panel-Version gehörende Agent-Paket direkt vom
   Panel, installiert Rootless Docker, erzeugt Agent-Token und TLS-Zertifikat
   lokal und startet den systemd-Service.
5. Der Node sendet Token und Zertifikatsfingerprint über HTTPS an das Panel.
   Das Panel speichert den Token DIS-verschlüsselt; UI, URL und Logs zeigen ihn
   nicht an.
6. Im Panel den angezeigten kurzen Code vergleichen und die Anfrage einmalig
   bestätigen. Erst danach prüft das Panel Token, TLS-Pin und Erreichbarkeit und
   schaltet den Node frei.

Enrollment-Anfragen laufen nach 15 Minuten ab und sind rate-limited. Der
Installationsbefehl enthält kein wiederverwendbares Agent-Token. Eine bereits
registrierte Node kann über den öffentlichen Enrollment-Endpunkt niemals
überschrieben werden; eine erneute Vertrauensfreigabe bleibt eine bewusste
Owner-Aktion. Der manuelle Dialog für Host, Token und Fingerprint bleibt nur als Fallback für
bereits separat installierte oder speziell angebundene Agents bestehen. Bei
einer manuellen Agent-Installation liegt das einmal zu übernehmende Token in
`/root/msm-agent-token` mit Modus `0600`; nach dem Eintragen im Panel muss diese
Übergabedatei gelöscht werden. Im normalen Enrollment-Flow wird sie nicht
angelegt.

## Beispiel mit 20 Hosts

- Host 1: Panel/Control Plane, optional einschließlich Frontend.
- Host 2 bis 20: jeweils nur ein Remote-Agent; kein Git-Checkout.

Wenn Frontend und Backend bewusst getrennt werden, kann Host 1 das statische
Frontend, Host 2 die Control Plane und Host 3 bis 20 die Nodes betreiben. Für
größere Mengen wird derselbe secret-freie Node-Befehl über Cloud-Init, Ansible
oder eine Provider-Automation ausgeführt. Die Owner-Bestätigung bleibt eine
bewusste Sicherheitsgrenze.

## Dateien und Konfiguration

- Panel: `backend/.env.example`
- Frontend: `frontend/.env.example`
- Agent/Node: `msm-agent/.env.example`
- DIS-Sidecar: `dis-sidecar/.env.example`

Jede Vorlage erklärt Status, Zweck, Herkunft und Format aller Betreiberwerte.
Automatisch erzeugte `.env`-Dateien dürfen niemals committed werden.

### Persistenter Guardian-State des Agents

Jeder Agent hält seinen node-lokalen Guardian-Betriebszustand standardmäßig
unter `/var/lib/msm-agent/guardian`. systemd legt den Elternpfad über
`StateDirectory=msm-agent` an; er gehört dem Agent-Service und darf weder vom
Webserver ausgeliefert noch in einen Gameserver-Container gemountet werden.

- `MSM_GUARDIAN_STATE_DIR` ändert den Pfad nur für bewusst abweichende
  Installationen.
- `MSM_GUARDIAN_LOOP_INTERVAL_SECONDS` bestimmt das Reconcile-Intervall
  (Default `5.0`). Kleinere Werte erhöhen Docker-/I/O-Last und sind kein
  Ersatz für sinnvolle Probe-Intervalle.
- Enthalten sind akzeptierte Sollzustände, beobachtete Zustände, unbestätigte
  Incidents und die höchstens zehn Recovery-Lockfile-Backups pro Server.
- JSON-State wird atomar geschrieben. Beschädigte Dateien werden nicht
  überschrieben, sondern als Corruption-Incident behandelt.
- Lockfile-Backups liegen unter
  `recovery-backups/<server-id>/<backup-id>/`; einzelne Dateien sind auf 1 MiB
  begrenzt und mit Modus `0600` angelegt.

Vor einer Node-Neuinstallation oder Migration muss dieser Pfad zusammen mit den
Serverdaten gesichert werden. Für eine Wiederherstellung Agent stoppen, den
Pfad mit unverändertem Eigentümer und restriktiven Rechten zurückspielen und
erst danach den Agent starten. Eine alte State-Kopie darf nicht parallel auf
zwei Nodes aktiv sein. Nach dem Start müssen Node-Heartbeat, Guardian-Zustand
und Generation/Hash im Panel übereinstimmen.

`install.sh`, `update.sh` und `helper-scripts/install-msm-agent.sh` legen den
State-Pfad an beziehungsweise erhalten ihn. Ein Update darf ihn nicht löschen.
Bei manueller Paketierung ist `/var/lib/msm-agent` deshalb ein persistentes
Release-Artefakt, kein Cache.

Bei getrenntem Hosting bezeichnet `MSM_PANEL_URL` die vom Benutzer geöffnete
Frontend-Origin, während `MSM_API_URL` die öffentliche Backend-Origin bezeichnet.
Bei einer All-in-one-Installation sind beide identisch. Cookies werden ohne
manuellen Override aus `MSM_API_URL` abgeleitet, weil nur der API-Host sie setzen
darf. `MSM_LOCAL_AGENT_ENABLED=false` wird auf einer migrierten Backend-only-
Control-Plane automatisch gesetzt; Betreiber müssen dafür keinen Token kopieren.

## SaaS-Hosting-Betrieb: Node-Härtung, Secret-Rotation, Admin-Monitoring

Wenn du **fremde Kunden** auf denselben Nodes hostest (Vermietung / SaaS), gilt
zusätzlich zur DB-Mandantenisolation (siehe
[`multi-node/phase-7.md` §6](multi-node/phase-7.md)) dieses Betriebs-Checkliste.
Sie ergänzt Code-Gates und ersetzt kein Host-CIS-Audit.

### Node-Härtung (Host / Agent / Docker)

| Thema | Erwartung |
| --- | --- |
| SSH / Root | Wenige privilegierte Personen; kein shared root; Keys statt Passwort wo möglich |
| Agent-Port | Nur Panel→Agent über TLS (+ Fingerprint-Pinning bei Remote-Nodes); nicht ungeschützt im Internet freigeben |
| Managed Postgres | Host-Bind nur `127.0.0.1`; Game-Container über **internal** Docker-Netz (`msm-internal`) |
| Docker | Kein unnötiges `privileged`; Agent-Hardening (`cap_drop`, Container-Name-Gates) beibehalten |
| systemd | Agent-Unit mit restriktiven Defaults (`ProtectSystem` u. a. wie von Installer gesetzt) |
| Trennung | Kritische Kunden optional auf eigenen Nodes, wenn Host-Compromise inakzeptabel ist |

Produktseitig erzwingbar: Loopback-Postgres, internal net, Agent-Auth, Panel-RBAC.
Nicht erzwingbar im Code: wer SSH auf dem Host hat — das bleibt Betriebsdisziplin.

### Secret-Rotation (was und wann)

| Secret | Wo | Rotation |
| --- | --- | --- |
| Managed-Postgres-Cluster-Admin (`msm_admin`) | Panel-Setting DIS (`managed_postgres.admin_password_encrypted`) + `ALTER ROLE` auf jedem Node mit Postgres | API: `POST /api/admin/managed-postgres/rotate-admin` (Permission `system.secrets.rotate`). Nach Verdacht, Personalwechsel oder periodisch (z. B. 90 Tage) |
| Node-Agent-Token | Node-Datensatz `auth_token_enc` | Node bearbeiten / Token tauschen (`nodes.manage`); Audit: `nodes.token.update` |
| App-DB-User / Power-User | Pro Server-DB | Bestehende Rotate-Endpunkte unter `/api/servers/{id}/databases/…` |
| Panel-Owner / Admin-Passwort | Auth | Reguläre Konto-Passwort- und 2FA-Pflege |

**Wichtig (kein stilles Split-Brain):** Die Admin-Rotation wendet `ALTER ROLE`
zuerst auf den Nodes an. Schlägt ein Node **hart** fehl, nachdem andere schon
rotiert wurden:

1. MSM versucht die **vorwärts erfolgreichen** Nodes zurück auf das **alte** Passwort zu setzen.
2. Gelingt der Rollback **vollständig**: Panel behält das **alte** Secret.
3. Schlägt der Rollback **nur bei manchen** Nodes fehl: die bereits zurückgesetzten
   Nodes bekommen das **neue** Secret erneut (`re-forward`), danach speichert das
   Panel das **neue** Secret. Alle vorwärts-erfolgreichen Nodes und das Panel
   liegen wieder auf demselben Secret.
4. Nodes, die beim Vorwärts-Schritt **hart** fehlgeschlagen sind, können noch das
   alte Secret haben — das wird in der Fehlermeldung benannt (manueller Follow-up).

Antwort und Audit enthalten **nie** das Passwort.

### Admin-Monitoring (Audit)

Privilegierte Aktionen schreiben in `audit_logs` (wer / wann / action / Ziel,
Details ohne Secrets):

- `postgres.admin.rotate`, `postgres.database.*`, `postgres.user.*`, `postgres.power_user.*`, `postgres.dump`, `postgres.restore`
- `nodes.token.update`, `nodes.enrollment.approve`

**Im Panel:** Administration → **Audit** (`/admin/audit`, Permission `system.audit.read`).  
**API:** `GET /api/admin/audit-logs?limit=50&action=…`  
Ohne Recht: **403** (kein leeres OK). Nav und Route sind gesperrt.

**Cluster-Admin rotieren im Panel:** Einstellungen → Tab **Sicherheit**  
(`system.secrets.rotate`). API: `POST /api/admin/managed-postgres/rotate-admin`.
Empfehlung: regelmäßig prüfen (wöchentlich oder nach Incidents), wer Power-User
aktiviert, Dumps gezogen oder Admin-Secrets rotiert hat.

### Kurz-Checkliste vor Go-Live mit Fremdkunden

1. SSH/Root-Kreis dokumentiert und klein  
2. Remote-Nodes nur HTTPS + TLS-Fingerprint  
3. Managed-Postgres-Admin mindestens einmal prozessiert/rotiert und Prozess bekannt  
4. `system.audit.read` / `system.secrets.rotate` nur für echte Betreiber-Rollen  
5. Ersten Audit-Abruf (`GET /api/admin/audit-logs`) verifiziert  

---

## Hoster- und Shop-Anbindung (optional, Phase 6)

Ein Betreiber kann einen externen Shop an MSM anbinden. MSM übernimmt dabei
**nicht** Shop, Zahlung oder Rechnung — nur den technischen Benutzerzugang, die
Servererstellung und den Lifecycle.

**Self-Hosted bleibt unberührt.** Ohne angelegte Integration existiert kein
Verhalten dieses Abschnitts. Die Tabellen bleiben leer, es gibt keinen
zusätzlichen Dienst und keinen offenen Endpunkt, der ohne API-Key etwas tut.

> Dieser Abschnitt beschreibt **Einrichtung und Betrieb**. Wer den Shop
> tatsächlich anbindet, braucht die vollständige Referenz mit allen
> Request- und Response-Feldern, Webhook-Nutzlasten, Eventnamen und einem
> nachrechenbaren HMAC-Beispiel: **[hoster-api.md](hoster-api.md)**, im Panel
> auch unter *Hilfe → Hoster-API*.

### Einrichtung im Panel

Einstellungen → Tab **Hoster** (Permission `panel.hoster.read`, Änderungen
`panel.hoster.write`).

1. **Dienstbenutzer anlegen.** Ein normaler Panel-Benutzer mit einer Rolle, die
   `servers.create` (und für automatische Löschung nach Kündigung zusätzlich
   `servers.delete`) enthält. Der Owner-Account ist bewusst nicht zulässig.
   Die Integration kann nie mehr als dieser Benutzer — das ist die
   Sicherheitsgrenze, nicht der API-Key.
2. **Integration anlegen.** Name, Slug, Dienstbenutzer-ID, optional das
   HTTPS-Webhook-Ziel und die Aufbewahrungsfrist nach Kündigung (Default 7 Tage).
   Der API-Key wird **genau einmal** angezeigt; MSM speichert nur seinen
   SHA-256-Hash.
3. **Produkte zuordnen.** Jede Shop-Produktkennung wird auf einen Blueprint und
   ein Ressourcenpaket abgebildet (RAM, CPU, Speicher, optional feste Node und
   Backup-Intervall). Der Shop muss keine internen MSM-IDs kennen.
4. **Webhook-Secret erzeugen**, falls der Shop Statusmeldungen empfangen soll.
   Auch dieser Wert erscheint nur einmal.

### Externe API

Authentifizierung über den Header `X-MSM-Hoster-Key`. Es gibt keinen
Cookie-Pfad; eine Browser-Session kann diese Endpunkte nicht ansprechen.

| Endpunkt | Zweck |
| --- | --- |
| `GET /api/hoster/v1/health` | API-Key prüfen, ohne etwas zu ändern |
| `PUT /api/hoster/v1/services/{external_service_id}` | Gewünschten Zustand setzen (`active`, `suspended`, `terminated`) |
| `GET /api/hoster/v1/services/{external_service_id}` | Tatsächlichen Zustand abfragen |
| `POST /api/hoster/v1/handoffs` | Einmal-Link in das Panel des Kunden erzeugen |

Beispiel für eine Bestellung:

```bash
curl -X PUT https://panel.example/api/hoster/v1/services/SVC-4711 \
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"active","external_subject":"CUST-1234","product_key":"mc-8gb"}'
```

Derselbe Aufruf darf beliebig oft wiederholt werden: `(Integration,
external_service_id)` ist eindeutig, ein Netzwerk-Retry erzeugt keinen zweiten
Server. Schlägt die Provisionierung fehl, bleibt der Vertrag mit
`status: "failed"` und einem stabilen Fehlercode abfragbar.

### Lebenszyklus

- `active` → Server wird bei Bedarf erstellt, Kundenrechte werden gesetzt.
- `suspended` → Server wird gestoppt, Kundenrechte werden entzogen. Der
  **Panelaccount des Kunden bleibt bestehen** und eine spätere Entsperrung ist
  möglich.
- `terminated` → Server wird gestoppt und eine Frist gesetzt. **Es wird nichts
  sofort gelöscht.** Erst ein Wartungslauf (jede Minute) entfernt den Server
  nach Ablauf von `terminate_grace_days` über denselben Löschpfad wie das Panel.

### Webhooks

MSM signiert jede Zustellung mit HMAC-SHA256 über `timestamp.body`:

```
X-MSM-Timestamp: 1786120930
X-MSM-Signature: sha256=<hex>
X-MSM-Event: service.ready
```

Der Empfänger berechnet dieselbe Signatur mit dem Webhook-Secret und vergleicht
zeitkonstant. Das Secret selbst wird **nie** übertragen. Zustellungen sind
persistiert: fünf Versuche mit wachsendem Abstand, 4xx ohne Wiederholung, und
ein Panel-Neustart während eines Backoffs verliert nichts. Fehlgeschlagene
Zustellungen lassen sich im Panel manuell erneut einplanen.

### Ein-Klick-Handoff

`POST /api/hoster/v1/handoffs` liefert eine URL, die fünf Minuten und genau
einmal gilt und nur auf `/servers`, `/servers/{id}` oder `/dashboard` führen
kann. MSM speichert ausschließlich den Hash; der Link erscheint weder im Audit
noch in Logs. Jeder Fehlerfall — unbekannt, abgelaufen, bereits verwendet —
führt einheitlich auf die Loginseite.

Alternativ oder zusätzlich kann der bestehende Custom-OIDC-Login des Hosters
verwendet werden (Einstellungen → **OAuth**).

### Betriebshinweise

- Der API-Key ist gleichwertig zu den Rechten des Dienstbenutzers. Bei Verdacht
  im Panel rotieren — der alte Key ist sofort ungültig.
- Eine Integration mit noch aktiven Verträgen lässt sich nicht löschen. Das ist
  Absicht: ein Cascade würde die Zuordnung zwischen Kunden und ihren laufenden
  Servern zerstören, während die Server weiterlaufen.
- Alle Vorgänge erscheinen im Audit (`hoster.*`) mit Origin `external` und einer
  gemeinsamen Korrelations-ID, ohne Secrets und ohne Kundenkennungen im Klartext.

---

## Zugangsdaten: panelweit, pro Benutzer, pro Server (Phase 7)

GitHub-Token und Steam-Konto können auf drei Ebenen existieren. **Für einen
Self-Hosted-Betrieb ändert sich nichts:** ohne eigene Zuordnung gilt weiterhin
der panelweite Zugang.

### Auflösungsreihenfolge

1. **Server-Zuordnung** — das diesem Server ausdrücklich zugewiesene
   Benutzer-Credential.
2. **Umgebungsvariable** — `MSM_GITHUB_CLONE_TOKEN` usw., wie bisher.
3. **Panel-Zugang** — Einstellungen → GitHub bzw. Steam Account.

Die Zuordnung steht oben, weil sie die spezifischste Aussage ist. Stünde ENV
davor, wäre eine bewusst gesetzte Kundenzuordnung wirkungslos.

### Bedienung

| Wo | Was |
| --- | --- |
| Profil → **Zugangsdaten** | Jeder Benutzer hinterlegt eigene Steam-Konten oder GitHub-Token. Der Wert wird verschlüsselt gespeichert und **nie wieder angezeigt** — sichtbar bleiben nur Bezeichnung, Benutzername und die letzten vier Zeichen. |
| Server-Detail → **Zugangsdaten** | Erscheint nur, wenn der Blueprint dieses Servers welche verlangt. Zeigt die Herkunft und erlaubt die Zuordnung. Erfordert `server.credentials.manage`. |
| Einstellungen → **Hoster** | Schalter *Zentraler Fallback*: darf ein Server ohne eigene Zuordnung den panelweiten Zugang mitbenutzen? |

### Für Hoster

Den zentralen Fallback **abschalten**. Dann läuft kein Kundenserver mehr
unbemerkt mit den Zugangsdaten des Betreibers — ein Server ohne eigene
Zuordnung meldet stattdessen einen verständlichen Fehler. Kunden erhalten auf
ihrem eigenen Server automatisch `server.credentials.manage` und können ihr
Steam-Konto selbst hinterlegen.

Ein Benutzer kann ausschließlich **eigene** Zugangsdaten zuweisen. Serverrechte
allein erlauben es nicht, fremde Zugangsdaten in Betrieb zu nehmen.

### Grenzen (bewusst)

- Der **Steam-Web-API-Key bleibt panelweit**. Er dient Workshop-Metadatenabfragen
  und nicht dem Zugriff auf Daten eines einzelnen Kunden; der Steam-Client ist
  zudem ein prozessglobaler Singleton.
- Der **CurseForge-API-Key bleibt panelweit** (`MSM_CURSEFORGE_API_KEY` oder per DIS-Verschlüsselung im Panel unter Einstellungen → CurseForge). Er dient Mod-Suchabfragen, Metadaten und Downloads für Spiele wie ARK: Survival Ascended oder Minecraft.
- Ein gebundenes Credential kann nicht gelöscht werden, solange ein Server es
  verwendet — sonst fiele dieser Server bei der nächsten Installation unbemerkt
  auf den Panel-Zugang zurück.
- Ein nicht mehr entschlüsselbares Credential (typisch nach `MSM_SECRET_KEY`-
  Rotation) führt zu einem klaren Fehler, **nicht** zu einem stillen Rückfall.

---

## KI-Werkzeuge und autonomer Modus (Phase 8)

**Ohne konfigurierten Provider passiert nichts von alledem.** Ein frisch
installiertes Panel hat keinen KI-Anbieter und alle Rollenkontingente stehen auf
0 — die KI ist damit aus, bis ein Betreiber sie unter *Einstellungen → KI*
einrichtet.

### Was die KI tun kann

Die KI ruft ausschließlich vorhandene MSM-Funktionen auf; es gibt keine freie
Befehlsausführung, keine Shell und keinen eigenen Downloadpfad. **Eine Stelle
verlangt eine genauere Formulierung**: über einen Blueprint kann die KI die
Startzeile setzen, aus der die argv eines Containers entsteht. Das ist keine
Befehlsausführung — der Abschnitt *Was die KI an einem Blueprint ändern kann*
sagt, wie weit es reicht und was es aufhält.

**Lesend** (jedes Werkzeug prüft sein eigenes Recht): Serverstatus, Node-Kapazität,
Logausschnitt, Konfigurationsdatei, Ports, Mods, Backups, Guardian-Vorfälle,
bisherige KI-Aktionen, ausstehende Modupdates, Workshop-Suche. Im Panel-Chat
zusätzlich die Blueprint-Liste und die Hostkapazität.

Die Erreichbarkeitsprüfung (`check_server_reachability`) sagt dabei ausdrücklich
**nichts über das Internet**. Sie sieht, ob die Ports auf der Node lauschen, wie
die Bind-Adresse einzuordnen ist, und was die im Blueprint deklarierte
Anwendungsprobe zuletzt gemeldet hat — die misst der Guardian dort, wo der
Server wirklich liegt; das Panel spricht selbst kein Spielprotokoll. Das Panel
steht hinter derselben Netzgrenze wie der Server, eine Verbindung auf die eigene
öffentliche Adresse prüfte also Hairpin-NAT und nicht die Außenwelt. Deshalb
steht in der Antwort dauerhaft `external_check: unavailable`: weder „von außen
offen" noch „von außen dicht" ist eine Aussage, die MSM treffen darf, und ein
erfundenes „ist erreichbar" wäre schlimmer als gar keine.

**Schreibend** — jedes erzeugt zunächst nur einen sichtbaren Vorschlag:
Start/Stop/Neustart, Backup und Backup-Wiederherstellung, revisionsgebundene
Konfigurationsänderung, Mod-Installation, Bind-IP, Servererstellung und
-löschung, das Löschen einer einzelnen Datei, die Reparatur der Anlage, stehende
Aufträge, die Shop-Anbindung (Integration, Produkt, Tarifrolle) — und die drei
Blueprint-Werkzeuge, die am weitesten reichen:

- **`propose_blueprint_change`** (`blueprints.manage`) leitet aus einer Vorlage
  eine neue ab und schreibt sie als Community-Blueprint. Es rührt **keinen**
  laufenden Server an; eine Vorlage, auf der bereits Server liegen, kann es
  nicht überschreiben.
- **`propose_blueprint_delete`** (`blueprints.manage`) entfernt eine
  Community-Vorlage per `unlink`. Es gibt keinen Versionsschnappschuss und
  keinen Papierkorb — deshalb ist es immer bestätigungspflichtig. Liegt noch ein
  Server auf der Vorlage, wird schon der Vorschlag mit der Anzahl abgewiesen.
- **`propose_server_blueprint_switch`** (`server.config.write`) stellt einen
  **gestoppten** Server auf eine andere Vorlage um. Was dabei geschieht, steht
  einzeln auf der Bestätigungskarte, weil „Blueprint wechseln" es sonst
  verschweigen würde: ein **Pflicht-Backup**, das **Löschen des gesamten
  Serververzeichnisses** samt Welten, Konfigurationen und Mods, eine
  Portneuvergabe und eine Neuinstallation. Der Server steht danach auf
  `installing`, nicht auf `stopped`.

Der Wechsel ist damit neben dem Löschen der weitreichendste Vorgang, den die KI
vorschlagen kann. Dass er trotzdem **nicht** auf der Immer-bestätigen-Liste
steht, ist eine bewusste Entscheidung und keine Lücke: das Pflicht-Backup
entsteht, bevor irgendetwas angefasst wird, und genau dieses Backup ist der Weg
zurück.

Konfigurationsänderungen der KI sind **dauerhaft**, und zwar unabhängig vom
Dateiformat. Was sie ändert — per Abschnitt und Schlüssel (`propose_config_set`)
oder als Teilersetzung (`propose_config_patch`) — wird zusätzlich am Server
hinterlegt und vor **jedem** Start erneut geschrieben. Das ist kein Komfort,
sondern die Bedingung dafür, dass eine Änderung bleibt: Spiele halten ihre
Einstellungen oft im Speicher und schreiben die Konfigurationsdatei beim
Autosave oder beim Start wieder auf den alten Stand — ARK tut das mit seiner
INI, andere Titel mit XML- oder JSON-Dateien. Ein Wert, den der laufende Prozess
nicht kennt, verschwindet dabei.

Welches Spiel das tut, muss niemand pflegen, und die Blueprints deklarieren
dafür nichts: der Wunsch hängt am Server, nicht an der Vorlage, und wird
ausnahmslos bei jedem Start durchgesetzt. Bei INI-Dateien ist der Anker Abschnitt
und Schlüssel, bei allen anderen Formaten ein exakter Textausschnitt, der genau
einmal vorkommen muss — ist er das später nicht mehr, wird das im Konsolenlog
gemeldet statt geraten. Ein laufender Server ist deshalb kein Hindernis für eine
Konfigurationsänderung (anders als beim Blueprint-Wechsel, der das
Serververzeichnis löscht) — sie wirkt mit dem nächsten Neustart. Passwortfelder
bleiben ausgeschlossen, und die hinterlegten Werte stehen im Serververzeichnis
wie jede andere Konfiguration.

Die Reparatur (`propose_server_repair`) nimmt **eine Kennung aus einer festen
Liste** entgegen — `repair_permissions` oder `reallocate_port` — und niemals
einen Pfad, ein Kommando oder einen Containernamen. Der Containername entsteht
in jedem Zweig aus `container_name_for(server_id)`. Das ist die mechanische
Seite der Zusage, dass es keinen Weg von einer Modellausgabe zu einer
Befehlszeile gibt.

Die Servererstellung läuft über denselben `server_provisioning_service` wie ein
Klick im Panel und eine Shop-Bestellung. Blueprintprüfung, Kapazität, Portvergabe,
Installation und Rollback sind identisch — es gibt bewusst keinen zweiten Weg,
einen Server anzulegen.

### Was die KI an einem Blueprint ändern kann

Änderbar ist eine **bewusst kurze Liste** von Punktpfaden (`AENDERBARE_PFADE` in
`services/blueprint_service.py`): `meta.name`, `meta.description`,
`runtime.image`, `runtime.env` und `runtime.startup`. Portrollen,
Installationsquelle und alles Übrige bleiben, wie sie sind; wer daran etwas
ändern will, lädt einen vollständigen Blueprint hoch — dann sieht ein Mensch das
ganze Ergebnis, statt einer Liste von Einzeländerungen zuzustimmen, deren
Zusammenwirken er nicht überblickt.

`runtime.startup` verlangt dabei eine ehrliche Einordnung: das ist die Zeile,
aus der die argv des Containers entsteht. Sie steht in der Liste, seit eine
falsche Startzeile bei GitHub-Quellen der häufigste Grund war, dass ein Server
gar nicht erst hochkommt — die KI soll sie korrigieren können. Vier Schranken
stehen davor:

- **Es ist kein Shell-Aufruf.** Der Renderer (`blueprints/renderer.py`)
  tokenisiert den String mit `shlex.split` und übergibt die fertige Liste an
  `docker run`, nie über `sh -c`. Ein Argument kann deshalb kein zweites
  Argument erzeugen, gleich was darin steht.
- **Die Schemaprüfung läuft vor dem Vorschlag, nicht danach.** `$` und Backtick
  sowie `$(`, `${`, `&&` und `||` sind in `runtime.startup` verboten. In einer
  argv-Liste wären sie ohnehin harmlos; das Verbot ist Defense-in-Depth für den
  Fall, dass ein Konsument den String je an eine Shell weiterreicht. Weil der
  abgeleitete Blueprint schon beim *Vorschlagen* gebaut und validiert wird,
  entsteht eine Karte, die das Schema verletzt, gar nicht erst.
- **Platzhalter sind eine Whitelist.** Erlaubt sind `{GAME_PORT}`,
  `{QUERY_PORT}`, `{RCON_PORT}`, `{VOICE_PORT}`, `{WEB_PORT}`, `{INSTALL_DIR}`,
  `{MOD_ARG}`, `{BIND_IP}`, `{CUSTOM_PORT_<N>}` und `{ENV.<KEY>}`. Ein
  unbekanntes Token macht den Blueprint ungültig, statt beim Start zu einem
  leeren Argument zu werden.
- **Der Mensch liest die Zeile.** Die Bestätigungskarte stellt `startup_before`
  und `startup_after` gegenüber, dazu Image und Umgebung. Ohne diese
  Gegenüberstellung bestätigte jemand einen Startbefehl, den er nie zu sehen
  bekommen hat.

`runtime.startupProfiles` steht bewusst **nicht** in der Liste: das ist eine
Liste mit Bedingungen, und eine Punktpfad-Änderung daran überblickt niemand auf
einer Karte. Führt die Quelle Profile, weist der Dienst eine Änderung an
`runtime.startup` deshalb ab, statt sie wirkungslos durchzureichen —
`resolve_startup_template` nähme dort ohnehin das erste Profil, dessen
`whenFile` existiert, und die Korrektur bliebe unbemerkt folgenlos.

Das Recht ist `blueprints.manage`, und es ist panelweit: wer keine Blueprints
verwalten darf, kommt an diesen Weg nicht heran. Wirksam wird eine so geänderte
Vorlage außerdem erst, wenn ein Server auf ihr liegt — entweder weil er neu
darauf angelegt wird, oder über `propose_server_blueprint_switch`, und der
wischt das Serververzeichnis und will bestätigt werden.

### Autonomer Modus

Standard ist der unterstützte Modus: die KI analysiert, schlägt vor, wartet.

Autonomie verlangt **vier** Bedingungen gleichzeitig:

1. die Berechtigung `ai.autonomous.use`;
2. eine ausdrückliche Freigabe des Benutzers — pro Server im KI-Tab des Servers
   oder panelweit unter *KI*. Eine Freigabe für einen konkreten Server gewinnt
   über die panelweite, **auch wenn sie abschaltet**;
3. ein Werkzeug, das nicht auf der Immer-bestätigen-Liste steht. Die Liste heißt
   im Code `ALWAYS_CONFIRM_TOOLS` (`services/ai_tool_registry.py`) und ist dort
   keine eigene Aufzählung, sondern die Ableitung aus der Spalte
   `immer_bestaetigen` der Werkzeugtabelle. Gebaut und gesperrt sind heute:
   `propose_server_delete`, `propose_blueprint_delete`, `propose_backup_restore`,
   `propose_hoster_integration`, `propose_hoster_product` und
   `propose_ai_tarif_role`. Dazu kommen vier Namen aus dem Zielbild, die es noch
   nicht gibt und die vorsorglich gesperrt sind, damit ein künftiges Werkzeug
   sich einordnen muss statt stillschweigend autonomiefähig zu sein:
   `propose_server_wipe`, `propose_server_reinstall`,
   `propose_permission_change` und `propose_secret_rotation`.

   Das Kriterium ist **Unumkehrbarkeit, nicht Risiko** — ausdrückliche Vorgabe
   des Betreibers, und sie ersetzt eine frühere Einteilung nach „das klingt
   heikel". Was die KI selbst wieder zurückstellen kann, darf sie autonom tun;
   was Daten vernichtet, die niemand zurückholt, fragt immer. Deshalb steht der
   Blueprint-*Wechsel* trotz seiner Reichweite nicht auf der Liste (er legt
   zwingend ein Backup an, bevor er etwas anfasst), das Blueprint-*Löschen*
   dagegen schon: `unlink` ohne Schnappschuss. Die Rechte- und
   Schlüsselwerkzeuge stehen aus einem anderen Grund darauf — sie wirken auf die
   Grenzen, innerhalb derer die KI selbst arbeitet;
4. freies Stundenbudget (Standard 10 Aktionen). Ist es erschöpft, **schlägt
   nichts fehl** — die KI fragt einfach wieder nach.

> **Was Autonomie nicht entfernt.** Sie ersetzt genau einen Schritt: die
> Bestätigung durch einen Menschen. Die Rechteprüfung läuft weiterhin dreimal
> (Vorschlag, Freigabe, unmittelbar vor der Ausführung), der Server-Mutex gilt,
> und jede Aktion steht im Audit mit `autonomous: true`. Die KI kann nichts, was
> der handelnde Benutzer nicht selbst dürfte.

Ein Betreiber, der Autonomie grundsätzlich erlauben, aber auf einem empfindlichen
Server ausschließen will, legt dort eine Freigabe mit `enabled: false` an.

### Guardian weckt die KI

Mit erteilter Freigabe hat der autonome Modus eine zweite Wirkung: ein
Guardian-Vorfall startet eine **Reparatur**, ohne dass jemand am Panel sitzt.
Ein Scheduler-Job sieht alle sechzig Sekunden nach offenen Vorfällen und nach
fälligen Reparaturaufträgen.

Eine Reparatur ist kein einzelner Lauf, sondern ein Auftrag über Stunden. Der
Grund ist Betriebserfahrung: ein Lauf las ein paar Werkzeuge, schrieb „ohne
Freigabe kann ich da nichts machen", und derselbe Vorfall wurde **nie wieder**
angefasst — auch dann nicht, wenn der Lauf nur an seinem Rundenbudget endete.

- **Der Akteur ist der Freigeber**, nicht ein Dienstbenutzer und nicht der
  Serverbesitzer: derjenige Benutzer, der für diesen Server die Autonomie
  eingeschaltet hat und zusätzlich `ai.chat.use`, `ai.autonomous.use` und
  `server.view` besitzt. Verbrauch und Verantwortung liegen bei ihm. Gibt es
  mehrere, gewinnt die serverbezogene Freigabe vor der panelweiten, danach die
  kleinste Benutzer-ID.
- **Ohne Freigabe passiert nichts** — kein Lauf, kein Anbieteraufruf, keine
  Tokens. Der Vorfall wird nur vorgemerkt und beim nächsten Chat des Benutzers
  als Meldung des Panels erwähnt.
- **Die Reparatur läuft in einem eigenen Fenster**, nicht im Dauerchat. Jeder
  Benutzer hat zwei Unterhaltungen: den Chat und das Guardian-Fenster
  (`/ai?ansicht=guardian`). Damit würgen sich beide nicht mehr gegenseitig ab —
  vorher verhinderte ein laufender Chat jede Heilung, und eine getippte
  Nachricht beendete eine laufende. Im Guardian-Fenster kann man zusehen; ein
  Knopf „Übernehmen" bricht den Auftrag ausdrücklich ab und öffnet den Chat.
  Ein Eingabefeld gibt es dort nicht, damit ein versehentlicher Tastendruck
  keine laufende Reparatur abwürgt.
- **Drei Phasen, mit Frist und Versuchsdeckel.** `diagnose` → `eingriff` →
  `beobachtung`, und von dort zurück in den Eingriff, bis die Anlage den Server
  als gesund meldet. Vorgabe sind sechs Stunden und acht Anläufe; danach endet
  der Auftrag als `aufgegeben`. Zwischen zwei Beobachtungen wartet der Auftrag
  zehn Minuten, statt in einer Schleife zu pollen — ein beendeter Lauf ist
  billiger als ein wartender.
- **„Erledigt" entscheidet die Anlage, nicht das Modell.** Der Vorfall steht auf
  `resolved`, der Server ist nicht in Quarantäne, und der vom Agenten
  beobachtete Zustand passt zum gewünschten. Ein durchgelaufener `docker start`
  reicht nicht.
- **Die Werkzeugmenge ist enger** als im Chat (`GUARDIAN_HEILUNG_TOOLS`):
  Lesewerkzeuge, Backup, Lifecycle, Konfiguration, Dateilöschung, Reparatur,
  Guardian-Einstellungen, Blueprints lesen und ableiten, Doku. Nicht dabei sind
  Gedächtnis, Skills, Hoster, das Löschen eines Blueprints, die
  Backup-Wiederherstellung, stehende Aufträge, Servererstellung und -löschung
  sowie die Websuche. Die `server_id` steht fest im Lauf; ein Werkzeugaufruf auf
  einen anderen Server wird abgewiesen, bevor er aufgelöst wird.
- **Guardian lässt sich je Server anders einstellen** (`propose_guardian_tuning`).
  Der Blueprint gilt für jeden Server seines Spiels und kann nicht wissen, dass
  auf dieser Node zwölf Instanzen um acht Gigabyte streiten; genau diese Lücke
  füllt die Übersteuerung. Erlaubt ist eine geschlossene Menge von zwölf Zahlen
  mit Ober- und Untergrenze — Startfenster, Probenabstand und -geduld,
  Schwellen, Wiederherstellungsversuche, Verifikationsfenster. Keine Listen,
  keine Regexe, keine Probentypen. Was gilt, steht sichtbar im Autopilot-Reiter
  samt Herkunft, und ein Knopf setzt es auf den Blueprint zurück
  (`server.config.write`). Nimmt der Agent die Konfiguration nicht an, wird die
  Übersteuerung zurückgerollt — sonst hinge die Synchronisation dieses Servers
  dauerhaft in einem gespeicherten Fehler.
- **Blueprints ableiten ja, wechseln nur mit Zustimmung.** Eine Ableitung legt
  eine neue Datei an und rührt keinen Server an. Der *Wechsel* eines Servers auf
  einen anderen Blueprint löscht dagegen das gesamte Serververzeichnis — Welt,
  Konfigurationen, Mods — und installiert frisch; er verlangt deshalb immer eine
  menschliche Bestätigung.
- **Vor jedem schreibenden Eingriff muss ein Backup nachgewiesen sein.** Nicht
  „angestoßen", sondern nachgewiesen: `backups.verified_at` ist gesetzt, das
  Archiv ist nicht leer, seine sha256 wurde gerechnet, und es ist jünger als der
  Vorfall. Fehlt der Nachweis, endet die Aktion mit `AI_BACKUP_UNVERIFIED` und
  die ganze Runde bricht ab — sonst folgte auf ein gescheitertes Backup ein
  Löschvorgang. Das Archiv heißt `Guardian-Heilung – <typ> – <uuid>`, damit im
  Backup-Reiter erkennbar ist, wozu es gehört.
- **Guardians eigene Heilungsleiter pausiert** währenddessen über
  `guardian_recovery_suspension_lease`. Die **Quarantäne hebt die KI nie auf** —
  die gehört dem Agenten.
- **Im Audit steht `origin=system`** statt `origin=ai`. Damit ist unterscheidbar,
  ob ein Mensch die KI gebeten hat oder ein Vorfall sie geweckt.
- **Eine E-Mail je Auftrag**, am Ende — nicht je Anlauf. Bei jedem Ausgang, auch
  bei „nicht behoben". Der Text stammt vom Modell und ist HTML-escaped;
  „behoben" steht nur dort, wenn die Anlage es zeigt.
- **Bestätigungspflichtige Schritte fragen per E-Mail**, statt den Lauf zu
  beenden. Trifft eine Reparatur auf etwas, das der autonome Modus nie ohne
  Klick tut — Serverlöschung, Wipe, Neuinstallation, Blueprint-Wechsel,
  Backup-Wiederherstellung —, geht ein Freigabelink an den Freigeber; der Lauf
  parkt und wird geweckt, sobald entschieden wurde. Der Link zeigt eine Seite
  (`GET`), entschieden wird per `POST` von dort: Mailscanner klicken Links. Er
  gilt 24 Stunden, lässt sich genau einmal verwenden, und **umgeht keine
  Prüfung** — Rechte, Backup-Schranke und Server-Mutex laufen unverändert. Im
  Audit steht `confirmed_via='email'`. Ist keine Adresse hinterlegt oder kein
  Versandweg eingerichtet, bleibt es beim alten Verhalten: zurücknehmen und
  ehrlich melden. Endet der Auftrag, bevor jemand geantwortet hat, wird der Link
  entwertet und der Auftrag als `eskaliert` abgeschlossen.

Der Schalter erklärt beides in einem Dialog, in beide Richtungen: Ausschalten
heißt, dass Vorfälle danach nur noch gemeldet und nicht mehr behoben werden.

### Was an den Anbieter geht

Nachricht, begrenzte Historie, freigegebene Servermetadaten und die Ergebnisse
der Werkzeugaufrufe — jeweils durch `redact_sensitive_text` geführt und
längenbegrenzt. Die Schwärzung sitzt an **einer** Stelle: dort, wo ein
Werkzeugergebnis für das Modell serialisiert wird. Vorher schwärzte jeder
Handler für sich, und mehrere taten es gar nicht.

Erkannt und ersetzt werden Secrets, Token, Schlüssel und E-Mail-Adressen. Bei
Freitext aus einem Server (Logzeilen, Dateiinhalte, Vorfallbeschreibungen) kommen
**öffentliche** IP-Adressen dazu; private, Loopback- und Link-Local-Adressen
bleiben stehen, weil eine geschwärzte Bind-Adresse jede Netzwerkdiagnose
unmöglich machte. **Spielernamen sind nicht mustererkennbar** und können in
Logausschnitten stehen — das ist die Grenze dieser Zusage, und sie steht so auch
in der Datenschutzerklärung.

Tool-Ergebnisse werden für Rückfragen im selben Chat gespeichert
und mit dem Chat gelöscht. Alles, was aus einem Server stammt (Logs, Configs,
Memory, Anhänge), ist im Modellkontext ausdrücklich als `untrusted` markiert.

Der Prompt ist dabei **nicht** die Sicherheitsgrenze. Selbst wenn ein Modell
einer in ein Gameserver-Log geschriebenen Anweisung folgt, scheitert die
Umsetzung an RBAC, der Tool-Allowlist, den Pfadgrenzen und der
Bestätigungspflicht.

### Azure als Anbieter

Zwei Einträge unter *Einstellungen → KI → Anbieter*: **Azure OpenAI** für die
Modelle von OpenAI und die Foundry-Modelle (Llama, Mistral, DeepSeek), **Azure ·
Anthropic Claude** für Claude. Beide brauchen zwei Angaben — den Schlüssel und
den **Namen deiner Azure-Ressource**, nicht ihre Adresse:

| Feld | Wert |
|---|---|
| Anbieter | *Azure OpenAI* oder *Azure · Anthropic Claude* |
| Azure-Ressourcenname | nur der Name, z. B. `mein-ai-hub` |
| Schlüssel | ein Schlüssel aus <https://ai.azure.com/> |
| Standardmodell | der Name deines **Deployments**, so wie du es angelegt hast |

Daraus baut MSM `https://mein-ai-hub.services.ai.azure.com/openai/v1` bzw.
`…/anthropic` — dieselben zwei Adressen, die dir das Azure-Portal anzeigt. Die
Anfrage geht dann an `…/openai/v1/responses` bzw. `…/anthropic/v1/messages`;
die Version steht bei Claude im Pfad der Operation und nicht in der Adresse, so
wie Anthropic selbst sie schneidet. Schema, Suffix und Pfad gehören dem Programm; aus der
Einstellung kommt ein einzelnes DNS-Label, geprüft gegen Buchstaben, Ziffern und
Bindestriche (2 bis 63 Zeichen, keiner am Rand, kein `xn--`). Das ist der eine
Ort, an dem MSM überhaupt noch Betreibereingaben in eine Zieladresse lässt — und
der Grund, warum ein geänderter Ressourcenname den hinterlegten Schlüssel
verwirft: er gehörte der alten Ressource.

> **Verbleibendes Risiko.** In einem Netz mit Azure Private Link kann derselbe
> gültige Name auf eine private Adresse im VNet zeigen. Die Formprüfung sieht
> das nicht; das ist eine Frage der Netzkonfiguration und keine der Eingabe.

Drei Unterschiede zu OpenRouter, die im Formular sichtbar sind:

- **Keine Modelliste.** Ein Modell heisst bei Azure so, wie du dein Deployment
  genannt hast — `prod-chat` ist ein gültiger Name. Eine Liste dafür gibt es
  nicht, also bleibt das Modell ein Textfeld und „Modelle neu laden" ist
  gesperrt. Heisst dein Deployment wie das Modell (`claude-sonnet-5`), leiht
  sich MSM Kontextfenster und Denkstufen aus dem Katalog von OpenRouter;
  heisst es anders, bleiben sie ehrlich unbekannt und nie geraten.
- **Kein Gehör.** Azures Transkription liegt hinter einer Vorschau-API. Der
  Anbieter erscheint deshalb als *Nur Chat*, und das Feld für das hörende
  Modell wird gar nicht erst angeboten. Für den Sprachmodus braucht es
  weiterhin OpenRouter oder OpenAI.
- **Claude spricht seinen eigenen Dialekt.** Die Messages-API von Anthropic ist
  kein `chat_completions`: `system` steht neben den Nachrichten statt als Rolle,
  Werkzeuge tragen `input_schema`, und der Strom besteht aus benannten
  Ereignissen ohne `[DONE]`. MSM übersetzt das; für dich ändert sich nichts
  ausser der Wahl des Anbieters.

**Nachdenken funktioniert an beiden Zugängen** — und darauf kommt es an, denn
alle aktuellen Modelle arbeiten mit Stufen statt mit einem Schalter. Zwei
Dinge stehen dahinter, die man sonst als Fehler erlebt statt als Entwurf:

- **Der GPT-Weg ist `/responses` und nicht `/chat/completions`.** Azure lehnt
  dort — wie OpenAI direkt — jede Anfrage ab, die Werkzeuge *und* eine echte
  Denkstufe trägt. Sichtbar war das als „Zugang läuft, bis ich eine Stufe
  wähle". MSM nimmt deshalb denselben Weg wie bei OpenAI: `/openai/v1/responses`,
  wo Denken und Werkzeugaufruf in dieselbe Runde passen. Für Claude gilt das
  nicht, dessen Messages-API kennt die Einschränkung nicht.
- **Die Stufen kommen aus dem geliehenen Katalog.** Azure führt keine
  Modelliste, also schlägt MSM die eingetippte Kennung einzeln nach. Heisst
  dein Deployment wie das Modell (`gpt-5.6-luna`, `claude-sonnet-5`), stehen
  Stufen und Kontextfenster zur Verfügung — im Chat **und** bei der festen
  Stufe des Hintergrund-Workers. Heisst es anders, bleibt beides ehrlich
  unbekannt und die Stufenauswahl verschwindet, statt eine erfundene zu zeigen.
  Wer die Stufen braucht, benennt sein Deployment wie das Modell.

---

## KI-Aufgaben (stehende Aufträge)

Der zweite Anlass, zu dem die KI ohne anwesenden Menschen arbeitet — neben der
Guardian-Heilung. Dort weckt sie eine Störung, hier die Uhr.

Ein stehender Auftrag entsteht im Chat — man sagt der KI, was regelmäßig
geschehen soll, und bestätigt die Karte — **oder von Hand in der
Aufgabenliste**: der Knopf neben dem Guardian-Fenster auf der KI-Seite öffnet
eine kleine Seite im Stil des Chats. Drei Angaben genügen dort: Name (für dich
und die Sortierung), Auftragstext (für die KI) und Zeitplan. Beide Wege rufen
dieselben Dienstfunktionen mit denselben Prüfungen (`/api/ai/tasks`); alles,
was die KI kann — anlegen, ändern, pausieren, löschen —, kann der Benutzer
dort auch. Deaktiviert die KI (oder eine manuelle Zeitplan-Änderung am Server)
eine Aufgabe, steht das dort sichtbar.

```
„Benachrichtige mich jeden Tag um 8 Uhr per Mail über den Zustand meiner Server."
„Mach jeden Tag um 3 Uhr ein Backup von allen Servern."
„Sag mir jeden Morgen, wie das Wetter wird."
„Welche Aufgaben hast du für mich?"  →  listet alles auf
„Pausier die erste."                  →  angehalten, aber nicht verloren
„Lösch die zweite."                   →  ganz weg
```

**Zwei Arten.** Ein *Bericht* (`report`) liest, fasst zusammen und meldet sich;
er darf nichts verändern. Ein *handelnder* Auftrag (`act`) darf zusätzlich
schreiben — und setzt den **autonomen Modus** voraus, mit Recht *und* erteilter
Freigabe. Die KI sagt das beim Anlegen und nicht erst um drei Uhr nachts. Wird
die Freigabe später zurückgezogen, schaltet sich der Auftrag beim nächsten
Termin ab, statt still zu Vorschlägen zu werden, die niemand bestätigt.

**Zeitzone.** Sie steht an der Aufgabe, als IANA-Name (`Europe/Berlin`). Die KI
fragt danach, wenn sie sie nicht schon aus dem Gedächtnis kennt, und nennt sie
in jeder Bestätigung und jeder Auflistung mit. „Täglich um 08:00" ohne Zone ist
genau die Angabe, bei der man sich später fragt, warum die Mail um neun kam.

**Zeitplan.** Täglich zu einer Uhrzeit (wahlweise nur an bestimmten
Wochentagen), in einem Abstand von 1 bis 168 Stunden, oder einmalig zu einem
Zeitpunkt. Ein Benutzer hat höchstens 20 Aufträge.

**Zustellweg.** `chat`, `email` oder `both`. Der Verlauf steht **immer** im
Chat — `email` heißt *zusätzlich*, nicht *ausschließlich*. Die Mail geht über
denselben panel-eigenen SMTP wie jede andere Benachrichtigung und über
denselben Schalter (*Profil → E-Mail-Benachrichtigungen*); einen zweiten
Schalter gibt es nicht. Auf Bitte hin kann die KI mit `send_test_email` prüfen,
ob dieser Weg funktioniert — Empfänger ist immer die eigene hinterlegte
Adresse, nie eine genannte.

**Was beim Fälligwerden gilt.** Ein Takt sieht jede Minute in der Tabelle nach;
der Zeitplan lebt also nicht im Scheduler, sondern in der Datenbank und
überlebt jeden Neustart. Der Lauf passiert **im Hintergrund**, in einem
eigenen Fenster je Aufgabe (wiederverwendet, wie ein Worker-Fenster) — der
Dauerchat wird weder unterbrochen noch vertagt, dort steht nur, was der Mensch
schreibt. Das Ergebnis bringt die Meldestelle in den Chat, sobald dort Ruhe
ist; der volle Verlauf ist über die Aufgabenliste erreichbar. Ein Termin, der
mehr als eine Stunde alt ist, wird
**übersprungen** und nicht nachgeholt — ein um elf Uhr nachgeholtes
Nachtbackup ist schlechter als keines. Im Lauf selbst ist die Werkzeugmenge
enger als im Chat: keine Rückfragen (es sitzt niemand da), kein Gedächtnis- und
Skill-Schreiben, keine Hoster-Werkzeuge, kein Löschen (weder Server noch Datei
noch Blueprint), keine Backup-Wiederherstellung, keine Blueprint-Ableitung und
kein Blueprint-Wechsel — und ein Auftrag legt keine Aufträge
an. Verlangt ein Vorschlag trotzdem eine Bestätigung, wird er zurückgenommen
und der Lauf endet mit einer ehrlichen Fehlanzeige — statt auf einen Klick zu
warten, den niemand tut.

**Abgrenzung — und die Ausnahme für Neustarts und Backups.** *Auto-Neustart*
und *Auto-Backup* am einzelnen Server bleiben, was sie sind: fester Zweck, kein
Modell, kein Kontingent. Genau deshalb legt die KI für „starte den Server alle
sechs Stunden neu" oder „mach jeden Tag ein Backup" **keinen** stehenden
Auftrag an, sondern stellt die eingebauten Zeitpläne des Servers ein
(`propose_restart_schedule_set`, `propose_backup_schedule_set`; Recht
`server.config.write`). Das Ergebnis steht am Server, trägt dort das Abzeichen
„von der KI verwaltet" und bleibt von Hand änderbar — eine manuelle Änderung
nimmt der KI die Verwaltung ab und pausiert die verknüpfte Aufgabe, falls eine
dahintersteht. Das Backup-Intervall reicht bis 720 Stunden (30 Tage), die
Aufbewahrung bis 100 Stände. Nur was die eingebauten Zeitpläne nicht ausdrücken
können — etwa „Mo/Mi/Fr um 10 Uhr" —, wird weiterhin ein stehender Auftrag.

Recht: `ai.tasks.manage` (Gruppe *KI*), zusätzlich `ai.chat.use`. Für
handelnde Aufträge kommt `ai.autonomous.use` samt Freigabe dazu.

---

## Sprachmodus (mit der KI reden)

Derselbe Agent, dieselbe Unterhaltung, dieselben Werkzeuge — nur gesprochen
statt getippt. Reinreden bricht die Antwort der KI ab, wie bei einem Menschen.

**Es ist ein eigener Modus, kein Zusatz neben dem Chat.** Oben rechts auf der
KI-Seite steht *Realtime-Modus*; ein Klick, und der Chat weicht einer Kugel, die
sich zum gesprochenen Wort bewegt. Zurück geht es über *Text-Modus* oder mit
`ESC`. Das ist Absicht: ein Sprachmodus neben einem Eingabefeld ist beides halb —
man weiss nicht, wohin man schaut, und das Textfeld verspricht eine Eingabe, die
gerade niemand benutzt.

Der Chat ist dabei einen Klick weit weg, nicht gelöscht — es ist dieselbe
Unterhaltung, und wer mitten im Gespräch hinüberwechselt, liest dort weiter, was
er eben gehört hat.

**Bestätigt wird gesprochen.** Eine Schreibaktion erzeugt denselben Vorschlag wie
im Chat, aber ohne Knopf: die KI sagt, was sie vorhat, ein *Ja* führt es aus,
alles andere nicht. Daneben erscheint ein Kasten mit dem Namen der Aktion —
gesprochen ist „ich lösche dann mal den Server" eindeutig genug, um zuzustimmen,
und zu ungenau, um zu wissen, welchen. Das gilt auch für Löschen und
Backup-Einspielen; der Sprachmodus kennt keine Aktion, die er dem Chat vorbehält.

### Ein zweiter Anbieterzugang, und warum

Der Sprachmodus benutzt **dasselbe Modell wie der getippte Chat**. Es denkt,
ruft Werkzeuge, legt Vorschläge an — genau wie sonst. Davor und dahinter stehen
zwei Wandler:

```
Mikrofon ─► Gehör (Chatanbieter) ─► derselbe Chatlauf ─► Stimme (ElevenLabs) ─► Lautsprecher
```

Hier stand bis zum 16.08.2026 OpenAIs Realtime-API — ein **zweites** Modell, das
selbst sprach und dafür einen eigenen Werkzeuglauf, eine eigene Bestätigung und
ein eigenes Gedächtnis neben denen des Chats brauchte. Zwei Wege, die dasselbe
Panel bedienen durften, hiess: jeder Befund musste zweimal behoben werden, und
beim zweiten Mal regelmässig anders. Der Sprachmodus kann seitdem alles, was der
Chat kann, weil er der Chat ist.

Der Betreiber braucht dafür zweierlei. Erstens **an einem Chatzugang** —
OpenRouter oder OpenAI — ein Modell, das zuhört:

| Feld | Wert |
|---|---|
| Modell für Gesprochenes | ein Transkriptionsmodell, z. B. `openai/gpt-transcribe` (OpenRouter) oder `gpt-transcribe` (OpenAI) |

> **Es steht nicht in der Modellliste — und das ist kein Fehler.** MSMs
> Modellauswahl liest den `/models`-Katalog des Anbieters, und dort stehen
> Chatmodelle. Transkriptionsmodelle werden über einen eigenen Endpunkt bedient
> (`/audio/transcriptions`) und tauchen in dieser Liste nicht auf. Deshalb ist
> das Feld ein Textfeld: hier wird die Kennung eingetippt, nicht ausgewählt.
>
> Hier stand bis zum 17.08.2026 das Gegenteil — OpenRouter habe keinen
> Transkriptions-Endpunkt, Gesprochenes müsse als Inhaltsteil in eine
> Chatanfrage. Geprüft worden war die Modellliste, und aus „kein Whisper in der
> Liste" wurde „kein Endpunkt". Die Lehre steht hier, weil sie sich wiederholen
> lässt: **ein leerer Katalog ist kein fehlender Endpunkt.** Ein `404` auf den
> Pfad wäre der Beweis gewesen; der Pfad antwortet ohne Schlüssel mit `401`.

#### Zwei Hörwege, und wann man den zweiten braucht

Es gibt **zwei** Wege, auf denen aus Ton Text wird. Welcher gilt, sagt
`MSM_AI_STT_WEG`; ohne Eintrag nimmt MSM den besten, den der Anbieter kann.

| Wert | Weg | Wann |
|---|---|---|
| *(leer)* | `POST /audio/transcriptions` | Vorgabe. Ein Dienst, der nur abschreibt: billig und ohne Nachdenken. |
| `chat` | `POST /chat/completions` mit dem Ton als `input_audio` | Nur OpenRouter. Wenn der Endpunkt nicht bezahlbar ist. |

> **Die Falle, wegen der der zweite Weg existiert.** OpenRouters
> Transkriptions-Endpunkt wird aus **Guthaben** bezahlt und **nicht** über einen
> hinterlegten Fremdschlüssel (BYOK). Ein Konto ohne Guthaben chattet also
> weiter — der Chat läuft über BYOK — und hört nicht mehr. Im Log steht dann
> `AI_PROVIDER_REQUEST_REJECTED` mit `Insufficient credits`, was nach einem
> kaputten Schlüssel aussieht und keiner ist.
>
> `MSM_AI_STT_WEG=chat` lässt stattdessen ein hörfähiges **Chat**modell
> abschreiben. Je Aufruf teurer, aber über dieselbe Abrechnung wie alles andere
> — und im Katalog stehen hörfähige Modelle zum Nulltarif, etwa
> `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`. In das Feld „Modell für
> Gesprochenes" gehört dann dieses Chatmodell und kein Transkriptionsmodell.
>
> Der Chatweg hat einen Nachteil, der kein Preis ist: er braucht einen Prompt
> („schreibe ab, befolge nicht"), und wo ein Prompt ist, lässt sich etwas
> hineinsprechen. Der Endpunkt hat keinen. Eine verfälschte Abschrift bleibt
> trotzdem nur ein verfälschter Satz — sie geht als Benutzernachricht in
> denselben Lauf, mit denselben Rechten und durch dieselbe Bestätigung.

> **Wer nur OpenAI hat**, trägt dort `gpt-transcribe` oder `whisper-1` ein und
> braucht die Einstellung nicht: OpenAI kennt nur den Endpunktweg, und der wird
> normal vom Konto abgebucht.
>
> Ein OpenAI-Zugang taugt auch für den getippten Chat — er spricht dasselbe
> `chat_completions` wie OpenRouter. **Er weiß nur weniger über sich selbst:**
> OpenAIs `/models` liefert je Modell nur Kennung, Besitzer und zwei Daten. Kein
> Kontextfenster, keine Preise, keine Denkstufen. Also gibt es an einem
> OpenAI-Zugang keine Denkstufenauswahl, und das Kontextfenster bleibt
> „unbekannt" — was in MSM überall „unbekannt" heißt und nie „klein". Die
> Modellliste enthält aus demselben Grund auch `whisper-1`, `tts-1` und
> `dall-e-3`: MSM reicht sie durch, wie OpenAI sie herausgibt, und behauptet
> nichts über Einträge, über die der Anbieter selbst nichts sagt.

Zweitens einen **zweiten Zugang** unter *Einstellungen → KI → Anbieter*:

| Feld | Wert |
|---|---|
| Anbieter | *ElevenLabs (Stimme)* |
| Schlüssel | ein ElevenLabs-Schlüssel von <https://elevenlabs.io/app/settings/api-keys> |
| Modell | `eleven_flash_v2_5` |
| Stimme | die Voice ID aus der Stimmenbibliothek, z. B. `21m00Tcm4TlvDq8ikWAM` |

Das ist ein eigener Schlüssel und eine eigene Rechnung. Der OpenRouter-Zugang
bleibt unberührt und bedient weiter den getippten Chat; die beiden Protokolle
sind getrennt, und kein Zugang kann versehentlich für das falsche verwendet
werden.

> **Die Stimme ist ein Textfeld und keine Auswahl.** Sie gehört dem Konto des
> Betreibers, MSM kennt sie nicht — und rät deshalb auch keine: es gibt keine
> Standardstimme. Ohne eingetragene Voice ID gibt es keinen Sprachmodus, denn
> eine geratene Stimme stünde auf seiner Rechnung. Geprüft wird beim Speichern
> nur die **Form** (Buchstaben, Ziffern, `-`, `_`), und zwar aus einem
> Sicherheitsgrund: die Kennung wird in einen URL-Pfad eingesetzt.

> **Warum Flash.** Rund 75 ms Rechenzeit, in Europa 100 bis 150 ms bis zum
> ersten Ton. Die höherwertigen Modelle klingen besser und kosten genau das, was
> ein Gespräch nicht hat. Die Auswahl kommt aus dem Katalog von ElevenLabs; für
> den Abruf braucht er — anders als OpenRouter — den Schlüssel, solange keiner
> hinterlegt ist bleibt die Modellliste beim Anlegen leer.

> **Warum überhaupt ein zweiter Anbieter — OpenRouter kann auch sprechen.**
> `POST /audio/speech` gibt es dort, und es liefert `pcm`, also genau das
> Format, das der Browser abspielt. Technisch wäre ElevenLabs entbehrlich.
> Drei Gründe sprechen dagegen, und wer den Zugang streichen will, muss alle
> drei entkräften: OpenRouters Stimmen sind überwiegend englisch und teurer;
> `/audio/speech` ist ein gewöhnlicher HTTP-Aufruf, der den **ganzen** Text
> braucht, bevor der erste Ton kommt, während ElevenLabs über WebSocket schon
> nach dem ersten Satz zu sprechen anfängt; und der zweite Zugang kostet nichts
> als einen Schlüssel.

> **Datenschutz.** Gesprochenes geht als Ton an den Chatanbieter, der Antworttext an
> ElevenLabs. Die EU-Datenresidenz von ElevenLabs ist Enterprise-Kunden
> vorbehalten; standardmässig routet der Dienst global. Das gehört in die
> Datenschutzerklärung des Betreibers.

**Ohne beide Zugänge gibt es keinen Sprachknopf.** Nicht ausgegraut, sondern gar
nicht — dieselbe Regel wie bei der Websuche, die ohne Schlüssel nicht einmal im
Werkzeugkatalog steht. „Beide" heisst dabei vollständig: ein Chatzugang ohne
hörendes Modell zählt so wenig wie ein ElevenLabs-Zugang ohne Stimme — und ein
Anbieter, der gar nicht zuhören kann (Azure), zählt ebenfalls nicht. Bei ihm
steht das Feld deshalb gar nicht erst im Formular.

### Recht und Grenzen

Recht: `ai.voice.use` (Gruppe *KI*). Bewusst getrennt von `ai.chat.use`: wer
spricht, bestätigt Änderungen per Stimme statt per Klick und verbraucht ein
Vielfaches. Ein Betreiber muss das abwählen können, ohne den Chat mitzunehmen.

Eine Sitzung endet nach **15 Minuten** von selbst; der Browser verbindet
automatisch neu. Das ist keine Vorsicht, sondern die Lebensdauer des
Access-Tokens: ein WebSocket prüft die Anmeldung nur beim Verbindungsaufbau, und
eine stundenlang offene Leitung umginge sowohl den Ablauf als auch die
Sperrliste abgemeldeter Sitzungen. Wer `MSM_ACCESS_TOKEN_EXPIRE_MINUTES` ändert,
ändert die Sitzungsdauer mit — sie ist daraus abgeleitet, nicht daneben
notiert.

### Bestätigen per Stimme

Soll etwas geändert werden, entsteht **derselbe Vorschlag wie im Chat** — hier
aber als Kasten ohne Knopf, der nur den Namen der Aktion nennt. Entschieden wird
gesprochen: die KI sagt, was sie vorhat, und fragt nach. Ein klares „Ja" führt
aus, ein klares „Nein" lässt es.

„Klar" heisst hier wörtlich: die Äusserung muss **nichts als** eine Zustimmung
sein. „Ja, aber schau vorher nochmal in die Logs" ist keine — das ist ein neuer
Auftrag, und als Zustimmung gelesen täte die KI das Gegenteil des Gesagten.

Das gesprochene Ja ersetzt genau einen Schritt: den Klick. Alles andere bleibt —
die Rechte werden beim Bestätigen erneut geprüft, beim Ausführen ein drittes
Mal, der Einmal-Token wird atomar entwertet, der Server-Mutex greift, das Audit
vermerkt den Vorgang.

> **Was die KI im Sprachmodus darf, darf der Sprechende auch.** Sie erbt seine
> Rechte und keines mehr — über seine Rolle oder als direkt zugewiesener
> Benutzer eines Servers. Der Lauf gehört ihm, und jede einzelne Prüfung läuft
> gegen ihn. Ein Vorschlag, der ihm nicht gehört, lässt sich per Stimme nicht
> auslösen; wird ihm der Server zwischen Frage und „Ja" entzogen, scheitert die
> Ausführung mit `AI_ACTION_ACCESS_REVOKED`, und das Gespräch läuft weiter.

Hier stand bis zum 16.08.2026, dass Löschen, Backup-Restore sowie Schlüssel und
Rollen per Stimme **nicht** bestätigbar sind — mit der Begründung, eine
gesprochene Zustimmung sei schwächer als ein Klick. Der Betreiber hat das
ausdrücklich anders entschieden: er will „lösch den Server" sagen, „ist das in
Ordnung?" hören und „ja" antworten können. Die Einschränkung ist deshalb
entfallen. Das Restrisiko bleibt beschreibbar und steht hier: im Audit steht
danach eine gesprochene Zustimmung, und wer im Raum mithört, kann sie
aussprechen. Wem das zu weit geht, nimmt `ai.voice.use` aus der Rolle.

**Rückfragen** funktionieren wie im Chat — dieselbe Logik, andere Ausgabe: statt
einer Karte mit Knöpfen liest die KI Frage und Möglichkeiten vor, und die
Antwort spricht man einfach.

**Logzeilen werden gezeigt, nicht vorgelesen.** Der Systemprompt verlangt vom
Modell ohnehin, die entscheidende Stelle als Codeblock zu zeigen und darunter zu
deuten. Im Gespräch erscheint der Block auf dem Bildschirm, gesprochen wird nur
die Deutung — ein Codeblock ist vorgelesen nichts als Satzzeichen.

### Kontingent

**Jede Äusserung sind zwei Buchungen.** Das ist der eine Punkt, an dem sich für
den Betreiber etwas geändert hat: wo eine Sprachsitzung früher **eine** Buchung
war, bucht jetzt jeder Zug zweimal — einmal die Abschrift des Gesprochenen,
einmal den Lauf selbst, beide über denselben Weg gezählt wie eine getippte
Nachricht. Ein Rollenlimit *Anfragen pro Minute* von fünf zerreisst damit ein
Gespräch, das vorher durchlief. Ohne gesetztes Limit passiert nichts.

Der Gewinn ist, dass Tokengrenzen und Kostengrenze den Sprachmodus genauso
binden wie den Chat: dieselbe Rechnung, dieselben vom Anbieter gemeldeten
Zahlen, derselbe gepflegte Rückfallpreis am Zugang. Die früheren Lücken — „die
Kostengrenze bindet den Sprachmodus überhaupt nicht" und „das Zuhören läuft an
allen Grenzen vorbei" — gibt es nicht mehr.

Eine Eigenheit hat die Abschrift-Buchung: sie erfolgt **nach** dem Hören, nicht
davor. Eine Reservierung vor dem Hören würfe die Äusserung weg, bevor irgendwer
weiss, was gesagt wurde — der Sprechende erführe nicht einmal, dass sein
Kontingent erschöpft ist. Deshalb wird erst gehört und dann gebucht; die
Buchung zählt trotzdem voll gegen Tages-, Wochen- und Monatsgrenzen, nur um
eine Äusserung versetzt. Ist das Kontingent erschöpft, endet der Zug dort, und
die Oberfläche sagt es als das, was es ist — „warte" statt „kaputt".

Einen Posten zählt MSM weiterhin **nicht** mit, und er steht trotzdem auf der
Rechnung des Betreibers:

- **Die Zeichen bei ElevenLabs.** Sie werden nach Zeichen abgerechnet und nicht
  nach Tokens; die Grenze dafür steht im Konto des Betreibers. Eine einzelne
  Antwort ist auf 4.000 Zeichen gedeckelt, damit ein Modell, das sich verrennt,
  kein ganzes Log verliest.

Wer den Sprachmodus hart deckeln will, tut das deshalb über *Anfragen pro
Minute* und über `ai.voice.use` — nicht über die Tokengrenze allein.

### Was technisch passiert

Der Ton läuft **durch das Panel** und nicht am Panel vorbei:

```
Browser ──WSS /api/ai/voice/ws──► MSM-Backend ──► Gehör · Chatlauf · Stimme
       (Cookie, Origin-Check)      (Betreiberschlüssel, Werkzeuge, RBAC)
```

Binärrahmen sind Ton (PCM16, 24 kHz, mono), Textrahmen sind Zustände,
Transkripte und gezeigte Stellen. Dasselbe Format in beide Richtungen — der
Browser nimmt so auf, wie er abspielt, und nichts wird unterwegs umgerechnet.
Das ist kein Zufall, sondern die Wahl des Ausgabeformats bei ElevenLabs
(`pcm_24000`).

Wann jemand aufgehört hat zu reden, entscheidet das **Backend** und nicht der
Browser (CLAUDE.md § 4). Das kostet Bandbreite während der Stille und ist
trotzdem richtig: die Grenzen einer Äusserung entscheiden, was als Frage an ein
Modell mit Werkzeugen geht — ein manipulierter Browser könnte sonst Tonstücke zu
einer Äusserung zusammensetzen, die so nie gesprochen wurde.

Eine Direktverbindung des Browsers zu den Anbietern wäre schneller. Dann liefe
die Werkzeugschleife aber über den Browser — er sähe jeden Werkzeugaufruf und
könnte welche erfinden. Der Umweg über das Panel kostet rund eine Viertelsekunde
und erspart zugleich die Ausgabe von Anbieterschlüsseln an den Browser: der
Betreiberschlüssel verlässt den Panelprozess nie.

Kein zusätzlicher Port, kein zusätzlicher Dienst. Der Reverse-Proxy muss
WebSocket-Upgrades unter `/api/` durchlassen — das tut er bereits für die
Server-Konsole.

**Permissions-Policy am Reverse-Proxy:** Die von `install.sh` erzeugte
Caddy-Site setzt `microphone=(self)` — die eigene Herkunft darf ans Mikrofon,
fremde iframes nicht. Installationen von vor dem 22.08.2026 tragen noch
`microphone=()`: damit blockiert der Browser `getUserMedia` vollständig
(es erscheint nicht einmal eine Freigabefrage, der Sprachmodus meldet nur
einen Verbindungsfehler). Wer nicht neu installieren will, ändert die Zeile
in der eigenen Caddy-Site von Hand auf `microphone=(self)` und lädt Caddy neu
(`systemctl reload caddy`).

---

## Kubernetes

Manifeste und Betriebsablauf liegen unter
[`deploy/kubernetes/`](../deploy/kubernetes/README.md).

**Wichtig zur Abgrenzung:** Kubernetes betreibt dort die **Control Plane**
(Panel, DIS-Sidecar, optional PostgreSQL). Gameserver laufen unverändert als
Docker-Container über den `msm-agent` auf angebundenen Nodes — sie werden
**nicht** zu Pods. Kapazität wächst weiterhin über weitere MSM-Nodes.

Zwei Punkte, die man nicht ändern sollte, ohne die Folgen zu kennen:

- Der **DIS-Sidecar läuft im selben Pod** wie das Panel. Er bindet nur an
  `127.0.0.1`; ein eigener Service würde die Krypto-Schnittstelle clusterweit
  erreichbar machen.
- **Genau eine Panel-Replica**, Rollout-Strategie `Recreate`. Scheduler-Jobs,
  Lifecycle-Sperren und der Settings-Cache liegen im Prozessspeicher — zwei
  gleichzeitige Panels würden Auto-Restarts, Backups und Webhook-Zustellungen
  doppelt ausführen. Dasselbe gilt für den KI-Laufvermittler und die
  KI-Meldestelle (Tipp-Signal, Live-Ströme): beide sind prozesslokal. Die
  Zustellung der Hintergrund-Aufträge verliert dadurch bei einem Neustart
  nichts — Meldungen stehen in der Datenbank, die Oberfläche pollt und lädt
  die persistierte Chat-Nachricht nach; nur das Live-Zusehen reisst kurz ab.

---

## Aktualität dieser Dokumentation

Änderungen an Bootstrap, `install.sh`, `update.sh`, Node-Enrollment,
`helper-scripts/migrate-panel-components.sh`, Release-Artefakten, Komponentenaufteilung oder
Environment-Verträgen müssen in demselben Commit sowohl diese Datei als auch die
sichtbare Panel-Seite `/docs/self-hosting` aktualisieren.
