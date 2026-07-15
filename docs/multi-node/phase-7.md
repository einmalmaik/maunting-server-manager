# Phase 7: Node-Aware Managed Postgres (Multi-Node Architektur)

## 1. Ausgangslage & Problemstellung
Im Rahmen der Phasen 1 bis 6 wurde der Maunting Server Manager (MSM) erfolgreich auf eine Multi-Node-Architektur umgestellt. Alle Datei- und Docker-Operationen wurden in den `msm-agent` ausgelagert.

**Ein kritischer Bereich wurde jedoch übersehen:** Die Server-eigenen Postgres-Datenbanken (Managed Postgres).
Aktuell geht `backend/services/postgres_service.py` fest davon aus, dass der Container `msm-postgres` auf dem `localhost` (`127.0.0.1:5432`) des Panel-Servers läuft. Das Backend verbindet sich direkt per `psycopg2` (SQL), um Datenbanken, Rollen und Berechtigungen für die Gameserver zu erstellen. 

In einer Multi-Node-Architektur funktioniert das nicht mehr:
1. Wenn ein Gameserver auf Node B gestartet wird, braucht er seine Datenbank auf Node B (damit er über das lokale Docker-Netzwerk `msm-managed-postgres` darauf zugreifen kann).
2. Das Panel-Backend (Node A) kann und darf nicht unverschlüsselt über das Internet auf Port 5432 von Node B zugreifen, um DDL-Befehle auszuführen.

## 2. Zielsetzung (KISS & Sicherheit)
Die gesamte Logik zur Verwaltung des `msm-postgres`-Containers und die Ausführung der SQL-Befehle (`psycopg2`) wird vollständig in den `msm-agent` ausgelagert. Das Panel-Backend wird zu einem reinen Proxy (Durchlauferhitzer), der nur noch Steuerbefehle an den Agenten sendet. 

---

## 3. Implementierungs-Aufgaben für den KI-Agenten

Bitte lies zwingend zuerst die Datei `AGENTS.md` für die Sicherheits- und Architekturregeln!

### Schritt 1: MSM-Agent aufrüsten (msm-agent/)
Der Agent muss lernen, SQL zu sprechen und den lokalen Postgres-Container zu verwalten.

1. **Abhängigkeiten aktualisieren:**
   Füge `psycopg2-binary` (und ggf. `sqlalchemy` falls für Escaping nötig) zur `msm-agent/requirements.txt` hinzu.

2. **Neuer Service: `msm-agent/services/postgres_service.py`**
   - Kopiere die *Kern-Logik* für die SQL-Ausführung aus dem Panel in den Agenten (z.B. `ensure_internal_postgres`, `_create_database_and_user`, `list_tables`, `describe_table`).
   - Der Agent nutzt intern `docker_service.py`, um den Container `msm-postgres` zu starten (genauso wie es vorher das Panel getan hat).
   - Der Agent verbindet sich lokal per `psycopg2` auf `127.0.0.1:5432` und führt das DDL (`CREATE DATABASE`, `CREATE ROLE`, `GRANT`) aus.
   - Die Passwörter für Admin und DB-Owner müssen vom Panel übergeben werden (siehe Schritt 2).

3. **Neuer Router: `msm-agent/routers/postgres.py`**
   Erstelle REST-Endpoints, die das Panel aufrufen kann:
   - `POST /postgres/ensure` (Startet den Container)
   - `POST /postgres/provision` (Erstellt DB, User und Grants. Erwartet im Body: `db_name`, `owner_role`, `owner_password`, `user_name`, `user_password`)
   - `POST /postgres/users/rotate` (Ändert das Passwort eines Users)
   - `DELETE /postgres/database` (Löscht eine DB und ihre Rollen)
   - `POST /postgres/query` (Führt sichere `SELECT`-Queries aus für Stats und Tabellen-Auflistung)
   - Registriere den Router in `msm-agent/main.py`.

### Schritt 2: Panel-Backend umbauen (backend/)
Das Panel verliert die direkte Anbindung an den Docker-Dämon und die SQL-Ausführung für Managed Postgres.

1. **`backend/services/node_client.py` erweitern:**
   Erstelle Wrapper-Methoden, die per HTTP-Requests mit den neuen `/postgres/*`-Endpoints des Agenten kommunizieren (z.B. `postgres_provision(self, payload: dict)`).

2. **`backend/services/postgres_service.py` radikal kürzen (Refactoring):**
   - Entferne den Import von `psycopg2` und `docker_service.py`.
   - Das Panel generiert weiterhin die sicheren, zufälligen Passwörter (z. B. `_generate_password()`), verschlüsselt diese für die eigene SQLite-Datenbank über das `AuthService` (DIS), schickt sie aber für die *Erstellung* im Klartext (temporär im Request-Payload) an den `NodeClient`.
   - Die Methoden wie `provision_server_databases`, `delete_database`, `list_tables`, `database_stats` rufen ab sofort nur noch den `NodeClient(server.node)` auf.
   - WICHTIG: Das Panel bleibt die "Source of Truth". Es speichert die erzeugten DB-Namen, User und verschlüsselten Passwörter weiterhin in seinen SQLAlchemy-Modellen (`PostgresDatabase`, `PostgresUser`).

3. **Backups anpassen (pg_dump):**
   Die Funktionen in `backend/services/postgres_service.py`, die `pg_dump` oder `psql` per `docker_service.exec_in` ausführen (z. B. `backup_database_to_file`), müssen nun ebenfalls über den Agenten laufen. Wahrscheinlich reicht es, wenn der Agent in seinem `backup.py` oder `postgres.py` Router einen Endpoint `/postgres/dump` anbietet.

---

## 4. Sicherheitsinvarianten (Checkliste für die Abnahme)
- [x] Kein `psycopg2` Import mehr im `backend/services/postgres_service.py`.
- [x] Keine Klartext-Passwörter in Logs, Fehler-Rückgaben oder URLs (auch nicht beim Agenten).
- [x] Das Panel speichert weiterhin die DIS-verschlüsselten Passwörter in der SQLite. Der Agent speichert **keine** Passwörter dauerhaft auf Festplatte (außer natürlich in der Postgres-Instanz selbst als gehashte Rollen-Passwörter).
- [x] Bei der Erstellung von Datenbanken auf einem Remote-Node läuft der Traffic sicher durch den TLS-Tunnel (`HTTPS`), da die Passwörter im Request-Body stecken.

## 5. Status

**ABGESCHLOSSEN** (Branch `feature/multi-node`).

