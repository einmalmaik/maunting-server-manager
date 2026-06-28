## GitHub-Source: npm `TAR_ENTRY_ERROR` wird jetzt automatisch retried

- **`blueprints/github_source._run_argv_with_retry`** — neuer, klar abgegrenzter
  Helper, der ein `setupCommand` ausführt. Wenn der Befehl `npm`/`npx` ist und
  stderr/stdout den bekannten Parallel-Extraktions-Race
  `npm warn tar TAR_ENTRY_ERROR ENOENT` enthält, wird `node_modules` im
  Arbeitsverzeichnis aufgeräumt und der **gleiche** argv **genau einmal**
  wiederholt.
- **Andere npm-Fehler** (z. B. `npm ERR! missing script`) werden **nicht**
  retried — Retry würde nichts retten und würde nur Log-Noise erzeugen.
- **Nicht-npm-Befehle** (`pip`, `pnpm`, `yarn`, …) werden ebenfalls nicht
  retried, selbst wenn `ENOENT` im Output steht. Das Pattern matcht explizit
  auf `npm warn tar TAR_ENTRY_ERROR`.

### Hintergrund

Beim ersten Klick auf **Reinstall** für `singra_backend` schlug das
`npm ci` mit

```
npm warn tar TAR_ENTRY_ERROR ENOENT: no such file or directory,
open '/opt/msm/servers/singra_backend_77/node_modules/es-abstract/2025/SetFunctionLength.js'
```

fehl. Ursache: klassischer Race in `pacote`/npm, wenn parallele Worker Dateien
in Unterordnern anlegen wollen, bevor das Elternverzeichnis existiert. Auf
**singra** (rootless Docker + overlayfs) tritt das gehäuft auf. Lösung ist
einfach `node_modules` entfernen und erneut laufen lassen — der Race ist
nicht-deterministisch.

### Sicherheit

- **Keine** Änderung am Token-/URL-Handling. `_run_argv_with_retry` nutzt
  weiterhin `_git_env()` ohne neue ENV-Variablen.
- **Keine** Änderung am `subprocess.run`-Aufruf (gleiches `cwd`, gleiches
  `timeout=900`, gleiches `capture_output=True`). Der einzige Unterschied ist,
  dass ein Retry passieren kann.
- Cleanup ist auf `cwd / "node_modules"` beschränkt — nichts anderes wird
  angefasst, insbesondere nicht `.git`, `package.json`, `package-lock.json`
  oder User-Configs.

### Tests

- **5 neue Tests** in `tests/test_blueprint_github_source.py`:
  - `test_setup_command_retries_npm_tar_entry_error` — Happy-Path mit Retry.
  - `test_setup_command_no_retry_on_other_npm_errors` — andere npm-Fehler
    propagieren ohne Retry.
  - `test_setup_command_no_retry_for_non_npm` — `pip`/`python` werden nicht
    retried, auch wenn `ENOENT` im Output steht.
  - `test_setup_command_retry_also_fails` — Retry schlägt ebenfalls fehl →
    Fehler wird sauber propagiert (kein Silent-Fail).
  - `test_setup_command_first_run_succeeds` — Happy-Path: kein Retry, kein
    Cleanup.
- Bestehende 5 Blueprint-GitHub-Source-Tests bleiben grün.
- 127 Blueprint-Tests insgesamt weiter grün.

### Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.11`), `systemctl restart msm-panel`.
- **Kein** Blueprint-Edit nötig — bestehende `setupCommands` (auch
  `["npm", "ci"]`) funktionieren ohne Anpassung robuster.
- **Keine** neuen Dependencies, **keine** Migrationen, **keine** API-Änderung.
