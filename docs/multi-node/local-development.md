# Local Development & Testing Guide (Multi-Node Dev)

Dieser Guide beschreibt, wie das MSM-Gesamtsystem lokal auf einem Entwicklungsrechner (z.B. Windows mit WSL2) gestartet und getestet wird.

---

## Der einfachste Weg: Automatische Start-Skripte (Windows)

Im Repository-Root befindet sich das Skript `start-dev.bat`.

### Normaler Dev-Start

`start-dev.bat` startet den lokalen Single-Node-Entwicklungsmodus.

**Was macht das Skript?**
1. **Docker & Postgres**: Prüft, ob Docker läuft, und startet/erstellt den dedizierten Panel-PostgreSQL-Dev-Container (`msm-panel-postgres-dev` auf `127.0.0.1:15434`).
2. **Dependencies prüfen**: Installiert automatisch alle fehlenden Node-Module (im `frontend/` und `dis-sidecar/`) sowie die Python-Requirements im Backend (erstellt das `venv`, falls nicht vorhanden).
3. **Agent vorbereiten**: Erzeugt ein zufälliges lokales Agent-Token in der gitignorierten `msm-agent/.env`, konfiguriert unter Windows die Docker-Desktop-Named-Pipe und synchronisiert das Token beim Backend-Start mit dem lokalen Node.
4. **Start**: Öffnet separate Terminalfenster und startet **DIS Sidecar**, **FastAPI Backend**, **MSM Agent** und **React Frontend**.

**Anwendung**:
Doppelklicke einfach auf `start-dev.bat` im Root-Verzeichnis deines Projekts. Sobald die Einrichtung abgeschlossen ist, öffnen sich die Fenster und das System läuft.

- **Frontend**: [http://localhost:3000](http://localhost:3000)
- **Backend API**: [http://localhost:8000](http://localhost:8000)
- **DIS Sidecar**: [http://localhost:9100](http://localhost:9100)
- **MSM Agent**: [http://localhost:9000/health](http://localhost:9000/health)

Der lokale Node ist nur dann `online`, wenn Docker Desktop läuft. Der Agent-Healthcheck
meldet absichtlich `503`, wenn Docker nicht erreichbar ist.

### Vollständige Multi-Node-Simulation

Für Multi-Node- und Backup-Tests doppelklicke stattdessen:

```text
start-dev-multi-node.bat
```

Dieser Modus startet zusätzlich:

| Dienst | Adresse | Zweck |
|---|---|---|
| Simulierter Remote-Agent | `https://127.0.0.1:9001` | TLS- und NodeClient-Kommunikation |
| MinIO S3 API | `http://127.0.0.1:9002` | Lokales S3-kompatibles Backup-Ziel |
| MinIO Console | `http://127.0.0.1:9003` | Bucket und Objekte kontrollieren |

Lokale, nicht commitbare Testdaten:

- Token des lokalen Agents: `msm-agent/.env`
- Token des simulierten Remote-Agents: `msm-agent/.dev/node-2/.env`
- TLS-Fingerprint: `msm-agent/.dev/node-2-fingerprint.txt`
- MinIO-Zugangsdaten: `msm-agent/.dev/minio.env`

Die Skripte geben Tokens und S3-Secrets nicht im Terminal aus.

Nach dem Start prüft der sichere Smoke-Test beide Agents, Authentifizierung,
Docker, Dateigrenzen, Portprüfung, TLS und MinIO. Der verwendete Alpine-Container
und das Testverzeichnis werden immer wieder entfernt. Der Bucket `msm-backups`
wird angelegt, falls er noch nicht existiert. Zusätzlich führt der Test einen
vollständigen Agent→MinIO-Backup/Restore-Roundtrip aus, prüft die Verschlüsselung
und vergleicht den wiederhergestellten Dateiinhalt:

```powershell
msm-agent\venv\Scripts\python.exe scripts\test-local-multi-node.py
```

### Remote-Node im Panel anlegen

1. Öffne `http://localhost:3000/admin/nodes` als Owner und klicke auf
   `Node hinzufügen`.
2. Für einen echten zweiten Linux-Rechner kopierst du den angezeigten Befehl
   auf diesen Rechner. Danach bestätigst du im Panel nur noch den passenden
   kurzen Code.
3. Der lokale simulierte Agent auf Port `9001` kann weiterhin über
   `Manuell einrichten` mit Token und Fingerprint aus `.dev` hinzugefügt werden,
   weil er bereits vom Dev-Startskript installiert wurde.
4. Löse den Healthcheck aus. Der Node muss `online` sein.

Damit wird derselbe echte Create-Node-, DIS-Verschlüsselungs-, TLS-Pinning- und
Heartbeat-Pfad verwendet wie in Produktion. Um einen Ausfall zu simulieren,
schließe nur das Fenster `MSM - Agent Node 2` und führe den Healthcheck erneut aus.

### Verschlüsseltes Backup und Restore lokal testen

1. Öffne die MinIO Console auf `http://localhost:9003` und melde dich mit den
   Werten aus `msm-agent/.dev/minio.env` an.
2. Prüfe, dass der Smoke-Test den Bucket `msm-backups` angelegt hat.
3. Trage in den Backup-Einstellungen des Panels ein:
   - Endpoint: `http://127.0.0.1:9002`
   - Bucket: `msm-backups`
   - Access Key und Secret Key aus `msm-agent/.dev/minio.env`
   - ein eigenes lokales Backup-Passwort
4. Erstelle im Panel einen Testserver und wähle `Local Node 2` als Node.
5. Lege über den Dateimanager eine eindeutig erkennbare Testdatei an und starte
   anschließend ein Backup.
6. Prüfe in MinIO, dass unter `msm-backups/servers/<server-id>/` eine `.enc`-Datei
   liegt. Sie darf sich mit `tar` oder einem Archivprogramm nicht öffnen lassen.
7. Ändere oder lösche die Testdatei im Panel, stelle das Backup wieder her und
   prüfe, dass der ursprüngliche Inhalt zurückgekehrt ist.
8. Falls PostgreSQL für den Testserver aktiviert ist, lege zusätzlich einen
   Testdatensatz an und prüfe nach dem Restore Dateninhalt und Besitzerrechte.

### Grenze der lokalen Simulation

Beide Agents verwenden lokal denselben Docker-Desktop-Daemon. Dadurch werden die
Panel→Agent-Kommunikation, TLS, Node-Auswahl, Dateipfade, Containeroperationen und
Backups real ausgeführt. Nicht simuliert werden die physische Host-Isolation,
echte WAN-Latenz und der vollständige Ausfall eines zweiten Rechners. In Produktion
hat jeder Agent seinen eigenen Docker-Daemon und sein eigenes Dateisystem; die
Panel-Logik und API-Verträge bleiben identisch.

PostgreSQL ist auch in der Entwicklung verbindlich. SQLite wird nur in
isolierten Unit-Tests und als read-only Quelle des einmaligen Legacy-Imports
verwendet.

---

## Manueller Start der Komponenten (Alternativ)

Die manuelle Steuerung der Komponenten läuft wie folgt:

### Systemkomponenten & Ports im Dev-Setup

| Komponente | Verzeichnis | Port / Schnittstelle | Tech-Stack | Start-Befehl |
|---|---|---|---|---|
| **DIS Sidecar** | `dis-sidecar/` | `127.0.0.1:9100` | Node.js / @msdis/shield | `node server.mjs` |
| **Backend** | `backend/` | `127.0.0.1:8000` | Python 3.11+ / FastAPI | `uvicorn main:app --reload --port 8000` |
| **Frontend** | `frontend/` | `localhost:3000` | React / Vite | `npm run dev` |
| **MSM Agent** (Phase 1) | `msm-agent/` | `127.0.0.1:9000` | Python / FastAPI | `python main.py` |

---

## 1. Voraussetzungen & Umgebungsvariablen

**Regel:** Jede Komponente hat eine vollständige Env-Vorlage (`*.env.example`). Lokale Secrets liegen in gitignored `.env` / `.env.local`.

| Komponente | Vorlage (committen) | Lokal (nicht committen) |
|---|---|---|
| Panel-Backend | `backend/.env.example` | `backend/.env` |
| Frontend (Vite) | `frontend/.env.example` | `frontend/.env.local` |
| MSM Agent | `msm-agent/.env.example` | `msm-agent/.env` |

Kopieren falls fehlend:
```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
cp msm-agent/.env.example msm-agent/.env
```

### Backend Dev-Minimum (Auszug — vollständige Liste in `backend/.env.example`)

```env
MSM_APP_NAME="Maunting Server Manager"
MSM_DEBUG=true
MSM_DATABASE_URL="postgresql+psycopg2://msm:msm_dev_pass@127.0.0.1:15434/msm"
MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://msm:msm_dev_pass@127.0.0.1:15434/msm"
MSM_SECRET_KEY="test-secret-key-for-dev-only-32-bytes-long!!"
MSM_PANEL_URL="http://localhost:3000"
MSM_SETUP_COMPLETED_FILE="./.setup_completed"
MSM_DIS_SIDECAR_URL="http://127.0.0.1:9100"
MSM_DIS_SALT="qhCLKLPChabuAqcCOqqxRw=="
MSM_COOKIE_CROSS_SITE=false
MSM_CORS_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
MSM_SERVE_FRONTEND=false
MSM_SERVERS_DIR="./servers"
```

### Frontend Dev (Phase 4)

```env
# frontend/.env.local — leer = Vite-Proxy /api → :8000
VITE_API_URL=
VITE_WS_URL=
```

---

## 2. Starten der Komponenten

### Schritt 2.1: DIS Sidecar starten
Das DIS Sidecar stellt alle Krypto-Operationen (Verschlüsselung, Passwort-Hashing, TOTP) bereit. Da das Panel selbst keine Kryptographie implementiert, **muss** dieser Dienst laufen.

**Unter Windows (PowerShell):**
```powershell
cd dis-sidecar
$env:NODE_ENV="development"
$env:MSM_SECRET_KEY="test-secret-key-for-dev-only-32-bytes-long!!"
$env:MSM_DIS_SALT="qhCLKLPChabuAqcCOqqxRw=="
node server.mjs
```

**Unter Linux / WSL:**
```bash
cd dis-sidecar
export NODE_ENV="development"
export MSM_SECRET_KEY="test-secret-key-for-dev-only-32-bytes-long!!"
export MSM_DIS_SALT="qhCLKLPChabuAqcCOqqxRw=="
node server.mjs
```

*Erfolgsmeldung im Terminal:*
`[DIS Sidecar] Encryption key derived (HKDF-SHA-256, 256-bit)`
`[DIS Sidecar] Listening on http://127.0.0.1:9100`

---

### Schritt 2.2: Python-Backend starten
Stelle sicher, dass du das Virtual Environment (`venv`) nutzt.

**Unter Windows (PowerShell):**
```powershell
cd backend
.\venv\Scripts\activate
$env:NODE_ENV="development"
uvicorn main:app --reload --port 8000
```

**Unter Linux / WSL:**
```bash
cd backend
source venv/bin/activate
export NODE_ENV="development"
uvicorn main:app --reload --port 8000
```

*Erfolgsmeldung im Terminal:*
`INFO:     Uvicorn server running on http://127.0.0.1:8000 (Press CTRL+C to quit)`

---

### Schritt 2.3: React-Frontend starten

**In einem neuen Terminal:**
```bash
cd frontend
npm install
npm run dev
```

*Erfolgsmeldung im Terminal:*
`  VITE v5.x.x  ready in X ms`
`  ➜  Local:   http://localhost:3000/`

**Default (empfohlen):** Vite proxied `/api` → `http://localhost:8000` (same-origin Cookies, `SameSite=Lax`).

**Optional — echte Cross-Origin-Dev (Phase 4):**

`frontend/.env.local`:
```env
VITE_API_URL=http://127.0.0.1:8000
```

`backend/.env` ergänzen:
```env
MSM_PANEL_URL="http://localhost:3000"
MSM_CORS_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
MSM_COOKIE_CROSS_SITE=true
MSM_SERVE_FRONTEND=false
```

Dann Cookies mit `SameSite=None; Secure` und CSRF über Response-Header `X-CSRF-Token`.

### Agent (Phase 1/5) — optional lokal

```bash
cp msm-agent/.env.example msm-agent/.env
# MSM_AGENT_TOKEN setzen; fuer TLS:
# openssl req -x509 -newkey rsa:4096 -nodes -keyout msm-agent/certs/agent.key \
#   -out msm-agent/certs/agent.crt -days 365 -subj "/CN=msm-agent"
# Fingerprint: openssl x509 -in msm-agent/certs/agent.crt -outform DER | sha256sum
```

Remote-Produktion: Den einen Installationsbefehl unter `Admin → Nodes` auf dem
Zielserver ausführen und den angezeigten Code im Panel bestätigen. Der Installer
richtet rootless Docker, TLS, systemd und bei aktiver UFW die Portregel ein.

### Phase 6 — Agent→S3 Backups

Voraussetzungen: S3 (oder MinIO) + Backup-Passwort im Panel, Remote-Node online.

1. Panel leitet AES-Key via DIS `derive-raw-key` ab (Argon2id wie `init-key`).
2. Agent: `POST /backup/create` tar.gz → AES-256-GCM Frames (DIS-kompatibel) → Multipart-S3.
3. Restore Remote: `POST /backup/restore` auf dem Agenten (Download → Decrypt → Extract).
4. Local-Node bleibt beim bisherigen Panel-Pfad (lokales `.enc` + optionaler S3-Upload).

Agent-Deps: `pip install -r msm-agent/requirements.txt` (enthält `cryptography`, `boto3`).

---

## 3. Testen und Verifizieren von Phase 0

In Phase 0 wurde die Datenbank-Migration implementiert. Diese fügt das Feld `node_id` zu den Servern hinzu und verknüpft sie mit einem Default-Node.

### Automatisierte Tests ausführen
Um sicherzustellen, dass keine bestehende Logik beeinträchtigt wird:
```bash
cd backend
# Windows
.\venv\Scripts\pytest
# Linux/WSL
pytest
```
*Erwartetes Ergebnis:* Alle 1570+ Tests laufen grün durch (`passed`).

### Manuelle Verifizierung der Datenbank
Die Migration legt automatisch einen Default-Node (`Local`) an und weist ihm
alle vorhandenen Server zu. Im automatischen Windows-Setup prüfst du das ohne
Passwortausgabe direkt im Dev-Container:

```powershell
docker exec msm-panel-postgres-dev psql -U msm -d msm -c "SELECT id, name, is_local FROM nodes;"
docker exec msm-panel-postgres-dev psql -U msm -d msm -c "SELECT id, name, node_id FROM servers;"
```

Alle bestehenden Server müssen eine `node_id` besitzen.

---

## 4. Testen von Phase 1 (MSM Agent)

### Agent starten
```bash
cd msm-agent
# Windows
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # Token setzen: MSM_AGENT_TOKEN=...
python main.py

# Linux / WSL
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # Token setzen
python main.py
```

`start-dev.bat` startet den Agenten automatisch mit.

### Smoke-Tests
```bash
curl http://localhost:9000/health
# Erwartet: {"status":"ok","version":"1.0.0","docker_connected":...}

curl http://localhost:9000/containers
# Erwartet: HTTP 401

curl -H "Authorization: Bearer <MSM_AGENT_TOKEN>" http://localhost:9000/containers
# Erwartet: HTTP 200 []

# Path-Traversal muss abgewiesen werden:
curl -H "Authorization: Bearer <TOKEN>" \
  "http://localhost:9000/files/read?server_id=1&path=../../etc/passwd"
# Erwartet: HTTP 400 oder 403
```

### Agent-Unit-Tests
```bash
cd msm-agent
source venv/bin/activate   # bzw. .\venv\Scripts\activate
pip install pytest
pytest -q
```

---

## 5. Testen von Phase 2 (Node-Aware Panel)

Phase 2 leitet Docker/Dateien/Konsole/Backups über den `NodeClient` an den Agenten
(Remote-Nodes). Port-Vergabe ist node-scoped (gleicher Port auf Node A und B erlaubt).

### Owner-API Nodes
```bash
# Liste (Owner-Cookie + CSRF für Writes)
curl -b cookies.txt http://localhost:8000/api/nodes

# Node hinzufügen (Token wird DIS-verschlüsselt gespeichert, nie im Klartext zurückgegeben)
curl -b cookies.txt -X POST http://localhost:8000/api/nodes \
  -H "Content-Type: application/json" -H "X-CSRF-Token: ..." \
  -d '{"name":"Worker-1","host":"http://10.0.0.5:9000","auth_token":"<agent-token>"}'
```

### Backend-Tests
```bash
cd backend
pytest tests/test_node_client.py tests/test_nodes_router.py -q
pytest -q   # volle Suite
```

---

## 6. Testen von Phase 3 (Node-UI & Server-Erstellung)

1. Panel + Agent starten (`start-dev.bat` oder manuell).
2. Als Owner: Sidebar → **Nodes** (`/admin/nodes`).
3. Node hinzufügen (Token wird nie wieder angezeigt).
4. Health-Check auslösen (Status-Badge Online/Offline).
5. Server erstellen: bei **>1 Node** erscheint Node-Dropdown; bei genau 1 Node bleibt es unsichtbar.
6. Server-Liste und Server-Detail zeigen den Node-Namen (Badge / System-Info).
