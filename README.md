# Warning! 

>This panel is a work in progress. Production use is not recommended.
Release planned for June 10, 2026.


# Maunting Server Manager

> Ein Panel, um Conan Exiles und DayZ Server auf deinem eigenen Linux-Server zu verwalten — ganz ohne Kommandozeile.

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
