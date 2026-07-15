# Local Development & Testing Guide (Multi-Node Dev)

Dieser Guide beschreibt, wie das MSM-Gesamtsystem lokal auf einem Entwicklungsrechner (z.B. Windows mit WSL2) gestartet und getestet wird.

## Systemkomponenten & Ports im Dev-Setup

| Komponente | Verzeichnis | Port / Schnittstelle | Tech-Stack | Start-Befehl |
|---|---|---|---|---|
| **DIS Sidecar** | `dis-sidecar/` | `127.0.0.1:9100` | Node.js / @msdis/shield | `node server.mjs` |
| **Backend** | `backend/` | `127.0.0.1:8080` | Python 3.11+ / FastAPI | `python main.py` |
| **Frontend** | `frontend/` | `localhost:5173` | React / Vite | `npm run dev` |
| **MSM Agent** (Phase 1) | `msm-agent/` | `127.0.0.1:9000` | Python / FastAPI | `python main.py` |

---

## 1. Voraussetzungen & Umgebungsvariablen

Die Umgebungsvariablen werden über die `.env`-Datei im `backend/`-Ordner verwaltet.

Stelle sicher, dass folgende Werte eingetragen sind:
```env
MSM_APP_NAME="Maunting Server Manager"
MSM_DEBUG=true
MSM_DATABASE_URL="sqlite:///./msm.db"
MSM_SECRET_KEY="test-secret-key-for-dev-only-32-bytes-long!!"
MSM_PANEL_URL="http://localhost:5173"
MSM_SETUP_COMPLETED_FILE="./.setup_completed"
MSM_DIS_SALT="qhCLKLPChabuAqcCOqqxRw=="
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
python main.py
```

**Unter Linux / WSL:**
```bash
cd backend
source venv/bin/activate
export NODE_ENV="development"
python main.py
```

*Erfolgsmeldung im Terminal:*
`INFO:     Uvicorn server running on http://127.0.0.1:8080 (Press CTRL+C to quit)`

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
`  ➜  Local:   http://localhost:5173/`

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
Die Migration legt automatisch einen Default-Node (`Local`) an und weist ihm alle vorhandenen Server zu.
Du kannst dies überprüfen mit:
```bash
sqlite3 backend/msm.db
sqlite> SELECT * FROM nodes;
# Zeigt den Default-Node mit ID 1 und verschlüsseltem Token.
sqlite> SELECT id, name, node_id FROM servers;
# Zeigt alle Server. Das Feld `node_id` sollte überall `1` sein.
```

---

## 4. Testen von Phase 1 (Zukünftiger MSM Agent)
Sobald der MSM Agent (Phase 1) entwickelt ist, kann dieser lokal gestartet werden:
1. Er lauscht auf Port `9000`.
2. Er verwendet dieselben Docker-Sockets und dasselbe Dateisystem wie das lokale Panel.
3. Du kannst Test-Requests mit `curl` senden, indem du den bei der Migration generierten Token nutzt (siehe Konsolen-Output der Migration).
