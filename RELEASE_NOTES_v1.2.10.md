## CI-Reparatur — alle 863 Backend-Tests grün, Frontend 135/135

Fünf Backend-Tests waren seit vor v1.2.9 in GitHub Actions rot (lokal
mit `pytest -q` reproduzierbar). Jeder Fail einzeln root-caused, jede
Korrektur dort, wo sie hingehört (Production-Code oder Test — nicht
beides).

### 1) `check_workshop_mod_updates` — Runtime-Check bei `unknown`

`backend/games/updater.py`: Der Runtime-Target-Check (Conan/DayZ
`workshop_runtime_targets_ready`) feuerte eine `install: missing_runtime_copy`-
Action auch dann, wenn der Update-Stand `unknown` war (kein lokales
`last_updated` und/oder kein Steam-API-Key). Wir können aber nicht
behaupten, eine Mod sei veraltet, wenn wir ihre Version gar nicht kennen.
Der Check läuft jetzt nur noch bei `update_status in ("up_to_date",
"outdated")` — bei `unknown`/`missing` bleibt die Action `none`, der
Mod-Status wird nur persistiert.

→ `test_installed_mod_without_metadata_is_marked_unknown_not_updated` und
`test_installed_mod_without_metadata_stays_unknown_when_remote_metadata_exists`
grün. Happy-Path `test_check_workshop_clears_stale_pending_when_files_present`
bleibt grün.

### 2) Blueprint-Validator nennt die Verletzer

`backend/blueprints/schema.py`: Bei `source.type=manualUpload` plus
unerwarteten Cross-Source-Feldern nennt der Validator die **konkreten**
Feldnamen (`steam`, `http`, `github`) im Error. Operator sieht sofort,
welches Feld reingerutscht ist. Substring `steam/http` bleibt enthalten
(Test-Konformität).

### 3) Test an reales Start-Verhalten angepasst

`backend/tests/test_docker_service.py::test_start_continues_with_warning_*`:
Commit `b293b97` hatte das Start-Verhalten bewusst auf „best-effort +
Console-Warning" umgestellt (siehe `references/msm-permission-repair-chmod-eperm-
rootless.md`). Der alte Test forderte noch das alte strikte
Hard-Stop-Verhalten — das wäre eine Regression und würde Memory brechen.
Test umgeschrieben, dass er das **echte** Verhalten absichert: Repair
fail → Start läuft weiter, `run_container` wird gerufen, Result ist
„Server gestartet", kein Hard-Error.

### 4) Workshop-Batch: eigentliche Invariante scharf stellen

`backend/tests/test_docker_service.py::test_workshop_batch_download_uses_one_ephemeral_container_for_many_mods`:
Seit dem Rootless-Docker-Bind-Mount-Visibility-Fix ruft
`run_steamcmd_workshop_download_batch` zwei `run_ephemeral`-Container
auf — den SteamCMD-Batch und den `repair_bind_mount_permissions`-Pass.
Die **eigentliche** Invariante ist „genau ein SteamCMD-Batch-Container
für N Workshop-Items", nicht „genau ein run_ephemeral-Call überhaupt".
Test filtert jetzt nach `+workshop_download_item` im Command und
assertiert `== 1`. Der Repair-Pass darf zusätzlich laufen.

## Tests

- **Backend: 863 passed, 0 failed** (vorher: 858 passed + 5 failed).
- **Frontend: 135/135 vitest grün, `tsc + vite build` grün.**
- Keine neuen Dependencies, keine Migrations.

## Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.10`), `npm run build`,
  `systemctl restart msm-panel`.
- Keine Breaking Changes an APIs oder DB-Schema.
- Tests laufen jetzt in CI durch — kein roter Build mehr für jeden Push
  auf `main`.

**Full Changelog**: https://github.com/einmalmaik/maunting-server-manager/compare/v1.2.9...v1.2.10
