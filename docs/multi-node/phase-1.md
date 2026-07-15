# Phase 1: MSM Agent (Eigenständiges Mini-Projekt)

Dieses Dokument enthält die detaillierte Spezifikation für den **MSM Agent**. Der Agent wird als eigenständige, leichtgewichtige FastAPI-Anwendung implementiert, die auf jedem Server (Node) läuft und Befehle vom zentralen Panel entgegennimmt.

---

## 1. Architektur und Anforderungen

- **Technologie**: Python 3.11+ / FastAPI / Uvicorn.
- **Port**: Standardmäßig `9000`.
- **Zustandslosigkeit (Stateless)**: Der Agent speichert keinen Zustand über registrierte Game-Server in einer eigenen Datenbank. Er liest zur Laufzeit direkt den Status der lokalen Docker-Container und das lokale Dateisystem aus. Die "Single Source of Truth" verbleibt beim zentralen Panel-Backend.
- **Sicherheits-Invariante (Authentifizierung)**:
  - Jeder Request (außer `/health`) muss über einen statischen Bearer-Token im `Authorization`-Header authentifiziert werden.
  - Der Token wird über die Umgebungsvariable `MSM_AGENT_TOKEN` konfiguriert.
- **Kryptographie**: Der Agent führt **keine** Krypto-Operationen (DIS) durch und besitzt keinen Zugriff auf den Master-Schlüssel des Panels. Verschlüsselung/Secrets-Handling passiert ausschließlich auf dem Panel-Server.

---

## 2. Projekt-Struktur im Repository

Erstelle ein neues Verzeichnis `msm-agent/` im Repository-Root:

```
msm-agent/
├── main.py                    # App-Initialisierung, Auth-Middleware & Error Handling
├── config.py                  # Konfiguration (geladen aus Env-Variablen)
├── requirements.txt           # dependencies: fastapi, uvicorn, docker, psutil, aiofiles
├── .env.example               # Vorlage für Umgebungsvariablen
├── routers/
│   ├── containers.py          # Docker-Container-Operationen
│   ├── files.py               # Dateiverwaltung (CRUD)
│   ├── metrics.py             # System- & Container-Ressourcen
│   ├── console.py             # WebSocket Konsolen-Streaming
│   └── health.py              # Health-Check (unauthentifiziert)
└── services/
    ├── docker_service.py      # Docker-SDK-Wrapper (mit Hardening)
    └── file_service.py        # Dateisystem-Operationen (mit Path-Traversal-Schutz)
```

---

## 3. Sicherheits-Invarianten & Hardening

### 3.1 Docker-Hardening
Der Agent steuert die Docker-Engine über das offizielle Python Docker SDK (`unix:///run/user/UID/docker.sock`).
Beim Erstellen/Starten von Containern müssen die Härtungsmaßnahmen aus dem bestehenden Panel-Code ([docker_service.py](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/backend/services/docker_service.py), Zeilen 50-51) exakt übernommen werden:
- **Keine privilegierten Container**: `privileged=False`
- **Capabilities droppen**: `cap_drop=["ALL"]` (bzw. `_HARDENING_CAP_DROP` / `_HARDENING_SECURITY_OPT` anwenden)
- **Ressourcen-Limits**: CPU-, RAM- und Disk-Limits anwenden, falls übergeben.

### 3.2 Path-Traversal-Schutz
Alle Dateioperationen müssen strikt auf das Verzeichnis `MSM_SERVERS_DIR` (z.B. `/opt/msm/servers/`) begrenzt sein.
Bevor eine Datei gelesen, geschrieben, verschoben oder gelöscht wird, muss verifiziert werden, dass der Zielpfad (nach Auflösung von symbolischen Links via `realpath`) innerhalb des erlaubten Server-Verzeichnisses liegt.

---

## 4. API-Spezifikation (Endpoints)

### 4.1 Authentifizierung (Middleware)
Jeder API-Aufruf (außer `GET /health`) wird über eine Middleware geprüft:
```python
# Authorization: Bearer <MSM_AGENT_TOKEN>
```
Wenn der Header fehlt oder ungültig ist, wird sofort HTTP 401 (Unauthorized) zurückgegeben.

### 4.2 Health-Check (`routers/health.py`)
- **`GET /health`** (Keine Authentifizierung erforderlich)
  - Gibt Status-Informationen zurück.
  - Prüft, ob die lokale Docker-Engine erreichbar ist.
  - *Response*: `{"status": "ok", "version": "1.0.0", "docker_connected": true}`

### 4.3 Container-Verwaltung (`routers/containers.py`)
- **`GET /containers`**
  - Listet alle MSM-Container auf dem Node auf (Filter: Containername beginnt mit `msm-srv-`).
- **`POST /containers`**
  - Erstellt einen neuen Container auf Basis eines Blueprints (Image, Volumes, Ports, Env-Variablen).
- **`POST /containers/{name}/start`**
  - Startet den Container.
- **`POST /containers/{name}/stop`**
  - Stoppt den Container (unter Einhaltung einer Grace-Period).
- **`POST /containers/{name}/restart`**
  - Startet den Container neu.
- **`DELETE /containers/{name}`**
  - Entfernt den Container (und stoppt ihn vorher, falls aktiv).
- **`GET /containers/{name}/stats`**
  - Gibt Echtzeit-Metriken (CPU%, RAM-Nutzung, Netzwerktraffic) des Containers zurück.
- **`POST /containers/{name}/exec`**
  - Führt einen Befehl (z.B. ein RCON-Kommando) im Container aus.

### 4.4 Dateiverwaltung (`routers/files.py`)
- **`GET /files/list?server_id={id}&path={relative_path}`**
  - Listet das Verzeichnis eines Servers auf.
- **`GET /files/read?server_id={id}&path={relative_path}`**
  - Liest den Inhalt einer Textdatei.
- **`POST /files/write?server_id={id}&path={relative_path}`**
  - Schreibt den übergebenen String-Inhalt in eine Datei.
- **`DELETE /files/delete?server_id={id}&path={relative_path}`**
  - Löscht eine Datei oder einen Ordner.
- **`POST /files/rename?server_id={id}`**
  - Benennt eine Datei um (mit Parametern `old_path` und `new_path`).
- **`POST /files/upload?server_id={id}&path={relative_path}`**
  - Akzeptiert eine Datei per Multipart-Upload.
- **`GET /files/download?server_id={id}&path={relative_path}`**
  - Streamt die Datei als Download zurück.
- **`POST /files/create-dir?server_id={id}&path={relative_path}`**
  - Erstellt einen neuen Ordner.

### 4.5 System-Metriken (`routers/metrics.py`)
- **`GET /metrics`**
  - Gibt allgemeine Node-Auslastung zurück (CPU-Kerne gesamt, belegter RAM, belegter Disk, Netzwerk-Durchsatz).
  - Verwendet `psutil`.

### 4.6 Konsolen-Streaming (`routers/console.py`)
- **`WS /console/{container_name}/ws`**
  - Öffnet einen WebSocket-Kanal.
  - Streamt Docker-Container-Logs (Echtzeit + History-Tail) zum Client.
  - Akzeptiert Input-Nachrichten vom Client und leitet diese an den TTY-Input (Stdin) des Containers weiter.

---

## 5. Test- und Verifizierungsschritte für den ausführenden Agenten

1. **Lokaler Start**:
   Starte den Agenten auf Port `9000`.
2. **Konnektivitätsprüfung**:
   ```bash
   curl http://localhost:9000/health
   # Erwartet: {"status": "ok", ...}
   ```
3. **Authentifizierungsprüfung**:
   ```bash
   curl http://localhost:9000/containers
   # Erwartet: HTTP 401 Unauthorized
   
   curl -H "Authorization: Bearer <DEIN_TOKEN>" http://localhost:9000/containers
   # Erwartet: HTTP 200 [] (oder Liste von Containern)
   ```
4. **Härtungstest**:
   Stelle sicher, dass Versuche, ungesicherte Container (z.B. privileged) über den Agenten zu starten, abgelehnt werden.
5. **Path-Traversal-Test**:
   Versuche, über `/files/read?server_id=1&path=../../etc/passwd` eine Datei außerhalb des erlaubten Scopes zu lesen. Der Agent **muss** dies mit HTTP 403 / 400 abweisen.
