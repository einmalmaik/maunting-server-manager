# MSM v1.4.7 — Docker-Exec-Tab (Container-Befehle mit argv-Sicherheit)

## Übersicht

Neuer Tab **"Exec"** im Server-Detail, neben "Konsole". Authentifizierte
User mit der neuen Permission `server.console.exec` können damit
einmalige Befehle **im MSM-Container des Servers** ausführen — argv-basiert,
nicht als Shell-String. Output (stdout+stderr) erscheint im Live-Stream
des Servers, damit Logs und Exec-Ausgaben im selben UI-Fenster sichtbar
sind.

**Use-Case:** Auf dem Singra-Discord-Bot willst du `npm ci` neu triggern,
`ps aux` aufrufen, oder `cat .env` lesen, ohne den Container manuell
attachen zu müssen. Gleicher Use-Case für jeden Container-Server-Typ.

## Sicherheits-Garantien (alle durch Tests bewiesen)

1. **Kein Host-Exec.** Der Zielcontainer-Name kommt ausschließlich aus
   `container_name_for(server.id)`. Es gibt kein Request-Feld, mit dem
   der User den Container beeinflussen kann. Host-Exec oder der
   Container eines anderen Servers sind strukturell ausgeschlossen.

2. **Kein Shell-Escape.** Das Befehls-Array wird 1:1 an
   `container.exec_run(argv)` weitergereicht — **niemals** ein
   `["sh", "-c", userstring]`. Ein `; rm -rf /` im argv wäre ein
   literaler Dateiname, nicht von einer Shell interpretiert. Test:
   `test_exec_endpoint_runs_argv_verbatim_no_shell` schickt exakt
   `["ls", "/data; rm -rf /tmp/x", "--with-dash"]` durch und prüft,
   dass genau dieses argv bei `exec_in` ankommt.

3. **Separate Permission `server.console.exec`.** Nicht zusammengelegt
   mit `console.write`. Wer Exec bekommt, bekommt es explizit.
   Mit-registriert: vorbestehender Bug `server.update` (wurde im
   `webhooks_outbound`-Router benutzt, war aber nirgends definiert).

4. **Blueprint-Gate `runtime.enableExec=true`.** Default `false`.
   Auch Owner mit Permission bekommen 403, wenn der Server-Blueprint
   Exec nicht aktiviert hat. So bleibt ein "neuer Exec-User pro
   Server"-Workflow sauber (Server-Owner aktivieren Exec pro Blueprint).

5. **Limits:** 1..32 Argumente (Pydantic min/max_length), je max
   4096 Zeichen. Timeout 1..600 Sekunden aus Blueprint
   `runtime.execTimeoutSeconds`. Output gedeckelt auf 256 KiB mit
   UTF-8-sicherem `[truncated]`-Marker.

6. **Audit-Log ohne Output.** `logger.info("exec ... server=%d user=%s
   argv=%r", ...)` schreibt Server-ID, User-ID und argv. **Niemals**
   stdout/stderr (kann sensible Daten enthalten). Test:
   `test_run_in_container_writes_audit_log_with_argv_not_output`
   prüft explizit, dass `secret-payload-NEVER-LOG-12345` nicht im Log
   auftaucht.

7. **Generische Error-Messages.** 500/504 mit kurzen deutschen Texten,
   keine internen Pfade/Stacktraces im Response.

## Aktivierung im Blueprint

```json
"runtime": {
  "image": "node:22-bookworm-slim",
  "startup": "./start.sh",
  "enableExec": true,
  "execTimeoutSeconds": 120
}
```

Der Singra-Discord-Bot-Blueprint (`blueprints/community/singra_backend.blueprint.json`)
ist bereits mit `enableExec: true` und `execTimeoutSeconds: 120` ausgestattet.
Beim nächsten Reinstall/Update des Bots ist der Tab direkt verfügbar.

## UI-Änderungen

- Server-Detail: Neuer Tab "Exec" zwischen "Konsole" und "Mod-Manager"
  (nur sichtbar, wenn `gameInfo.enable_exec` aus `/system/games` true ist).
- `ServerConsolePanel` hat einen neuen `mode`-Prop (`'console' | 'exec'`).
  Default `'console'` → bestehende Konsumenten unverändert.
- Im Exec-Modus: Input-Feld mit Whitespace-Splitting → argv-Array,
  Send-Button "Ausführen", Output als Stream-Zeilen mit
  `source: 'exec'`.
- Lokalisierung: `tabs.exec` in DE/EN, BlueprintsDocs-Sektion
  `docs-exec` mit Sicherheits-Erklärung.

## Tests

- **Backend:** 124 passed im Backend-Sweep
  (`pytest tests/test_exec_service.py tests/test_servers_router.py
  tests/test_blueprint_schema.py tests/test_permission_catalog.py`).
  Davon **11 neue exec_service-Tests** + **9 neue Endpoint-Tests** +
  **6 neue Schema-Tests** + **1 erweiterte Permission-Catalog-Prüfung**.

- **Frontend:** 135 passed (`npm run test`).

- TypeScript-Check: `npx tsc --noEmit` ohne Fehler.

## Verifikation

Manuell reproduziert auf `/opt/msm/servers/singra_backend_80`:

- `git rev-parse HEAD` → `0570540a69ec` (PR #17 mit `temp-voice`-
  Migrationen, alle auf Disk).
- Working-Tree clean (bis auf eine bewusst lokale Modifikation an
  `migrations.meta.json`, die du mit `git checkout -- migrations/
  postgres/migrations.meta.json` zurücksetzen kannst).

Keine neuen Dependencies. Keine API-Brüche (additive Permission,
additive Blueprint-Felder mit Default `false`).

## Migration

Keine Migration nötig. Server mit Blueprints ohne `enableExec`
verhalten sich exakt wie vorher (Tab nicht sichtbar, Endpoint gibt
403 für alle — auch für Owner — bis der Blueprint opt-in'ed).

## Tagging-Status

**Nicht** getaggt. Diese Notes-Datei wird committed, das Tagging
macht der User (du) manuell, wenn er bereit ist, v1.4.7 zu releasen.
Damit bleibt der Release-Zeitpunkt in deiner Hand.

## v1.4.7 Nachtraege -- GitHub-Source-Reliability & Blueprint-Haertung

### GitHub-Source: Race-Toleranz bei lokalem Branch (fix)

**Problem:** install_github_source brach mit 'git fehlgeschlagen (128):
fatal: a branch named master already exists' ab, wenn der Blueprint-Branch
lokal bereits existierte (Normalfall nach erstem Install). Die alte
show-ref --verify + konditionale git branch Sequenz hatte ein
TOCTOU-Race-Window, das bei parallelen Restart-Versuchen reproduzierbar
auftrat.

**Fix:** Neue idempotente Helferfunktion _create_local_branch_if_missing
fuehrt git branch <name> origin/<name> immer aus und schluckt nur den
benignen already-exists-Fehler. Alle anderen Fehler eskalieren weiter.
Der nachfolgende git reset --hard origin/<branch> synct in beiden Faellen.

### Config-Restore: tar --no-same-owner (Haertung)

**Problem:** restore_manual_configs extrahierte Config-Backups mit der
im Archiv eingebetteten UID. Bei UID-Mismatches (rootful vs. rootless Docker,
Pterodactyl-Recycle) scheiterte tar -xf mit Exit 2.

**Fix:** --no-same-owner Flag -- Restore laeuft unter der UID des
MSM-Panel-Workers (msm), nicht unter einer archivierten UID.

### Blueprint: setupCommands fuer Singra-Discord-Bot

setupCommands auf npm ci, build:api, build:bot, chmod +x start.sh
aktualisiert. build:web bewusst weggelassen (Frontend laeuft auf Vercel).
Damit MSM bei jedem Restart automatisch pullt, baut und startet -- kein
manueller Build mehr noetig.

### Tests

- Neuer Regressionstest: test_pull_tolerates_local_branch_already_exists_race
- 99 Blueprint-Tests gruen (test_blueprint_github_source + test_blueprints_router
  + test_blueprint_schema)
- Beweis: Alter Pfad git branch main origin_main -> Exit 128; neuer Helper
  -> already exists geschluckt, Reset laeuft durch.
