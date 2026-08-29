![Status](https://img.shields.io/badge/Status-WIP-orange)

> [!IMPORTANT]
> Dieses Panel befindet sich in aktiver Entwicklung. Der Einsatz in produktiven Umgebungen ohne vorheriges Backup wird nicht empfohlen.

# Maunting Service Manager (MSM)

Maunting Service Manager (MSM) ist ein selbstgehostetes Web-Panel zur zentralen Steuerung von Game-Servern, Anwendungen und Linux-Workloads auf eigenen Servern oder VPS, ohne Notwendigkeit täglicher SSH-Eingaben.

---

## Was ist MSM?

MSM trennt die Benutzeroberfläche (Control Plane) von den eigentlichen Ausführungsservern (Nodes). Sämtliche Anwendungen und Game-Server laufen isoliert in Rootless-Docker-Containern.

### Anwendungsbereiche
- **Game-Server**: Verwaltung, Start, Stopp, Neustart und automatische Portvergabe für unterstützte Titel (z. B. Conan Exiles, DayZ, Minecraft, ARK).
- **Linux-Anwendungen & Workloads**: Bereitstellung generischer Serverdienste, Datenbanken oder Web-Tools über das flexible Blueprint-System.
- **Multi-Node-Betrieb**: Steuerung mehrerer physischer Server oder VPS über ein einziges Dashboard.

### Abgrenzung
- Kein Game-Server-Hosting-Anbieter: MSM setzt eigene Linux-Server voraus (z. B. bei Hetzner, OVH, netcup oder eigener Hardware).
- Kein Windows-Tool: Die Control Plane und die Nodes setzen ein Linux-Betriebssystem voraus.
- Kein Ersatz für Root-Rechte bei der Erstinstallation: Der Bootstrapper benötigt einmalig Root-Rechte zur Systemeinrichtung. Im regulären Betrieb laufen Panel und Container unprivilegiert.

---

## Kernfunktionen

### 1. Multinode System (Multi-Node-Architektur)
Eine zentrale Control Plane steuert beliebig viele Nodes. Neue Nodes werden über ein mTLS-Verfahren mit HMAC-Challenge und expliziter Bestätigung durch den Administrator eingebunden.

### 2. Guardian Engine (Autonomes Self-Healing)
Auf jedem Node läuft die Guardian Engine als lokaler Hintergrunddienst. Sie überwacht Container-Zustände sowie HTTP-, TCP- und Regex-Probes. Bei Ausfällen führt der Node selbstständig definierte Recovery-Aktionen (z. B. Container-Neustart oder Quarantäne) durch, auch wenn die zentrale Control Plane offline oder nicht erreichbar ist. Incidents und Statusänderungen werden lokal protokolliert und synchronisiert, sobald die Verbindung wieder steht.

### 3. Blueprint Integration
Anwendungen werden nicht über starre Skripte, sondern über deklarative Blueprint-Dateien (YAML/JSON) definiert. Ein Blueprint legt Umgebungsvariablen, Ports, Docker-Images, Lautstärken-Mounts, Konfigurations-Templates und Guardian-Healthchecks fest. MSM ist dadurch nicht auf Game-Server beschränkt.

### 4. Steam Workshop Integration
Integrierte Mod- und Workshop-Verwaltung für unterstützte Spiele. Der Agent lädt Workshop-Objekte über SteamCMD direkt auf den Node herunter, aktualisiert diese und bindet die Pfade in die Container-Struktur ein.

### 5. Rootless Docker Isolation
Sämtliche Container laufen über den Rootless-Docker-Daemon des unprivilegierten `msm`-Benutzers (`unix:///run/user/<uid>/docker.sock`). Der Panel-Benutzer besitzt keine Mitgliedschaft in der globalen `docker`-Gruppe. Game-Server-Ports liegen oberhalb von 1024, wodurch keine Root-Rechte oder `setcap`-Rechteerweiterungen erforderlich sind.

### 6. Zero-Knowledge Backups via DIS (`@msdis/shield`)
Verschlüsselung von Server- und Datenbank-Backups über den DIS Cryptographic Shield. Daten werden lokal mit AES-256-GCM und Argon2id verschlüsselt, bevor ein Streaming-Upload zu S3-kompatiblem Object Storage erfolgt. Schlüssel und S3-Zugangsdaten liegen niemals im Klartext vor.

### 7. Komponenten-Migration (`migrate-panel-components.sh`)
Integrierter CLI-Assistent zum Verschieben von Control Plane, externem Frontend oder einzelnen Server-Instanzen zwischen Nodes inklusive atomarem Cutover und Rollback-Schutz.

### 8. Hoster- und Shop-Anbindung (optional)
Ein externer Shop kann Server über eine idempotente Desired-State-API bestellen, sperren und kündigen. Die Anbindung verwendet dieselbe Provisionierungs- und Lifecycle-Logik wie das Panel: Es gibt keinen zweiten Weg, einen Server anzulegen. Kunden gelangen über einen signierten Einmal-Link direkt ins Panel und benötigen kein zweites Passwort. **Ohne angelegte Integration ändert sich am Self-Hosted-Betrieb nichts.** Einrichtung und Betrieb in [`docs/self-hosting.md`](docs/self-hosting.md#hoster--und-shop-anbindung-optional-phase-6), die vollständige Endpunkt-, Webhook- und Signaturreferenz in [`docs/hoster-api.md`](docs/hoster-api.md) (im Panel auch unter **Hilfe → Hoster-API**).

### 9. Sprachmodus (optional)
Der Betreiber kann zwischen zwei Wegen wählen. Ohne OpenAI Realtime bleibt der bestehende Ablauf aus Transkription, Chatmodell, Pipecat und ElevenLabs unverändert. Ein panelweit aktivierter OpenAI-Realtime-Zugang führt Sprache dagegen direkt per WebRTC zwischen Browser beziehungsweise Desktop-App und OpenAI; das Backend hält über einen Sideband-Kanal Werkzeuge, RBAC, Guardian, Worker und Abrechnung unter Kontrolle. API-Schlüssel erreichen den Client nie. Der Realtime-Weg speichert keine Abschriften oder gesprochenen Antworten im Chat und fällt bei einem Fehler nicht still auf ElevenLabs zurück. Einrichtung und Netzwerkvoraussetzungen stehen in [`docs/self-hosting.md`](docs/self-hosting.md#sprachmodus-mit-der-ki-reden).

### 10. Getrennte Zugangsdaten und Kubernetes
GitHub-Token und Steam-Konten können panelweit, pro Benutzer oder pro Server hinterlegt werden. Ein Server verweist auf Zugangsdaten, statt deren Werte zu kopieren. Der Klartext ist nach dem Speichern nicht mehr auslesbar. Der Betreiber entscheidet, ob ein Server ohne eigene Zuordnung den zentralen Zugang nutzen darf. Für den Cluster-Betrieb liegen Kubernetes-Manifeste unter [`deploy/kubernetes/`](deploy/kubernetes/README.md) bereit (sie betreiben die Control Plane; Gameserver bleiben Docker-Container auf den angebundenen Nodes). **Der Standard-Self-Hosted-Betrieb funktioniert ohne beides.**

---

## Vergleich: MSM vs. Pelican Panel vs. Klassische Panels

Die folgende Tabelle vergleicht verifizierte technische Eigenschaften von MSM mit **Pelican Panel** (dem modernen Nachfolger von Pterodactyl) und **Klassischen Panels** (wie Pterodactyl v1 oder AMP).

| Eigenschaft / Funktion | Maunting Service Manager (MSM) | Pelican Panel | Klassische Panels (z. B. Pterodactyl v1, AMP) |
|---|---|---|---|
| **Architektur** | Central Control Plane + Multi-Node (Multinode System) | Panel + Node-Architektur (Wings) | Monolithisch oder Panel + Daemon (Wings/AMP Instance) |
| **Container-Sicherheit** | Standardmäßig Rootless Docker pro Node-User (`unix:///run/user/...`) | Standardmäßig privilegierter Root-Docker-Daemon | Standardmäßig privilegierter Root-Docker-Daemon |
| **Autonomes Self-Healing** | **Ja (Guardian Engine)**: Lokale Probes und Recovery auf dem Agenten, voll funktionsfähig auch bei Ausfall der Control Plane | **Nein**: Statusüberwachung hängt an Panel-Verbindung und Daemon-Heartbeats | **Nein**: Daemon führt Befehle der zentralen Steuerung aus |
| **Anwendungs-Deklaration** | **Blueprints (YAML/JSON)**: Flexible Schemas für Game-Server, Web-Apps, Datenbanken & Custom Guardian Probes | **Egg-System**: JSON-Templates für Pterodactyl/Pelican-Container | **Egg-System / Feste Module**: Spezifische Skripte oder fest verdrahtete Anwendungsmodule |
| **Steam Workshop Manager** | **Nativ im Agenten**: Automatische Downloads, Updates und Struktur-Mapping via SteamCMD | **Teilweise**: Abhängig von Community-Eggs oder externen Zusatzskripten | **Teilweise**: Über Community-Addons oder manuelle Skripte |
| **Backup-Verschlüsselung** | **Zero-Knowledge (DIS)**: Clientseitige AES-256-GCM + Argon2id Verschlüsselung vor S3-Streaming | **Standard S3**: Unverschlüsselte Uploads oder providerseitige S3-Verschlüsselung | **Standard S3 / Lokal**: Unverschlüsseltes Tar/Zip auf S3 oder lokaler Speicher |
| **Komponenten-Migration** | **Ja**: Interaktiver Assistent (`migrate-panel-components.sh`) für Cutover von Frontend, Servern & Control Plane | **Manuell**: CLI-Befehle, manuelle Dateiverlagerung und Datenbankanpassungen | **Manuell**: SSH-Kopiervorgänge, Dumps und manuelle Pfadkorrekturen |
| **Installation & HTTPS** | Ein-Befehl-Bootstrap mit Caddy Auto-HTTPS (Let's Encrypt) & PostgreSQL | CLI-Installer oder Docker-Compose-Setup | Manuelle Webserver- (Nginx/Apache) und Datenbank-Einrichtung oder Skripte |

---

## Systemanforderungen & Betriebssysteme

### Hardware & Netzwerk
1. **Root-Zugang**: SSH-Zugriff mit Root-Rechten für die Erstinstallation.
2. **Domain**: FQDN (z. B. `panel.example.com`) mit A/AAAA-Record auf die Server-IP für automatische HTTPS-Zertifikate.
3. **Hardware**: Mindestens 2 CPU-Kerne, 2 GB RAM für die Control Plane. Ressourcen für Game-Server kommen hinzu.

### Betriebssystem-Kompatibilität

MSM erfordert ein Linux-Betriebssystem mit Systemd und Docker-Unterstützung.

| Betriebssystem / Distribution | Status | Anmerkung |
|---|---|---|
| **Ubuntu 24.04.4 LTS** | 🟢 **Offiziell unterstützt** | Haupt-Entwicklungs- und primäres Testsystem |
| **Ubuntu 22.04 LTS** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Debian 12 (Bookworm)** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Debian 11 (Bullseye)** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **AlmaLinux 9** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Rocky Linux 9** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Fedora Server (40+)** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Arch Linux** | 🟡 **Unsicher** | Bisher ungetestet, noch keine Community-Rückmeldung vorliegend |
| **Alpine Linux** | 🔴 **Funktioniert nicht** | Inkompatibel (kein Standard-Systemd, glibc-Abweichungen) |
| **Windows / Windows Server** | 🔴 **Funktioniert nicht** | Inkompatibel (setzt nativen Linux-Kernel & Systemd voraus) |

**Status-Kategorien:**
- 🟢 **Offiziell unterstützt**: Auf diesem Betriebssystem (Ubuntu 24.04.4 LTS) wird MSM entwickelt, aktiv gepflegt und getestet.
- 🟡 **Unsicher**: Noch nicht vom Entwickler oder der Community getestet (Status offen, Rückmeldungen willkommen).
- 🔴 **Funktioniert nicht**: Aus architektonischen Gründen inkompatibel oder nicht unterstützt.

---

## Installation

### Erstinstallation per Bootstrap

Verbinde dich per SSH auf deinen Server:

```bash
ssh root@DEINE-SERVER-IP
```

Führe den Installationsbefehl aus:

```bash
curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh | sudo bash -s -- --domain panel.example.com
```

Ersetze `panel.example.com` durch deine eigene Domain. 

Der Installer richtet automatisch folgende Komponenten ein:
- PostgreSQL-Datenbank und Redis-Cache
- Rootless Docker für den `msm`-Benutzer
- DIS-Cryptographic-Sidecar
- Lokaler Node-Agent und Guardian Engine
- Caddy Webserver mit automatischem HTTPS-Zertifikat
- Systemd-Dienste und Aktualisierungstimer

### Nach der Installation

1. Rufe die angezeigte Panel-URL im Browser auf.
2. Schließe den Ersteinrichtungs-Assistenten ab.
3. Erstelle das Administrator-Konto (Owner).
4. Erstelle den ersten Server oder binde weitere Nodes ein.

---

## Architektur

```
┌─────────────────────────────────────────┐
│  Browser (HTTPS)                        │
│  → panel.example.com                    │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  Caddy Reverse-Proxy (TLS Auto)         │
│  → Port 80 / 443                        │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  FastAPI Backend (Python)               │
│  → Port 8000 (Localhost)                │
│  → PostgreSQL (Loopback)                │
│  → DIS Sidecar (@msdis/shield)          │
└────────────┬────────────────────────────┘
             │ (mTLS / HMAC Enrollment)
┌────────────▼────────────────────────────┐
│  Node Agent + Guardian Engine           │
│  → Rootless Docker Socket               │
│  → Autonomes Monitoring & Recovery      │
│  → Pro Server isolierter Container-User │
└─────────────────────────────────────────┘
```

---

## Sicherheit

- **HTTPS**: Automatische Zertifikate von Let's Encrypt via Caddy Proxy.
- **Netzwerk-Isolation**: UFW-Firewall-Regeln beschränken Zugriffe auf SSH (22), Web (80/443) und definierte Game-Ports.
- **Fail2ban**: Schutz vor Brute-Force-Angriffen auf SSH und Panel-Endpunkte.
- **Authentifizierung**: JWT mit kurzlebigen Access-Tokens (15 Min.) und Refresh-Tokens (30 Tage).
- **Zwei-Faktor-Authentifizierung (2FA)**: TOTP mit Wiederherstellungscodes.
- **Container-Isolation**: Rootless Docker ohne globale Root-Rechte.
- **Ressourcenbegrenzung**: CPU-, Arbeitsspeicher- und Disk-Limits pro Container konfigurierbar.

---

## Update & Wartung

### Manuelles Update

```bash
sudo bash /opt/msm/update.sh
```

Der Updater erstellt vor Schema-Änderungen einen PostgreSQL-Dump, versetzt das Panel kurzzeitig in den Wartungsmodus und prüft die Erreichbarkeit der Dienste. Laufende Game-Server auf den Nodes werden während des Updates nicht unterbrochen.

### Automatisches Update (Optional)

In der Konfigurationsdatei `/opt/msm/backend/.env` aktivieren:

```env
MSM_AUTO_UPDATE=true
```

Anschließend den Systemd-Timer starten:

```bash
sudo systemctl start msm-update.timer
```

---

## Wichtige Befehle

| Befehl | Zweck |
|--------|-------|
| `sudo systemctl status msm-panel` | Status des Panel-Dienstes anzeigen |
| `sudo systemctl restart msm-panel` | Panel neu starten |
| `sudo journalctl -u msm-panel -f` | Live-Logs des Backend-Dienstes verfolgen |
| `sudo bash /opt/msm/update.sh --check-only` | Verfügbare Updates prüfen |
| `sudo /opt/msm/helper-scripts/migrate-panel-components.sh` | Komponenten-Migrationsassistent starten |

---

## Ports

| Port / Range | Protokoll | Zweck |
|--------------|-----------|-------|
| 80 | TCP | HTTP (Weiterleitung auf HTTPS) |
| 443 | TCP | HTTPS (Panel-Webinterface) |
| 27015-27999 | UDP/TCP | Game-Server-Ports (automatische Vergabe ab Port 1024) |

---

## Dokumentation & Support

- **Dokumentation**: Ausführliche Anleitungen stehen in [`docs/self-hosting.md`](docs/self-hosting.md) sowie direkt im Panel unter **Dokumentation**.
- **Issue Tracker**: [GitHub Issues](https://github.com/einmalmaik/maunting-server-manager/issues)

---

## Discord Rich Presence (Optional)

Die Desktop-App (*Maunting Smart System* / MSS) unterstützt Discord Rich Presence (RPC). Wenn Discord auf Ihrem Rechner läuft, wird Ihr Status im Discord-Profil angezeigt (Standard: „Security needs trust“ / „Sicherheit braucht Vertrauen“).

- **Lokale Verbindung**: Die Kommunikation erfolgt rein lokal über die Windows Named Pipe (`\\.\pipe\discord-ipc-0`). Es werden keine externen Anfragen an Discord-Server gesendet und keine Server-Adressen, Kennwörter oder Chat-Inhalte übertragen.
- **Standard**: Die Standard-Anwendungs-ID (`1512525013155057735`) ist fest hinterlegt.
- **Texte und Application-ID anpassen**: Sie können die Discord-Texte und die Client-ID nach eigenen Wünschen anpassen. Entweder über die `konfig.json` der Desktop-App:
  ```json
  {
    "discord_rpc_aktiv": true,
    "discord_client_id": "DEINE_APPLICATION_ID",
    "discord_details": "Eigener Statustext Zeile 1",
    "discord_state": "Eigener Statustext Zeile 2"
  }
  ```
  Oder direkt im Quellcode in [`smart-system/src-tauri/src/discord.rs`](smart-system/src-tauri/src/discord.rs).
- **Deaktivieren**: Rich Presence lässt sich in der `konfig.json` der Desktop-App mit `"discord_rpc_aktiv": false` jederzeit vollständig abschalten.

---

## Lizenz

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE).
