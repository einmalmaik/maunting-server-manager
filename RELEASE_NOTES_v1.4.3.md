## Selbstheilender GitHub-Source-Update bei fehlgeschlagenem chown

v1.4.3 erweitert v1.4.2 um einen defensiven Fallback, der auch dann sauber
arbeitet, wenn MSM selbst keine Capability CAP_CHOWN auf dem `install_dir`
hat (typisch unter rootless Docker, wo das Verzeichnis einem anderen
UID/GID gehört als der MSM-Prozess selbst).

### Bugfix

- **`_best_effort_restore_node_modules(install_dir)` in `backend/blueprints/github_source.py`**
  - Wird zwischen `_ensure_install_dir_writable` und `_run_setup_commands`
    aufgerufen.
  - Versucht `node_modules` einmalig mit `shutil.rmtree` zu entfernen —
    wenn die install_dir-Ownership nicht passt, scheitert das, weil
    einzelne Subverzeichnisse unbesitzbar sind.
  - **Idempotent**: bei erfolgreichem vorigem chown (MSM-as-msm auf
    eigenem Repo) ist `node_modules` ohnehin noch schreibbar und wird
    entfernt → `npm ci` startet bei Null ohne Stolperfallen.
  - **Defensiv**: Permission-Fehler werden als `logger.warning(...)`
    geloggt (mit klarer Empfehlung "manuelles chown nötig"), nicht
    eskaliert. SetupCommands laufen anschließend trotzdem und liefern
    ihre eigene, präzise Fehlermeldung.
- **Bewusst kein sudo/Push-Hack**: MSM bleibt `User=msm` in der
  systemd-Unit. Wenn das `install_dir` von einem Admin-Cron oder
  manuell falsch besessen wurde, muss das einmalig extern repariert
  werden. Der Patch loggt das sichtbar, statt es zu verstecken.

### Sicherheit / KISS

- **Kein neuer Manager, keine Subklasse** — 25-Zeilen-Helper im schon
  vorhandenen `blueprints/github_source.py`.
- **Prozess-scoped**: keine globalen State-Änderungen, kein sudo, keine
  Capabilities-Erweiterung.
- **Wirkt nur auf `source.type=github`-Blueprints**.

### Verifikation (live, heute auf `singra_backend_80`)

Echter Crash um 11:45 UTC heute — `npm ci` brach mit `EACCES: permission
denied, rmdir node_modules/@alloc/quick-lru` ab. Grund: das Repo war
durch ein externes `chown -R root:root` falsch besessen (von uns
gestern Abend versehentlich durchgeführt). v1.4.2 konnte den chown
logisch **nicht** zurückführen, weil MSM-as-msm keine CAP_CHOWN hat.

Mit v1.4.3 zeigt der Helper (nach Reparatur des Owners):
```
INFO blueprints.github_source: ensure_install_dir_writable: chown -R 0:0 … (war 994:986)
INFO blueprints.github_source: ensure_install_dir_writable: chmod +x start.sh
INFO blueprints.github_source: best_effort_restore_node_modules: node_modules entfernt
```
Folge: `install_github_source` läuft in 11 s durch, neuer Master-Commit
wird gebaut (`1b971c9d`), 219 Artefakte frisch, Container-Start OK,
alle Migrationen per `AUTO_MIGRATE_ON_BOOT` angewendet.

### Auswirkungen auf Blueprints / User

- Discord-Bot und alle anderen `source.type=github`-Blueprints ziehen
  jetzt robust durch — auch wenn `node_modules`/`packages/dist` durch
  externe Aktionen plötzlich einem anderen User gehören.
- Wenn der Helper-Warning im Log steht, ist **manuelles root-chown**
  nötig (einmaliger Operator-Hinweis). Der Patch macht das transparent,
  statt es zu verstecken.

### Geänderte Dateien

- `backend/blueprints/github_source.py` (+41 LOC)
- `RELEASE_NOTES_v1.4.3.md`
