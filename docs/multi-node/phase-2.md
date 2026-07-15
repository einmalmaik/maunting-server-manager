# Phase 2: Panel-Backend auf "Node-Aware" umbauen

Dieses Dokument spezifiziert das Refactoring des zentralen Panel-Backends. Ziel ist es, direkte lokale Host-Kopplungen (Docker SDK, Dateisystem-Aufrufe, etc.) durch Delegation an den entsprechenden Node-Agenten zu ersetzen.

---

## 1. Das Bindeglied: `node_client.py`

Es wird ein neuer Service `backend/services/node_client.py` implementiert. Dieser stellt eine statische Methode oder eine Klasse bereit, um mit den HTTP/WS-Endpoints des Agenten zu kommunizieren.

### Authentifizierung & Token-Decryption
Bevor ein Request an den Agenten gesendet wird, muss das Panel den Token des Nodes entschlüsseln.
- Das Model `Node` enthält `auth_token_enc`.
- Entschlüsselung erfolgt über `DisClient`:
  ```python
  decrypted_token = DisClient.decrypt(node.auth_token_enc, aad="msm:node:auth_token")
  ```
- **Sicherheits-Invariante**: Der entschlüsselte Token darf unter keinen Umständen geloggt, persistiert oder an das Frontend gesendet werden!

---

## 2. Refactoring-Bereiche im Backend

### 2.1 `backend/services/docker_service.py`
- **Bisher**: Importiert `docker` (Python SDK) und kommuniziert mit dem lokalen Socket.
- **Neu**: Die Methoden (`start_container`, `stop_container`, `get_container_stats`, etc.) müssen den `NodeClient` des Zielnodes aufrufen.
- Jede Methode benötigt künftig die Referenz auf den `Server` oder dessen `node_id`.

### 2.2 `backend/routers/file_manager.py`
- **Bisher**: Liest/schreibt über `aiofiles` und `os.path` auf dem lokalen Host.
- **Neu**: Die Endpunkte (`/api/files/list`, `/api/files/read` etc.) ermitteln den Server, rufen den zugehörigen `NodeClient` auf und leiten Dateiinhalte weiter.

### 2.3 `backend/services/console_stream_service.py`
- **Bisher**: Liest direkt aus dem Docker SDK Stream des lokalen Containers.
- **Neu**: Baut einen WebSocket-Proxy auf. Das Backend öffnet eine WebSocket-Verbindung zum Agenten (`ws://<node_host>/console/msm-srv-<id>/ws`) und vermittelt bidirektional zwischen dem Browser des Users und dem Agenten.

### 2.4 `backend/services/port_check_service.py`
- **Bisher**: Prüft Port-Konflikte global (lokaler Host-Socket-Check + DB-Check aller Server).
- **Neu**: Die Port-Prüfung darf nur noch Server betreffen, die **auf demselben Node** laufen. Ein Port (z.B. 27015) kann auf Node A und Node B gleichzeitig belegt werden.

### 2.5 `backend/services/backup_service.py`
- **Bürde in Phase 2 (KISS)**: Backups werden über den Agenten erstellt, als Stream an das Panel gesendet, dort via DIS verschlüsselt und zu S3 hochgeladen.
- Dies hält die S3-Zugangsdaten zentral im Panel-Server geschützt (Sicherheits-Invariante) und vermeidet eine Verteilung an die Agenten. Optimierungen folgen in Phase 6.

---

## 3. Node-Management API (`routers/nodes.py`)

Es wird ein neuer Router für Administratoren/Owner implementiert:
- `GET /api/nodes`: Liste aller Nodes (Ausgabe via `NodeOut`-Schema, enthält Server-Anzahl).
- `POST /api/nodes`: Node hinzufügen (Owner-only). Token wird über DIS verschlüsselt in `auth_token_enc` gespeichert.
- `GET /api/nodes/{id}`: Details + aktuelle Metriken des Nodes (abgefragt live beim Agenten).
- `PUT /api/nodes/{id}`: Node-Verbindung ändern (Owner-only).
- `DELETE /api/nodes/{id}`: Node löschen (nur erlaubt, wenn keine Server mehr diesem Node zugewiesen sind).

---

## 4. Test- und Verifizierungsschritte

1. **Localhost-Kompakttest**:
   - Backend läuft auf Port `8080`, Agent auf Port `9000` (beide auf demselben Rechner).
   - Registriere den lokalen Node im Panel.
   - Alle Operationen (Server starten, Konsole öffnen, Dateien bearbeiten) müssen identisch wie vor dem Refactoring funktionieren.
2. **Integrationstests**:
   - Die Backend-Testsuite (`pytest`) muss so angepasst werden, dass API-Aufrufe an den Agenten in den Tests gemockt werden (oder die Tests verwenden den lokalen Agenten über einen Test-Setup).
