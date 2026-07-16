![Status](https://img.shields.io/badge/Status-WIP-orange)
> [!IMPORTANT]
>
>This panel is a work in progress. Production use is not recommended.


# Maunting Server Manager

> Ein Panel, um Server jeder art auf deinem eigenen Linux-Server zu verwalten — ganz ohne Kommandozeile.

---

## Was ist das? 

Der **Maunting Server Manager** ist ein Web-Panel. Du öffnest es im Browser und kannst damit:

- Game-Server installieren, starten, stoppen und neustarten
- Ports automatisch vergeben lassen (keine Kollisionen)
- CPU/RAM/Disk-Limits pro Server setzen
- Backups erstellen und wiederherstellen
- Mods verwalten
- Mehrere Benutzer mit unterschiedlichen Rechten anlegen
- 2FA und Email-Verifikation nutzen

## Was ist das NICHT?

- Kein Game-Server-Hosting-Anbieter (du brauchst einen eigenen Linux-Server, z.B. bei Hetzner, OVH, Strato)
- Kein Windows-Tool (läuft nur auf Linux — Ubuntu 22.04+ oder Debian 12+)
- Kein Ersatz für SteamCMD-Kenntnisse (das Panel nutzt SteamCMD im Hintergrund, du musst es nicht bedienen)
- Kein kostenloser Root-Server (du musst den Server selbst mieten)

---

## Voraussetzungen

Bevor du loslegst, brauchst du:

1. Einen **Linux-Server** (Ubuntu 22.04 oder Debian 12 empfohlen)
2. **Root-Zugang** (SSH-Key oder Passwort)
3. Eine **Domain** (optional, aber empfohlen für HTTPS)
4. Einen **Resend-Account** für die einfache Browser-Einrichtung (SMTP bleibt über den klassischen Installer möglich)

---

## Installation

### Schritt 1: Auf den Server verbinden

Öffne ein Terminal (PowerShell auf Windows, Terminal auf Mac/Linux) und verbinde dich:

```bash
ssh root@DEINE-SERVER-IP
```

### Schritt 2: Ein Befehl

```bash
curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh | sudo bash -s -- --domain panel.example.com
```

Ersetze `panel.example.com` durch die Domain, deren DNS bereits auf den Server
zeigt. Der Befehl fragt keine weiteren technischen Einstellungen ab.

**Das war's.** PostgreSQL, Rootless Docker, DIS, der lokale Agent, Caddy und die
Systemdienste werden automatisch eingerichtet. Eine vorhandene Legacy-SQLite-
Datenbank wird vor dem Update gesichert, einmalig geprüft nach PostgreSQL
importiert und anschließend als Migrationsarchiv behalten. Erkennt der
Bootstrap eine vorhandene Installation, verwendet er automatisch den sicheren
Updater; Serverdaten, Backups, Agent-Tokens und Konfigurationen bleiben erhalten.

MSM richtet Docker im Rootless-Modus für den `msm`-User ein. Der Panel-User
ist nicht Mitglied der globalen `docker`-Gruppe und nutzt
`unix:///run/user/<msm_uid>/docker.sock` statt `/var/run/docker.sock`.

---

## Nach der Installation

1. Öffne die Panel-URL im Browser (steht im Installer am Ende)
2. Hinterlege im **Setup-Wizard** Absender und Resend-API-Key
3. Erstelle den ersten **Owner-Account** und bestätige die E-Mail
4. Lege deinen ersten **Game-Server** an

---

## Umgebungsvariablen

Für Self-Hosting und manuelle Installationen enthält jede Komponente eine
vollständige, kommentierte Vorlage. Bei jeder Variable steht, was sie macht,
ob sie automatisch erzeugt wird, wann sie geändert werden muss und wo ein
externer Wert bezogen wird:

- [`backend/.env.example`](backend/.env.example) — Panel, Panel-Datenbank, E-Mail, DIS, Steam/GitHub und Updates
- [`msm-agent/.env.example`](msm-agent/.env.example) — lokaler oder entfernter Node, TLS, Docker und node-eigenes PostgreSQL
- [`frontend/.env.example`](frontend/.env.example) — ausschließlich öffentliche Build-Werte, niemals Secrets
- [`dis-sidecar/.env.example`](dis-sidecar/.env.example) — lokale Kryptografie-Secrets, identisch zum Backend

Der normale Installer generiert alle sicherheitskritischen Werte und verweist
in den erzeugten `.env`-Dateien auf die jeweilige Vorlage. Manuell angelegte
Dateien müssen Modus `600` erhalten und dürfen niemals committed werden.

Die vollständige Komponenten-, Release- und Node-Anleitung steht in
[`docs/self-hosting.md`](docs/self-hosting.md) und nach der Anmeldung im Panel
unter **Dokumentation → Self-Hosting & Nodes**.

---

## Update (neue Version installieren)

### Manuell

```bash
sudo bash /opt/msm/update.sh
```

Der Updater erstellt vor Schemaänderungen einen verifizierten PostgreSQL-Dump
(bei Altinstallationen eine bytegenau geprüfte SQLite-Sicherung), nimmt nur das
Panel kurz in Wartung und meldet erst nach Agent- und Backend-Healthchecks
Erfolg. Laufende Game-Server auf den Nodes werden nicht gestoppt.

### Automatisch (optional)

Aktiviere Auto-Update in der Konfiguration:

```bash
# Bearbeite die .env-Datei:
nano /opt/msm/backend/.env

# Setze:
MSM_AUTO_UPDATE=true

# Starte den Timer:
sudo systemctl start msm-update.timer
```

---

## Wichtige Befehle

| Befehl | Was er tut |
|--------|-----------|
| `sudo systemctl status msm-panel` | Zeigt ob das Panel läuft |
| `sudo systemctl restart msm-panel` | Startet das Panel neu |
| `sudo journalctl -u msm-panel -f` | Zeigt Live-Logs |
| `sudo bash /opt/msm/update.sh --check-only` | Prüft ob ein Update verfügbar ist |
| `sudo ufw status` | Zeigt Firewall-Regeln |

---

## Ports

Das Panel nutzt folgende Ports:

| Port | Protokoll | Zweck |
|------|-----------|-------|
| 80 | TCP | HTTP (wird zu HTTPS weitergeleitet) |
| 443 | TCP | HTTPS (Panel-Webinterface) |
| 27015-27999 | UDP/TCP | Game-Server (automatisch vergeben) |

Game-Server-Ports müssen über `1024` liegen. Rootless Docker bindet keine
privilegierten Ports; MSM setzt dafür bewusst keinen `setcap`-Workaround.

Bei einer Re-Installation stoppt der Installer alte rootful MSM-Container
(`msm-srv-*`) und weist auf die Rootless-Migration hin. Die alten Container
werden nicht automatisch gelöscht.

Die Game-Server-Ports werden **automatisch** aus der Range 27015-27999 vergeben. Du musst nichts manuell einstellen.

---

## Architektur

```
┌─────────────────────────────────────────┐
│  Browser (HTTPS)                        │
│  → panel.deinserver.de                  │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  Caddy (Reverse-Proxy + TLS)            │
│  → Port 443                             │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  FastAPI Backend (Python)                 │
│  → Port 8000 (nur localhost)              │
│  → PostgreSQL (nur Loopback)              │
│  → Redis (optional)                       │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  Local Node Agent + Rootless Docker       │
│  → Conan Exiles UE5 (Linux native)      │
│  → DayZ (Linux native)                    │
│  → Jeder Server eigener Linux-User        │
└─────────────────────────────────────────┘
```

---

## Sicherheit

- **HTTPS** via Caddy (automatische Zertifikate von Let's Encrypt)
- **Firewall** (UFW) — nur Ports 22, 80, 443 und Game-Range offen
- **Fail2ban** — blockiert Brute-Force auf SSH und Panel
- **JWT-Auth** mit kurzlebigen Tokens (15 Min) + Refresh (30 Tage)
- **CSRF-Schutz** für alle state-changing Requests
- **Rate-Limiting** — 10/min für Auth, 100/min für alles andere
- **2FA** via TOTP + Backup-Codes
- **Email-Verifikation** für Setup und neue Accounts
- **Resource-Limits** — CPU/RAM/Disk pro Game-Server begrenzbar

---

## Backup-System

MSM besitzt ein vollständiges Backup-System mit lokalen Backups und verschlüsseltem Off-Site-Upload zu S3-kompatiblem Object Storage. Alle kryptografischen Operationen laufen über den **DIS Sidecar** (`@msdis/shield`) mit AES-256-GCM und Argon2id-Key-Derivation. Das Panel selbst implementiert keine eigene Kryptografie (Zero-Knowledge-Prinzip).

### Überblick

- **Server-Backups:** lokales `tar.gz` pro Game-Server + verschlüsselter Streaming-Upload zu S3
- **Panel-Backups:** `pg_dump` der MSM-Datenbank + Konfigurationsdateien (`.env`, `install.sh` etc.), verschlüsselter S3-Upload
- **Local + S3-Mirror:** lokale Backups für schnelle Recovery, S3 für verschlüsselten Off-Site-Schutz
- **Best-Effort S3:** schlägt der S3-Upload fehl, bleibt das lokale Backup erhalten und blockiert nicht
- **Retention:** konfigurierbare Aufbewahrung für lokale und S3-Backups (Server und Panel separat)
- **Scheduler:** automatische Server- und Panel-Backups inklusive S3-Upload

### S3-Konfiguration

In den **Einstellungen → Backup** kannst du S3-kompatiblen Object Storage einrichten:

- **S3-Endpoint** (z.B. MinIO, Hetzner S3, AWS S3)
- **Access Key & Secret Key** — verschlüsselt via DIS mit AAD-Domain-Separation gespeichert
- **Bucket & Region**

S3-Credentials und das Backup-Passwort werden nie im Klartext gespeichert. Sie liegen verschlüsselt in den `panel_settings` und sind nur über den DIS Sidecar entschlüsselbar.

### Backup-Passwort

Das Backup-Passwort wird für die Verschlüsselung der S3-Uploads verwendet. Es wird ebenfalls via DIS verschlüsselt in den `panel_settings` abgelegt. Ohne DIS Sidecar und das gespeicherte Passwort lassen sich S3-Backups nicht entschlüsseln.

### Server-Backups vs. Panel-Backups

| | Server-Backup | Panel-Backup |
|---|--------------|--------------|
| **Inhalt** | Game-Server-Daten (`tar.gz`) | MSM-Datenbank (`pg_dump`) + Konfigurationsdateien |
| **Lokal** | ja | ja |
| **S3-Upload** | ja (verschlüsselt) | ja (verschlüsselt) |
| **Retention** | konfigurierbar | konfigurierbar |
| **Automatisch** | ja (Scheduler) | ja (Scheduler) |

Für bestehende lokale Server-Backups gibt es zudem eine **Upload-to-Cloud**-Funktion, um ältere Backups nachträglich verschlüsselt zu S3 hochzuladen.

### Restore-Optionen

**Server-Backups:**
- Wiederherstellung aus lokalem Backup
- Wiederherstellung aus S3 (Download + Entschlüsselung + Restore)

**Panel-Backups:**
- Restore startet über die **PanelBackups**-Seite (Prepare-Restore)
- Das Panel generiert ein Bash-Restore-Skript, das folgendes ausführt:
  1. Panel stoppen
  2. Datenbank aus `pg_dump` wiederherstellen
  3. Konfigurationsdateien zurückspielen
  4. Panel neu starten

### Frontend

- **Einstellungen → Backup-Tab:** S3-Konfiguration und Backup-Passwort verwalten
- **Backups-Seite:** S3-Status-Badges und Cloud-Icons pro Backup
- **PanelBackups-Seite:** Panel-Backups erstellen, löschen, Restore vorbereiten und Einstellungen verwalten

---

## Auto-Update

Wenn du Auto-Update aktiviert hast, passiert folgendes:

1. Ein systemd-Timer prüft alle 24h GitHub Releases
2. Wenn eine neue Version verfügbar ist, wird sie automatisch installiert
3. **Vor dem Update** wird ein Backup erstellt
4. **Bei Fehlern** wird automatisch zum alten Stand zurückgerollt
5. Der Admin sieht im Dashboard, dass ein Update verfügbar ist

---

## Tauri (Desktop-App)

Für die Zukunft ist eine Tauri-Desktop-App geplant. Sie nutzt denselben GitHub-Release-Feed für Updates.

---

## Hilfe & Support

- **GitHub Issues**: [github.com/einmalmaik/maunting-server-manager/issues](https://github.com/einmalmaik/maunting-server-manager/issues)
- **Logs prüfen**: `sudo journalctl -u msm-panel -n 100`
- **Update manuell**: `sudo bash /opt/msm/update.sh`

---

## Lizenz

MIT License — siehe [LICENSE](LICENSE)
