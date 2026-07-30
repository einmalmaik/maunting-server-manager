# MSM Intelligent Engine (AI Integration) — Architectural Planning & Specifications

> **Notice:** Confidential internal planning document for Maunting Server Manager (MSM).  
> **Target Version:** Next Major Update (`v4.0.0`)  
> **Status:** Draft / Conceptual Planning Phase  

---

## 1. Vision & Core Philosophy

MSM will evolve into an **intelligent, AI-assisted Server Management Platform**. Rather than a basic chatbot overlay, the AI operates as a deeply integrated **Autonomous Operations & Diagnostics Assistant**.

### Key Principles
1. **Security & Privacy First (BYOK - Bring Your Own Key):**
   - Users provide their own API key (OpenAI, Anthropic, Gemini) or specify a local Ollama / vLLM endpoint.
   - API keys are encrypted at rest via **DIS Shield (AES-256-GCM)**.
   - Keys are **never** logged, exposed in frontend states, or leaked in error stacktraces.
2. **Centralized Panel Orchestration (Multi-Node Isolation):**
   - The AI engine runs strictly within the **Central MSM Panel (Backend)**.
   - Node agents (`msm-agent` / Guardian Engine) remain lightweight, fast, and free of AI dependencies.
   - Panel queries Node Agents via signed JWT API calls.
3. **Structured Function Calling (No Raw Arbitrary Shell Execution):**
   - The LLM does **not** generate unvalidated shell/bash scripts.
   - Interaction happens exclusively through deterministic **Tool Calls** (Read Logs, Parse Configs, Search Workshop Mods, Propose Patches).
4. **Human-in-the-Loop (Safety Guardrails):**
   - Any configuration change, mod installation, or destructive action requires explicit user confirmation via visual Diffs in the Dashboard UI.
5. **Generative Log Scrubbing:**
   - Pre-processing pipeline scrubs sensitive environment variables, RCON passwords, tokens, and private IPs from log buffers prior to LLM submission.

---

## 2. System Architecture & Component Design

```
+-------------------------------------------------------------------+
|                        MSM Dashboard (UI)                         |
|   [ AI Assistant Drawer / Log Diagnostic Tool / Mod Installer ]   |
+-------------------------------------------------------------------+
                                  | (HTTPS / WS)
                                  v
+-------------------------------------------------------------------+
|                     Central MSM Panel Backend                      |
|                                                                   |
|   +-----------------------+     +-----------------------------+   |
|   |  BYOK Key Vault       |     |  AI Service Orchestrator    |   |
|   |  (DIS Shield Encrypt) |     |  (Provider Adapter & Tools) |   |
|   +-----------------------+     +-----------------------------+   |
|                                                |                  |
+------------------------------------------------+------------------+
                                                 | (Tool Call / gRPC)
                                                 v
                                  +-----------------------------+
                                  |    Guardian Node Agent      |
                                  |   (Log Reader / Mod Sync)   |
                                  +-----------------------------+
```

### Supported AI Providers
* **OpenAI:** `gpt-4o`, `gpt-4o-mini`
* **Anthropic:** `claude-3-5-sonnet`, `claude-3-haiku`
* **Google Gemini:** `gemini-1.5-pro`, `gemini-2.0-flash`
* **Local / Self-Hosted:** Ollama, vLLM, LocalAI (OpenAI-compatible `/v1/chat/completions` API)

---

## 3. Generic Game & Mod Architecture (Blueprints + Adapters)

To support any game server (Steam Workshop, CurseForge, Modrinth, direct files, INI/JSON/TOML configs) in a generic way, the Blueprint Schema is extended:

### Blueprint Schema Extension (`blueprints/schema.json`)
```json
{
  "ai_diagnostics": {
    "log_paths": ["Saved/Logs/Server.log"],
    "known_error_patterns": [
      {
        "pattern": "Failed to load mod '(?P<mod_id>.*)'",
        "category": "mod_missing",
        "action_hint": "search_mod"
      }
    ]
  },
  "mod_system": {
    "provider": "steam_workshop | curseforge | modrinth | custom_file",
    "mod_directory": "Mods/",
    "config_mapping": [
      { "path": "Config/ServerSettings.ini", "format": "ini" }
    ]
  }
}
```

### Mod Provider Adapters
* `backend/services/mod_providers/base.py`: Abstract Base Class for Mod Managers.
* `backend/services/mod_providers/steam_workshop.py`: Handles Steam AppID & PublishedFileIDs.
* `backend/services/mod_providers/curseforge.py`: Curated API client for CurseForge games.
* `backend/services/mod_providers/modrinth.py`: Modrinth API client for Minecraft/open mods.

---

## 4. Defined LLM Tools (Function Calling Interface)

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `get_server_logs` | `server_id`, `lines`, `filter_level` | Retrieves scrubbed log snippet from Node. |
| `read_server_config` | `server_id`, `relative_path` | Parses specified configuration file. |
| `propose_config_patch` | `server_id`, `relative_path`, `diff` | Generates a unified diff for user UI review. |
| `search_mod_catalog` | `game_id`, `query` | Searches configured mod provider for compatible mods. |
| `analyze_node_capacity` | `server_id` | Checks node CPU/RAM overcommit and suggests sizing. |

---

## 5. Development Roadmap & Phases

- [ ] **Phase 1: BYOK Provider Engine & DIS Key Storage**
  - Implement Provider API client (OpenAI, Anthropic, Gemini, Ollama).
  - Secure credential storage & log scrubbing middleware.
- [ ] **Phase 2: AI Log Analyzer & Troubleshooting (Read-Only)**
  - Add "Analyze Crash / Logs" button in Server Terminal & Logs view.
  - LLM parses logs using known patterns + context and outputs structured diagnoses.
- [ ] **Phase 3: Blueprint Extension & Mod Adapters**
  - Extend blueprint format for log patterns and mod providers.
  - Implement Steam Workshop, CurseForge, and Modrinth adapters.
- [ ] **Phase 4: Interactive Assistant UI & Action Execution**
  - Add AI Chat drawer in Panel UI.
  - Implement Visual Diff preview for 1-click config fixes with human-in-the-loop approval.
- [ ] **Phase 5: Intelligent Capacity & Scale Assist**
  - Connect Node Capacity metrics (CPU/RAM allocatable) with AI recommendations for server resource allocation.
