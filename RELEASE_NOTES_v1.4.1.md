## Update-Pfad für GitHub-Blueprints (Reproducible Rebuilds)

Mit v1.4.1 zieht MSM Commits aus GitHub-Blueprints (z. B. Discord-Bot,
Voice-Tools, Apps) auch dann zuverlässig neu, wenn das lokale Repo einem
anderen User gehört als der MSM-Prozess. Voraussetzung: das Repo wird vor
dem Container-Start frisch gebaut — `npm ci`, `build:api`, `build:bot`.
Vorher konnte ein einziges `dubious ownership`-Problem jeden Pull
blockieren; dann lief der Container mit dem alten Snapshot weiter, ohne
dass ein Build-Fehler sichtbar wurde.

### Bugfixes

- **GitHub-Source: Dubious-Ownership-Schutz pro Aufruf umgehen**
  - Patch in `backend/blueprints/github_source.py` (`_ensure_safe_directory`).
  - Setzt `GIT_CONFIG_COUNT` + `GIT_CONFIG_KEY_0=safe.directory` +
    `GIT_CONFIG_VALUE_0=*` als Prozess-Env **vor** jedem `git fetch`/`reset`
    und vor `local_repo_sha`. Damit umgeht MSM die Ownership-Prüfung pro
    Subprozess — kein dauerhafter globaler State, robust gegen Cron-Timer
    (`msm-update.timer`) und Container-Restarts.
  - Optional wird zusätzlich `safe.directory=<repo-pfad>` in `.git/config`
    geschrieben — das hilft externen CLI-Aufrufen, die ohne den Env-Trick
    kommen.
  - Wirkt in `_run_git`, `local_repo_sha`, `remote_branch_sha`. Kein
    `git config --global` mehr nötig.

### Sicherheit / KISS

- **Keine** `safe.directory=*` global oder systemweit — alles in
  Prozess-Env oder per-Repo lokal (`.git/config`).
- Defensive Try/except um die per-Repo-Schreibung: Ein Fehler hier
  eskaliert nicht; `_run_git` selbst meldet den eigentlichen
  Fetch-/Reset-Fehler sauber.
- Idempotent, mehrfache Aufrufe schreiben denselben Eintrag.

### Verifikation (live, ohne globalen `safe.directory`)

- `install_github_source(...)` auf `singra_backend_80` lief in 11 s mit
  `{'ok': True, 'commit': 'b71de2ec0…', 'branch': 'master'}` durch.
- `dist/api/apps/api/src/index.js` und `dist/bot/apps/bot/src/index.js`
  wurden frisch gebaut (02:42:33 / 02:42:36, 219 Artefakte neuer als
  `package.json`).
- `restart_server_with_updates` über MSM-Lifecycle: 7 s, Container neu
  gestartet, `modules registered`, `discord: ready`, alle Migrationen
  per `AUTO_MIGRATE_ON_BOOT` angewendet.

### Auswirkungen auf Blueprints / User

- Discord-Bot-Blueprint (`blueprints/community/singra_backend.blueprint.json`)
  und alle anderen `source.type=github`-Blueprints funktionieren wieder
  mit Pull + Build bei jedem Restart, ohne manuellen Eingriff.
- Keine API-Änderung, keine DB-Migration, kein Frontend-Touch.
- Bestehende Global-Einträge in `git config --global` werden **nicht**
  entfernt — falls schon vorhanden, schadet der Patch nicht.

### Geänderte Dateien

- `backend/blueprints/github_source.py` (+62 / −3)
- `RELEASE_NOTES_v1.4.1.md`
