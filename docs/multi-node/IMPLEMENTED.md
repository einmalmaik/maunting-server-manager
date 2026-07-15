# Multi-Node: Umgesetzte Phasen — Pflicht-Dateien behalten

Stand: Branch `feature/multi-node`  
Dieses Dokument listet die **wichtigen Dateien**, die zu den bereits umgesetzten Phasen gehören.  
**Nicht löschen, nicht aus dem Repo entfernen** (außer explizit genannte Secret-/Runtime-Dateien).

| Phase | Status |
|-------|--------|
| 0 – DB & Modell | umgesetzt |
| 1 – MSM Agent | umgesetzt |
| 2 – Panel node-aware | umgesetzt |
| 3 – Node-UI & Server-Create | umgesetzt |
| 4 – Frontend entkoppeln (Vercel-Ready) | umgesetzt |
| 5–6 | spezifiziert, noch offen |

---

## Specs & Guides (alle behalten)

| Datei | Rolle |
|-------|--------|
| `docs/multi-node/roadmap.md` | Gesamt-Roadmap |
| `docs/multi-node/local-development.md` | Lokaler Dev-/Test-Guide |
| `docs/multi-node/phase-0.md` … `phase-6.md` | Phasenspezifikationen |
| `docs/multi-node/IMPLEMENTED.md` | Diese Datei (Inventar) |

---

## Phase 0 — Datenbank & Modell

| Datei | Rolle |
|-------|--------|
| `backend/models/node.py` | `Node`-ORM |
| `backend/models/server.py` | `node_id` FK + Relationship |
| `backend/models/__init__.py` | Export `Node` |
| `backend/schemas/node.py` | `NodeCreate` / `NodeOut` / `NodeUpdate` |
| `backend/schemas/__init__.py` | Schema-Exports |
| `backend/scripts/migrate_add_nodes.py` | Idempotente Migration + Default-Node |

**Nicht committen:** verschlüsselte Tokens in DB, generierte Klartext-Tokens aus Migrations-Logs.

---

## Phase 1 — MSM Agent (Mini-Projekt)

| Datei / Pfad | Rolle |
|--------------|--------|
| `msm-agent/main.py` | FastAPI-App, Bearer-Auth (ASGI) |
| `msm-agent/config.py` | `MSM_*` Env-Config |
| `msm-agent/requirements.txt` | Agent-Dependencies |
| `msm-agent/.env.example` | Env-Vorlage (**kein** echtes `.env`) |
| `msm-agent/msm-agent.service.template` | systemd-Vorlage |
| `msm-agent/routers/health.py` | `GET /health` (unauth) |
| `msm-agent/routers/containers.py` | Container-CRUD / exec / stats |
| `msm-agent/routers/files.py` | Datei-API + `/files/archive` |
| `msm-agent/routers/metrics.py` | Node-Metriken |
| `msm-agent/routers/console.py` | WebSocket-Konsole |
| `msm-agent/services/docker_service.py` | Docker-SDK + Hardening |
| `msm-agent/services/file_service.py` | Path-Traversal-Schutz + Archive |
| `msm-agent/tests/` | Auth / Path-Traversal / Hardening |
| `install.sh` | Agent-Kopie, venv, `.env`, systemd |
| `update.sh` | Agent-Update + Service |
| `start-dev.bat` | Dev-Start inkl. Agent |
| `.gitignore` | `msm-agent/.env`, `venv/`, `servers/` |

**Nicht committen / nicht behalten im Git:**  
`msm-agent/.env`, `msm-agent/venv/`, `msm-agent/servers/`, `__pycache__/`.

---

## Phase 2 — Panel Backend node-aware

| Datei | Rolle |
|-------|--------|
| `backend/services/node_client.py` | HTTP/WS-Client zum Agenten (Token nur in-memory) |
| `backend/services/node_service.py` | Resolve Node, Token-Encrypt, Serialization |
| `backend/routers/nodes.py` | Admin/List Nodes-API |
| `backend/routers/__init__.py` + `backend/main.py` | Router-Registrierung |
| `backend/services/docker_service.py` | optional `node=` → Agent |
| `backend/games/base.py` | Lifecycle start/stop/status mit Node |
| `backend/routers/files.py` | File-Ops über Agent (Remote) |
| `backend/services/console_stream_service.py` | WS-Proxy zum Agenten |
| `backend/services/port_allocation_service.py` | Ports **pro node_id** |
| `backend/services/backup_service.py` | Remote-Backup-Stream vom Agenten |
| `backend/routers/servers.py` | Create mit `node_id`, Response `node_name` |
| `backend/schemas/server.py` | `node_id` create/response, `node_name` |
| `backend/tests/test_node_client.py` | NodeClient + Port-Scope |
| `backend/tests/test_nodes_router.py` | Nodes-API / Token-Leak-Schutz |

---

## Phase 3 — Frontend Node-UI & Server-Create

| Datei | Rolle |
|-------|--------|
| `frontend/src/stores/nodeStore.ts` | Zustand Node-State |
| `frontend/src/pages/AdminNodes.tsx` | `/admin/nodes` Verwaltung |
| `frontend/src/Singra/UI/ProgressBar.tsx` | CPU/RAM-Balken |
| `frontend/src/pages/Servers.tsx` | Create: Node-Auswahl (Pflicht, sobald Nodes geladen) |
| `frontend/src/pages/ServerDetail.tsx` | Node in Header/Netzwerk-Info |
| `frontend/src/App.tsx` | Route `/admin/nodes` |
| `frontend/src/components/layout/Sidebar.tsx` | Nav „Nodes“ (Owner) |
| `frontend/src/services/routeAccess.ts` | Route-Guard `nodes` (Owner-only) |
| `frontend/src/services/routeAccess.test.ts` | Guard-Tests |
| `frontend/src/types/index.ts` | `Node`, `Server.node_id` / `node_name` |
| `frontend/src/locales/de.json` | i18n Nodes/Servers |
| `frontend/src/locales/en.json` | i18n Nodes/Servers |

Bestehende UI-Primitives (behalten, werden genutzt):  
`frontend/src/components/ui/Badge.tsx`, `Dropdown.tsx`, `PasswordInput.tsx`, …

---

## Phase 4 — Frontend entkoppeln (Vercel-Ready)

| Datei | Rolle |
|-------|--------|
| `frontend/src/config/api.ts` | `API_BASE` / `apiUrl` / `wsUrl` via `VITE_API_URL` / `VITE_WS_URL` |
| `frontend/src/api/client.ts` | Absolute/relative API, CSRF-Memory aus `X-CSRF-Token` |
| `frontend/src/hooks/useWebSocket.ts` | WS über `wsUrl()` (split origin) |
| `frontend/vercel.json` | SPA-Rewrites für Vercel |
| `frontend/.env.example` | Vite-Env-Vorlage: `VITE_API_URL`, `VITE_WS_URL` (**keine** Secrets) |
| `backend/.env.example` | Vollständige Backend-Env inkl. Phase-4 (`MSM_CORS_*`, `MSM_COOKIE_CROSS_SITE`, `MSM_SERVE_FRONTEND`) |
| `frontend/vite.config.ts` | Proxy `/api` + `ws: true` (local same-origin) |
| `backend/config.py` | `cors_allowed_origins`, `cookie_cross_site`, `serve_frontend`, `get_cors_origins()` |
| `backend/cookies.py` | SameSite=None wenn cross-site; `X-CSRF-Token` Header |
| `backend/main.py` | CORS + expose CSRF-Header, CSP `connect-src`, `MSM_SERVE_FRONTEND` |
| `backend/routers/auth.py` | `/me` echo `X-CSRF-Token` |
| `backend/routers/servers.py` | WS-Origin-Allowlist = CORS-Liste |
| `backend/tests/test_phase4_frontend_decouple.py` | CORS/CSP/CSRF-Header-Tests |
| `backend/.env.example` | Phase-4 Env-Doku |

**Split-Hosting (Prod):**  
`MSM_CORS_ALLOWED_ORIGINS`, `MSM_COOKIE_CROSS_SITE=true`, `MSM_SERVE_FRONTEND=false`, Frontend-Build mit `VITE_API_URL=https://api…`.

---

## Security-Invarianten (über alle Phasen)

1. Agent-Token: DIS-verschlüsselt in DB (`auth_token_enc`, AAD `msm:node:auth_token`).
2. Klartext-Token nie in Logs, API-Responses, Frontend-State nach Submit.
3. Agent: kein DIS/Crypto; Bearer auf allen Routen außer `GET /health`.
4. Path-Traversal nur innerhalb `MSM_SERVERS_DIR/<server_id>`.
5. Docker-Hardening: kein privileged, `cap_drop=ALL`, kein host-network.
6. Ports pro Node; Remote ohne lokalen Host-Bind-Check.

---

## Runtime / Secrets (lokal behalten, nicht ins Git)

| Pfad | Hinweis |
|------|---------|
| `backend/.env` | Panel-Secrets (gitignored) |
| `msm-agent/.env` | `MSM_AGENT_TOKEN` (gitignored) |
| `backend/msm.db` | Dev-DB inkl. Nodes (gitignored `*.db`) |

---

## Kurz-Check: „Ist Phase X noch da?“

```bash
# Phase 0
test -f backend/models/node.py && test -f backend/scripts/migrate_add_nodes.py

# Phase 1
test -f msm-agent/main.py && test -f msm-agent/services/docker_service.py

# Phase 2
test -f backend/services/node_client.py && test -f backend/routers/nodes.py

# Phase 3
test -f frontend/src/pages/AdminNodes.tsx && test -f frontend/src/stores/nodeStore.ts

# Phase 4
test -f frontend/src/config/api.ts && test -f frontend/vercel.json
```

PowerShell:

```powershell
@(
  'backend/models/node.py',
  'backend/scripts/migrate_add_nodes.py',
  'msm-agent/main.py',
  'backend/services/node_client.py',
  'backend/routers/nodes.py',
  'frontend/src/pages/AdminNodes.tsx',
  'frontend/src/stores/nodeStore.ts',
  'frontend/src/config/api.ts',
  'frontend/vercel.json',
  'backend/tests/test_phase4_frontend_decouple.py'
) | ForEach-Object { if (Test-Path $_) { "OK $_" } else { "MISSING $_" } }
```
