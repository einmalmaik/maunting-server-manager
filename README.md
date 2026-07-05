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
4. Einen **SMTP-Server oder Resend-Account** (optional, für Email-Verifikation und 2FA)

---

## Installation (3 Schritte)

### Schritt 1: Auf den Server verbinden

Öffne ein Terminal (PowerShell auf Windows, Terminal auf Mac/Linux) und verbinde dich:

```bash
ssh root@DEINE-SERVER-IP
```

### Schritt 2: Repository klonen

```bash
cd /opt
git clone https://github.com/einmalmaik/maunting-server-manager.git msm
cd msm
```
**Wichtig (Prod):** Die Installations- und Server-Daten liegen unter `/opt/msm/servers`, `/opt/msm/backups` etc.
Führe auf dem Server **niemals** `git clean -fd` (oder ähnliche "aufräumen"-Befehle) im Clone-Verzeichnis aus – auch wenn Daten in Sub-Dirs liegen. Die `.gitignore` schützt die Laufzeit-Daten, aber manuelle Git-Clean-Befehle können trotzdem gefährlich sein. Immer erst mit `--dry-run` testen.

Es gibt ein Hilfsskript `scripts/reset-msm-docker.sh` für den (hoffentlich nie wieder auftretenden) Notfall.

**PS vom Entwickler (der das selbst mal um 2 Uhr nachts verbockt hat):** Ja, genau das ist mir passiert. Git clean hat den rootless Docker-Content-Store des msm-Users zerschossen (Blobs weg, "lease content" Horror). Nie wieder. Lernt aus meinen Schmerzen, Leute! 😂 Wenn's bei euch passiert: Es gibt jetzt `scripts/reset-msm-docker.sh` (als root ausführen). Danach git pull + Panel restart. Die .gitignore + Warnungen hier schützen euch (hoffentlich) davor.

### Schritt 3: Installer starten

```bash
sudo bash install.sh
```

Der Installer fragt dich nach:

1. **Domain** — gib deine Domain ein oder lasse sie leer für IP-Zugriff
2. **Email** — wähle Resend (API-Key) oder SMTP
3. **Datenbank** — PostgreSQL (empfohlen) oder SQLite
4. **Redis** — Ja/Nein für Rate-Limiting

**Das war's.** Der Rest läuft automatisch.

MSM richtet Docker im Rootless-Modus für den `msm`-User ein. Der Panel-User
ist nicht Mitglied der globalen `docker`-Gruppe und nutzt
`unix:///run/user/<msm_uid>/docker.sock` statt `/var/run/docker.sock`.

---

## Nach der Installation

1. Öffne die Panel-URL im Browser (steht im Installer am Ende)
2. Folge dem **Setup-Wizard** (erfordert eine gültige Email-Adresse)
3. Erstelle deinen ersten **Owner-Account**
4. Lege deinen ersten **Game-Server** an

---

## Update (neue Version installieren)

### Manuell

```bash
sudo bash /opt/msm/update.sh
```

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
│  → SQLite oder PostgreSQL                 │
│  → Redis (optional)                       │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│  Game-Server (systemd)                    │
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
