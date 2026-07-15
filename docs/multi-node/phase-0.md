# Phase 0: Datenbank & Modell vorbereiten (Abgeschlossen)

Dieses Dokument dokumentiert die Ergebnisse der Phase 0. Diese Phase bereitet die Datenbank und die internen SQLAlchemy-Modelle auf Multi-Node vor und stellt Abwärtskompatibilität durch einen automatischen Migration-Pfad sicher (Zero-Migration Requirement).

## 1. Implementierte Änderungen

### 1.1 Datenbank-Modelle
- **`backend/models/node.py`**:
  - Klasse `Node(Base)` definiert.
  - Felder: `id`, `name`, `host` (URL des Agenten), `auth_token_enc` (DIS-verschlüsselt), `is_local` (Boolean für Localhost-Betrieb), `status` ("online", "offline", "unknown"), Ressourcen-Metriken (`cpu_total`, `ram_total`, `disk_total`), `created_at` und `last_heartbeat`.
  - Relationship `servers` zu `Server` (1-n).
- **`backend/models/server.py`**:
  - Feld `node_id` als `ForeignKey("nodes.id")` hinzugefügt (Standard: `nullable=True` für die Migration, danach befüllt).
  - Relationship `node` zu `Node` (n-1).
- **`backend/models/__init__.py`**:
  - Exportiert `Node` und fügt es zu `__all__` hinzu.

### 1.2 Pydantic-Schemas
- **`backend/schemas/node.py`**:
  - `NodeCreate`: Schema für das Erstellen eines Nodes (Felder: `name`, `host`).
  - `NodeOut`: Schema für API-Antworten (Felder: `id`, `name`, `host`, `is_local`, `status`, Systemressourcen, `last_heartbeat` und `server_count`).
- **`backend/schemas/__init__.py`**:
  - Exportiert `NodeCreate` und `NodeOut` in `__all__`.

### 1.3 Migrations-Skript
- **`backend/scripts/migrate_add_nodes.py`**:
  - Idempotentes Python-Skript zur Durchführung der Schema-Erweiterungen.
  - Legt die Tabelle `nodes` an, falls nicht vorhanden.
  - Prüft über den `inspector` (SQLAlchemy), ob `node_id` in der Tabelle `servers` existiert und fügt sie andernfalls per `ALTER TABLE` hinzu.
  - Generiert einen zufälligen kryptographisch sicheren Token (`secrets.token_urlsafe(32)`) für den lokalen Node.
  - Verschlüsselt den Token über das `DIS Sidecar` mit AAD `"msm:node:auth_token"`.
  - Erstellt den Default-Node (`Local`, `http://127.0.0.1:9000`, `is_local=True`).
  - Setzt die `node_id` aller bestehenden Server auf den Default-Node.

---

## 2. Ausführung & Validierung

Das Migrations-Skript wurde am 15. Juli 2026 ausgeführt:
1. **DIS Sidecar** wurde im Dev-Modus gestartet.
2. Das Skript `scripts/migrate_add_nodes.py` wurde über die Python-Virtualenv ausgeführt.
3. Die Spalte `node_id` wurde erfolgreich hinzugefügt und der Default-Node angelegt.
4. Alle bestehenden Server in der sqlite-Datenbank (`msm.db`) wurden erfolgreich dem Default-Node zugeordnet.
5. Die gesamte Testsuite (`pytest`) wurde ausgeführt und alle **1572 Tests bestanden** fehlerfrei.

---

## 3. Relevante Token-Details für Phase 1
Bei der Migration wurde für den Default-Node ein lokaler Token generiert. Dieser muss beim Start des künftigen MSM-Agenten in dessen `.env` eingetragen werden, damit sich das Panel authentifizieren kann:

- **Default-Node ID**: `1`
- **Typ**: `is_local = True`
- **Sicherheits-Invariante**: Der Token liegt in der Datenbank ausschließlich per DIS verschlüsselt vor (`auth_token_enc`). Das Panel entschlüsselt ihn zur Laufzeit (in-memory) nur bei API-Aufrufen an den Agenten.
