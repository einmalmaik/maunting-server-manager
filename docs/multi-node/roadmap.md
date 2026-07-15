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

Die Entwicklung ist in 7 logische Phasen aufgeteilt. Für jede Phase gibt es eine detaillierte Spezifikation im Verzeichnis `docs/multi-node/`:

1. **[Phase 0: Datenbank & Modell vorbereiten](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-0.md)** (ABGESCHLOSSEN)
   - Einführung des `Node`-Modells und Verknüpfung von `Server` mit `Node`.
   - Migration der bestehenden Datenbank, Seed des Default-Nodes (`localhost`) und Migration aller bestehenden Server auf diesen Node.

2. **[Phase 1: MSM Agent (Eigenständiges Mini-Projekt)](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-1.md)**
   - Entwicklung der FastAPI App für den Agenten (Docker-, Datei-, Konsolen- und Metriken-API).
   - Generierung und Absicherung des Tokens (Kryptographie verbleibt beim Panel).

3. **[Phase 2: Panel-Backend auf "Node-Aware" umbauen](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-2.md)**
   - Umleitung aller Docker- und Dateioperationen im Panel-Backend auf `NodeClient` (Delegation an den Agenten über HTTP/WebSockets).
   - Port-Zuweisung und Backups Node-aware gestalten.

4. **[Phase 3: Server-Erstellung & Node-UI im Frontend](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-3.md)**
   - UI für das Hinzufügen/Verwalten von Nodes im Admin-Panel (unter Einhaltung der MauntingStudios Design-DNA).
   - Node-Auswahl beim Erstellen von Game-Servern.

5. **[Phase 4: Frontend entkoppeln (Vercel-Ready)](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-4.md)**
   - Statische React-App lauffähig machen auf Vercel.
   - CORS-Policies, cross-domain WebSockets und Token-basierte Auth anpassen.

6. **[Phase 5: Agent-Installer & Produktionsreife](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-5.md)**
   - TLS-Absicherung der Agenten-API (Self-signed mit Fingerprint-Pinning im Panel).
   - Heartbeat-System und automatische Ausfallsicherung (Offline-Status der Nodes).
   - Bash-Installer für Remote-Server (`install-agent.sh`).

7. **[Phase 6: Backup-System für Multi-Node optimieren](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/phase-6.md)**
   - Direktes Backup-Streaming vom Agenten zu S3 mit temporären Credentials (vermeidet Traffic-Engpässe am zentralen Panel).

---

## Lokale Entwicklung und Tests
Wie die Komponenten lokal (z.B. unter Windows mit WSL) gestartet und getestet werden, ist im Dokument **[Local Development Guide](file:///c:/Users/einma/AppData/Local/Singra/workspace/maunting-server-manager/docs/multi-node/local-development.md)** beschrieben.
