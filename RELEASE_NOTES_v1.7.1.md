# MSM v1.7.1 — Live-Ressourcenlimits fuer bestehende Server

Release mit sicherer nachtraeglicher Bearbeitung von CPU-, RAM- und Disk-Limits
fuer bestehende Server. CPU- und RAM-Aenderungen werden live auf laufende
Docker-Container angewendet, ohne den Game-Server neu zu starten. Disk-Limits
bleiben Soft-Limits und werden nach Aenderung sofort neu bewertet.

## Highlights

### Live CPU/RAM-Limit-Bearbeitung
Bestehende Server koennen CPU- und RAM-Limits nachtraeglich bearbeitet werden.
Bei laufenden Containern werden die Limits per Docker ``container.update()``
live angewendet, ohne den Container zu stoppen, neu zu erstellen oder neu zu
starten. Die Container-Identitaet und der Start-Zeitstempel bleiben unveraendert.

**CPU-Prozentlogik:** ``100% = 1 Core``, ``50% = 0.5 Core``, ``200% = 2 Cores``.
Intern wird ``cpu_period=100000`` und ``cpu_quota=cpu_limit_percent * 1000``
gesetzt. ``null`` (unlimitiert) setzt ``cpu_quota=0``.

**RAM-Limits:** Werden als ``mem_limit`` und ``memswap_limit`` in Bytes
(MB-Konvertierung) angewendet. ``null`` (unlimitiert) setzt beide auf ``0``.

### Gestoppte Server: Persistenz fuer naechsten Start
Bei gestoppten Servern werden geaenderte CPU/RAM-Limits nur in der Datenbank
persistiert. Es wird kein Docker-Update aufgerufen und der Server wird nicht
gestartet. Beim naechsten normalen Start werden die neuen Limits beim
Container-Create angewendet.

### Disk Soft-Limit: Sofortige Neubewertung
Disk-Limits bleiben weiterhin Soft-Limits (kein Docker Hard-Quota). Nach einer
Aenderung wird sofort die aktuelle Disk-Nutzung neu gemessen und die bestehende
Warn-/Stop-Policy angewendet. Es werden keine Server-Daten, Backups, Logs oder
Datenbank-Eintraege geloescht.

### Frontend: Ressourcen-Editor
In der Server-Detail-Seite wurde ein Ressourcen-Editor als modaler Dialog
hinzugefuegt. Nur Nutzer mit ``server.resources.manage``-Berechtigung sehen die
Bearbeiten-Aktion. Der Dialog ist barrierefrei (Fokus-Trap, Escape, Fokus-Rueckkehr),
validiert Werte client-seitig (CPU 10-3200, RAM >=512 MB, Disk >=1 GB), sendet
nur geaenderte Felder und behandelt leere Felder als ``null`` (unlimitiert).
DE/EN-Lokalisierung ist vollstaendig. Background-Polling setzt dirty-Edits
nicht zurueck.

## Sicherheits- und Autorisierungs-Notizen

- **Backend ist autoritativ:** Die ``server.resources.manage``-Berechtigung wird
  ausschliesslich im Backend geprueft. Frontend-Checks sind nur UX-Komfort.
- **CSRF-Schutz:** Alle PATCH-Requests erfordern Authentifizierung und einen
  gueltigen CSRF-Token.
- **Least-Privilege:** Ein Nutzer mit ``server.resources.manage`` kann
  Ressourcen-Felder aendern, aber keine Netzwerk- oder Config-Felder.
- **Mixed-Payload-Reject:** PATCH-Requests, die sowohl Ressourcen- als auch
  Netzwerk-Felder enthalten, werden mit ``409`` abgelehnt, bevor eine Mutation
  stattfindet. Berechtigungspruefungen laufen zuerst (``403`` vor ``409``).
- **Sanitized Errors:** Fehlermeldungen leaken keine Host-Pfade, Docker-Socket-Pfade,
  Stack-Traces, Tokens, Secrets oder rohe Docker-Ausgaben.
- **Keine destruktiven Aktionen:** Ressourcen-Bearbeitung loescht keine
  Server-Daten, Backups, Logs oder Datenbank-Eintraege.
- **Lifecycle-Serialisierung:** Ressourcen-PATCH ist mit Lifecycle-Locks
  serialisiert. Bei aktiver Start/Stop/Restart-Aktion wird ``409`` zurueckgegeben.
- **No-Drift-Garantie:** Schlaegt das Docker-Live-Update fehl, werden die
  Datenbank-Werte nicht geaendert. Bei Docker-Warnings oder Partial-Success
  werden alte Limits verifiziert zurueckgesichert oder der Fehler wird als
  Blocker gemeldet.

## Rootless-Docker-Einschraenkungen

Rootless Docker kann live CPU/RAM-cgroup-Updates ablehnen, wenn cgroup-v2-
Controller nicht delegiert sind. Dies ist eine Umgebungseinschraenkung, kein
Autorisierungs-Bypass.

Verhalten bei Rootless-Fehlern:
- Die API gibt eine sicher bereinigte Fehlermeldung zurueck.
- Die Datenbank-Werte bleiben unveraendert (kein Drift).
- Der Container wird nicht neu gestartet.
- Es wird kein privilegierter Fallback, kein rootful Docker, kein sudo und kein
  cgroup-Dateisystem-Schreibversuch unternommen.

## Validierung

### Backend
- **1548 Tests** im vollen Backend-pytest-Suite bestanden (inklusive Docker-Service-,
  Server-Router-, Cross-Area-E2E- und Migrations-Tests).
- Targeted Tests: ``test_docker_service.py`` und ``test_servers_router.py``:
  243 Tests bestanden.
- Cross-Area E2E: ``test_cross_area_e2e.py`` deckt VAL-CROSS-001 bis VAL-CROSS-014
  ab (Browser-zu-API-zu-Docker, Live-Update, Stopped-Next-Start, Unlimited
  Round-Trip, Disk-Warning/Stop, Auth/CSRF-Bypass, Mixed-Payload, Lifecycle-Race,
  Rootless-Failure, Netzwerk-Reachability, No-Destructive-Cleanup, Combined-Failure).

### Frontend
- TypeScript/Vite-Build erfolgreich.
- Volle Vitest-Suite bestanden (inklusive Shell.test.tsx, ResourceEditorDialog,
  ServerDetail-Permission-Matrix).
- Browser-Validierung mit ``agent-browser``: UI-Flows, Modal-Verhalten,
  Tastatur-Navigation, EN/DE-Lokalisierung, Permission-Gating, Konsolen-Sauberkeit.

### Recovery/Tauri
- Recovery-Typecheck: bestanden.
- Recovery-Tests: 123 Tests bestanden.
- Recovery-Build: erfolgreich.
- Cargo-Tests: 19 Rust-Tests bestanden.

### Review
- Mehrere Scrutiny-Review-Runden mit frischem Sub-Agent-Kontext durchgefuehrt
  (Backend, Docker/Rootless/Security, Frontend-UI/Accessibility/Design,
  Release-Workflow).
- Alle P1-Findings wurden behoben und re-reviewed.

## Bekannte Einschraenkungen

- **Rootless-Linux-Smoke-Validation:** Die lokale Entwicklungsumgebung verwendet
  Windows Docker Desktop, nicht das produktive Rootless-Linux-Docker. Rootless-
  Linux-Smoke-Validierung (live CPU/RAM-Update, Unlimited-Clearing, Safe-Failure
  auf einem echten Rootless-Linux-Host) konnte in dieser Umgebung nicht
  durchgefuehrt werden. Dies ist ein Release-Gate fuer die Publikation
  (Merge/Tag). Mocked-Docker-SDK-Tests und simulierte Rootless-Failure-Tests
  decken das Verhalten ab, ersetzen aber nicht die produktive Paritaet.
- **Disk als Soft-Limit:** Disk-Limits sind keine Docker-Hard-Quotas. Das System
  misst Nutzung und warnt/stopt bei Ueberschreitung, kann aber keine echte
  Festplatten-Quota auf Container-Ebene erzwingen.

## Geaenderte Bereiche

### Backend
- ``routers/servers.py``: Ressourcen-PATCH-Hardening, Mixed-Payload-Reject,
  Lifecycle-Serialisierung, Live-Update-Orchestrierung, Sanitized-Errors
- ``services/docker_service.py``: ``update_container_resources()`` fuer live
  CPU/RAM-Update, Verified-Rollback bei Warnings/Partial-Success
- ``services/scheduler_service.py``: Disk-Soft-Limit-Re-evaluation-Integration
- ``schemas/server.py``: Strikte JSON-Typ-Validierung fuer Ressourcen-Felder
- ``blueprints/github_source.py``: Cross-Platform ``os.getuid``/``getgid``-Guard
- ``tests/test_docker_service.py``: Docker-Update-Unit-Tests
- ``tests/test_servers_router.py``: Ressourcen-PATCH-Permissions- und
  Live-Update-Tests
- ``tests/test_cross_area_e2e.py``: Cross-Area-E2E-Validierung

### Frontend
- ``pages/ServerDetail.tsx``: Ressourcen-Karten und Bearbeiten-Aktion
- ``components/server/ResourceEditorDialog.tsx``: Modal-Dialog mit Validierung,
  Permission-Gating, Lokalisierung
- ``api/client.ts``: PATCH-Ressourcen-Unterstützung
- ``locales/de.json``, ``locales/en.json``: DE/EN-Ressourcen-Editor-Texte
- ``components/layout/Shell.test.tsx``: Test-Stabilisierung
- ``pages/ServerDetail.permission.test.tsx``: Permission-Topology-Matrix
- ``components/server/ResourceEditorDialog.test.tsx``: Dialog-Unit-Tests

### Version-Metadaten
- ``frontend/package.json``: ``1.7.0`` -> ``1.7.1``
- ``backend/main.py``: FastAPI-App-Version und ``/api/version``-Endpoint: ``1.7.0`` -> ``1.7.1``
- Recovery-App behaelt eigene Versionierung (``0.2.0``), da keine Recovery-Code-Aenderungen.

## Breaking Changes

Keine. Alle Aenderungen sind additiv zur bestehenden ``PATCH /api/servers/{id}``-
Schnittstelle. Bestehende Server, Netzwerk-Konfigurationen und Backup-Flows
funktionieren unveraendert.

## Upgrade-Hinweise

```bash
# 1. Pull
cd /opt/msm && git pull

# 2. venv synchronisieren (keine neuen Python-Pakete in diesem Release)
sudo bash scripts/sync-venv.sh

# 3. Panel restarten
sudo systemctl restart msm-panel
```

— Maunting Studios
