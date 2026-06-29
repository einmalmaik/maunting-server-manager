## Servergebundene PostgreSQL-Verwaltung

Mit v1.3.0 wird die bislang globale Postgres-Verwaltung auf ein **servergebundenes
Modell** umgestellt. Jeder Server kann jetzt eigene Datenbanken, eigene Power-User
und einen eigenen SQL-Editor bekommen — ohne dass sich Gameserver untereinander
in die Quere kommen.

### Neue Features

- **Pro Server: eigene Postgres-Instanz**
  - Über das Server-Detail öffnet sich ein neuer Tab **„Datenbank“** (`DatabaseManager.tsx`).
  - Pro Server werden angelegt: Default-Datenbank, Owner-Rolle (Power-User),
    Connection-Strings (intern / extern).
  - Provisioning läuft container-basiert mit eigenem Volume —
    `volume_path` wird per Server-Setting festgelegt und persistent gespeichert.
  - `services/postgres_service.py` kapselt `psql`-Aufrufe, Lifecycle und
    Credential-Generierung. Keine globalen Locks mehr.
- **Power-User / Owner-Rolle pro Server**
  - Beim Anlegen wird die Rolle automatisch auf `SUPERUSER` für genau diese
    eine Datenbank gehoben (kein Cluster-weites `SUPERUSER`).
  - Credentials werden **nur einmal** im UI angezeigt (`PostgresCredentialsDialog.tsx`),
    danach nur noch Hash-Storage serverseitig.
- **Multi-Statement SQL-Editor (psql-like)**
  - Eigener `services/sql_parser.py` zerlegt SQL in einzelne Statements,
    Kommentare (`-- …` und `/* … */`) und Dollar-Quoting bleiben erhalten.
  - Pro Tab: Ergebnis-Liste (`SELECT` → Tabelle, `INSERT/UPDATE/DELETE` → Rowcount,
    `CREATE/ALTER/DROP` → OK). Transaktionen werden pro Statement committet.
  - Verhindert Statement-Injection über `;`-Trenner.
- **Trusted Extensions pro Datenbank**
  - Pro Datenbank whiteliste Extensions: `pgcrypto`, `pg_trgm`, `citext`,
    `hstore`, `uuid-ossp`, `pg_stat_statements`, … (Server-Setting
    `postgres.trusted_extensions`).
  - Nicht-getrustete Extensions werfen `42883` mit klarer Fehlermeldung
    statt `permission denied` — User können den Admin kontaktieren.

### Bugfixes

- **`fix(postgres): managed Postgres fails to provision under rootless Docker`**
  - Auf `singra` mit rootless Docker (`msm:994` + `subuid 296608:65536`) brach
    das initiale `initdb` mit `permission denied` auf dem Socket ab.
  - Fix: Postgres läuft jetzt im Container unter dem gleichen UID/GID wie das
    MSM-Service-User; `PGDATA` und `PG_SOCKET` werden explizit als `chown 700`
    gesetzt, statt sich auf das Overlay-Default zu verlassen.
  - Idempotent: bestehende Volumes werden erkannt, kein Doppel-`initdb`.

### Tests

- **Neu:** `backend/tests/test_postgres_service.py` — Provisioning, Power-User,
  Credential-Rotation, Volume-Reuse.
- **Neu:** `backend/tests/test_sql_parser.py` — 87 Tests, decken Single- und
  Multi-Statement, Kommentare, Dollar-Quoting, leere Statements, Fehlerfälle ab.
- **Erweitert:** `backend/tests/test_servers_router.py` — 55 Tests für die
  neuen `databases`-Endpunkte.
- Alle bestehenden Blueprint-Tests (131) bleiben grün.

### Sicherheit

- **Credentials leaken nicht mehr ins UI-State.** Nach einmaliger Anzeige liegt
  nur noch der Hash in `models/postgres_database.py` und `models/postgres_user.py`.
  Der Connection-String wird **nie** zurück ans Frontend geschickt (selbst beim
  Server-`GET`).
- **SQL-Parser verhindert Statement-Chain-Injection.** Auch wenn ein Game-Server
  einen Multi-Statement-POST versucht, wird jeder Sub-Statement einzeln
  ausgeführt und committed — kein `; DROP DATABASE`-Szenario quer über andere
  Datenbanken.
- **Power-User ist pro-DB**, nicht cluster-weit. Auch ein kompromittierter
  Server-A kann nicht Server-B's Datenbanken löschen.
- **Keine** Änderung am Token-/URL-/SSH-Handling. Bestehende `credential_vault`-
  Invarianten bleiben erhalten.

### Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.3.0`), `systemctl restart msm-panel`.
- **Kein** Blueprint-Edit nötig.
- **Keine** neuen Dependencies.
- **Keine** Daten-Migration. Bestehende globale Postgres-Datenbanken bleiben
  unter `backend/services/postgres_service.py` Legacy-Pfad erreichbar, werden
  aber nicht automatisch in das neue Per-Server-Modell überführt.

### Auswirkung auf `singra_backend`

Der neue Tab **„Datenbank“** im Server-Detail ist sofort verfügbar. Pro Server
kann jetzt:

1. eine eigene Postgres-DB provisioniert werden,
2. ein eigener Owner mit SUPERUSER-auf-diese-DB-Rechte erzeugt werden,
3. ein psql-like Editor gegen genau diese DB benutzt werden,
4. Trusted Extensions pro DB aktiviert werden.

Der Rootless-Docker-Provisioning-Bug ist auf `singra` reproduzierbar vorher
aufgetreten (Volume-Permission-Denied im Panel-Log) — der Fix räumt das auf.