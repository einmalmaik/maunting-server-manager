# MSM → Multi-Node Infrastructure Panel: Roadmap

Dieses Dokument beschreibt die langfristige Roadmap zur Umstellung des Maunting Server Managers (MSM) von einem Single-Host Game-Server-Panel auf eine skalierbare Multi-Node Infrastruktur.

## Ausgangslage (IST-Zustand)
MSM läuft derzeit vollständig auf einem einzelnen Linux-Server. Alle Docker-Container (Game-Server), das Dateisystem (`/opt/msm/servers/`), das Python-Backend und das DIS-Sidecar teilen sich denselben Host. 

## Ziel-Architektur
- **Zentrales Panel-Backend**: FastAPI App, PostgreSQL, DIS-Sidecar für Verschlüsselung/Secrets, verwaltet Server-Definitionen, Berechtigungen und löst Scheduler-Jobs aus.
- **Frontend**: Als statische React-App entkoppelt und z.B. auf Vercel gehostet.
- **Remote-Nodes**: Jeder Node führt einen minimalen **MSM Agent** (FastAPI) aus. Dieser verwaltet lokal rootless Docker-Container, das Dateisystem des Nodes, streamt die Konsole per WebSockets und sammelt Metriken.

---

## Die Phasen der Roadmap

Die Entwicklung ist in 9 logische Phasen (Phase 0 bis 8) aufgeteilt. Für jede Phase gibt es eine detaillierte Spezifikation im Verzeichnis `docs/multi-node/`:

1. **[Phase 0: Datenbank & Modell vorbereiten](phase-0.md)** (ABGESCHLOSSEN)
   - Einführung des `Node`-Modells und Verknüpfung von `Server` mit `Node`.
   - Migration der bestehenden Datenbank, Seed des Default-Nodes (`localhost`) und Migration aller bestehenden Server auf diesen Node.

2. **[Phase 1: MSM Agent (Eigenständiges Mini-Projekt)](phase-1.md)** (ABGESCHLOSSEN)
   - Entwicklung der FastAPI App für den Agenten (Docker-, Datei-, Konsolen- und Metriken-API).
   - Generierung und Absicherung des Tokens (Kryptographie verbleibt beim Panel).

3. **[Phase 2: Panel-Backend auf "Node-Aware" umbauen](phase-2.md)** (ABGESCHLOSSEN)
   - Umleitung aller Docker- und Dateioperationen im Panel-Backend auf `NodeClient` (Delegation an den Agenten über HTTP/WebSockets).
   - Port-Zuweisung und Backups Node-aware gestalten.

4. **[Phase 3: Server-Erstellung & Node-UI im Frontend](phase-3.md)** (ABGESCHLOSSEN)
   - UI für das Hinzufügen/Verwalten von Nodes im Admin-Panel (unter Einhaltung der MauntingStudios Design-DNA).
   - Node-Auswahl beim Erstellen von Game-Servern.

**Inventar der behaltenen Kern-Dateien:** [IMPLEMENTED.md](IMPLEMENTED.md)

5. **[Phase 4: Frontend entkoppeln (Vercel-Ready)](phase-4.md)** (ABGESCHLOSSEN)
   - Statische React-App lauffähig machen auf Vercel.
   - CORS-Policies, cross-domain WebSockets und Cookie/CSRF-Auth anpassen.

6. **[Phase 5: Agent-Installer & Produktionsreife](phase-5.md)** (ABGESCHLOSSEN)
   - TLS-Absicherung der Agenten-API (Self-signed mit Fingerprint-Pinning im Panel).
   - Heartbeat-System und automatische Ausfallsicherung (Offline-Status der Nodes).
   - Bash-Installer für Remote-Server (`scripts/install-agent.sh`).

7. **[Phase 6: Backup-System für Multi-Node optimieren](phase-6.md)** (ABGESCHLOSSEN)
   - Direktes Backup-Streaming vom Agenten zu S3 mit temporären Credentials (vermeidet Traffic-Engpässe am zentralen Panel).

8. **[Phase 7: Node-Aware Managed Postgres](phase-7.md)** (ABGESCHLOSSEN)
   - Vollständige Auslagerung der `psycopg2`-Datenbanklogik und des `msm-postgres`-Containers in den `msm-agent`.
   - Backend wird zum reinen REST-Proxy für DDL-Befehle, um Remote-Nodes sicher zu unterstützen.
   - Server-Backups enthalten die node-lokalen Datenbanken; Restore erhält Daten und Owner-Rechte.

9. **[Phase 8: PostgreSQL-only, sichere Updates und einfache Node-Einrichtung](phase-8.md)** (ABGESCHLOSSEN)
   - PostgreSQL als einzige Panel-Betriebsdatenbank und geprüfter einmaliger SQLite-Import.
   - Update-Handoff, PostgreSQL-Backup, Agent-/Panel-Health-Gates und klarer Rollbackpfad.
   - Geführtes Node-Enrollment mit einem kopierbaren Befehl und Bestätigung im Panel.

### Abschluss-Härtung

Die phasenübergreifende Laufzeitprüfung wurde nachgezogen: vollständiger Remote-Dateimanager,
Source-Installationen, Konsole/Exec/stdin, Live-Ressourcen, Speicherstatus, Zielhost-Portprüfung,
Node-Firewall, automatische Bestandsmigration sowie atomische Datei- und Datenbank-Restores.
Damit sind lokale und entfernte Nodes über denselben Agent-Vertrag ausführbar; direkte
Panel-Host-Operationen werden für Remote-Server nicht mehr als Fallback verwendet.

---

## Lokale Entwicklung und Tests
Wie die Komponenten lokal (z.B. unter Windows mit WSL) gestartet und getestet werden, ist im Dokument **[Local Development Guide](local-development.md)** beschrieben.
