## GitHub-Source: npm Race robust mit `--network-concurrency=1` + 3 Retries

- **`blueprints/github_source._run_argv_with_retry`** — erweitert:
  - `npm ci`/`npm install`/`npm i`/`npm add`/`npm update` bekommen jetzt
    **automatisch** `--no-audit --no-fund --prefer-offline` (verhindert
    parallele Audit-/Funding-Calls, die TAR_ENTRY_ERROR triggern können).
  - Bei einem TAR_ENTRY_ERROR-Fehler: bis zu **3 Retries** (insgesamt 4 Läufe)
    mit exponentiellem Backoff (5s / 15s / 30s). Vor jedem Retry wird
    `cwd/node_modules` komplett aufgeräumt.
  - **Ab dem zweiten Lauf** wird zusätzlich `--network-concurrency=1` injiziert.
    Das eliminiert den pacote-Parallel-Race komplett (single-threaded
    Downloads + Extraktion).
  - `--prefer-offline` bleibt in **jedem** Lauf erhalten, damit der Cache
    (auf singra: `/opt/msm/.npm/_cacache`) genutzt wird und kein zweiter
    Download-Sturm entsteht.
  - Existierende User-Flags (`--no-audit`, `--no-fund`, …) werden nicht
    dupliziert.
- **`_is_npm_install`** überspringt jetzt CLI-Flags bei der Subcommand-Suche,
  damit die Erkennung auch nach der Flag-Injection noch greift (Bugfix: argv[1]
  war sonst ein Flag statt `ci`).

### Hintergrund

Der erste Versuch (v1.2.11) hat einmal retried, aber auf `singra` mit
rootless Docker + overlayfs reicht das nicht — der pacote-Race ist nicht
transient, sondern entsteht reproduzierbar bei jeder parallelen Extraktion.
Die Lösung ist **Single-Threading auf Netzwerk/IO-Ebene** statt nur Retry.

Konkret auf singra: Mit Default-Concurrency `npm ci` öffnet ~8 Worker
parallel, die alle `node_modules/<pkg>/...` anlegen wollen, bevor das
Elternverzeichnis existiert. Resultat: Hunderte `TAR_ENTRY_ERROR ENOENT`
münden in Exit-Code 1, weil `npm ci` (im Gegensatz zu `npm install`) bei
Inkonsistenzen komplett abbricht.

### Sicherheit

- **Keine** Änderung am Token-/URL-Handling, `_git_env()` bleibt unverändert.
- Keine neuen ENV-Variablen, keine neuen Subprozesse (nur `subprocess.run` mit
  mehr Argumenten).
- Cleanup ist strikt auf `cwd/node_modules` beschränkt — `.git`,
  `package.json`, `package-lock.json`, `.env` und alle User-Configs bleiben
  unangetastet.
- Retry bricht nach 4 Läufen ab (kein Silent-Hang); andere npm-Fehler und
  Nicht-npm-Befehle werden **nicht** retried.

### Tests

- **4 neue Tests** in `tests/test_blueprint_github_source.py`:
  - `test_setup_command_retries_until_exhausted` — nach 4 Läufen sauberer
    Fehler (kein Hang).
  - `test_setup_command_injects_stabilize_flags_on_npm_ci` — Flags landen
    zwischen `npm` und `ci`, ohne Doppelung.
  - `test_setup_command_does_not_inject_flags_for_npm_run` — `npm run build`
    bleibt unverändert (keine Störung von Build-Schritten).
  - `test_setup_command_inject_does_not_duplicate_existing_flags` — User-Flags
    werden respektiert.
  - `test_setup_command_retry_adds_network_concurrency=1` — erst beim Retry
    wird single-threaded gezwungen.
- Bestehende 5 Tests aktualisiert + alle Blueprint-Tests weiterhin grün
  (insgesamt **131 Blueprint-Tests grün**).

### Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.12`), `systemctl restart msm-panel`.
- **Kein** Blueprint-Edit nötig.
- **Keine** neuen Dependencies, **keine** Migrationen.

### Auswirkung auf `singra_backend`

Der vorherige Fehlertext
```
npm warn tar TAR_ENTRY_ERROR ENOENT: .../node_modules/es-abstract/2025/SetFunctionLength.js
```
sollte jetzt mit der ersten Install-/Reinstall-Aktion verschwinden. Wenn der
Cache (`/opt/msm/.npm/_cacache`) noch sauber ist, schafft es `npm ci` mit den
neuen Flags im ersten Anlauf; sonst rettet der erste oder zweite Retry mit
`--network-concurrency=1` die Installation.
