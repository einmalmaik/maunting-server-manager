## Self-healing für install_dir-Ownership und Start-Skript-Bits

Mit v1.4.2 normalisiert MSM das `install_dir` jedes GitHub-Servers
**vor** jedem Setup-Command-Lauf automatisch: Setzt den Owner auf den
laufenden MSM-Prozess und stellt sicher, dass `start.sh` (oder ein
anderes Top-Level-`*.sh`) ausführbar ist. Damit verschwindet eine
Bug-Klasse, die heute Morgen einen Discord-Bot-Container mit Exit-Code 1
geknockt hat.

### Bugfixes

- **Dubious-Ownership + fehlende Execute-Bits reparieren sich jetzt selbst**
  - Neuer Helper `_ensure_install_dir_writable(install_dir)` in
    `backend/blueprints/github_source.py`.
  - Wird in `install_github_source` direkt **nach** dem Git-Block
    (fetch/reset bzw. clone) und **vor** `_run_setup_commands` aufgerufen.
  - **chown**: `chown -R <os.getuid()>:<os.getgid()> <install_dir>`,
    **nur wenn** der aktuelle Owner wirklich nicht stimmt — kein I/O-Storm
    auf großen Repos.
  - **chmod +x**: scannt das `install_dir` top-level nach `*.sh` und setzt
    `0o755`, **nur wenn** das Execute-Bit fehlt — keine Subdir-Iteration
    (KISS: Start-Skripte liegen top-level).
  - **Defensiv**: `OperationNotPermitted` (z. B. read-only-FS) wird
    geloggt, nicht eskaliert. Setup-Commands laufen danach trotzdem und
    bekommen ihren eigenen, präzisen Fehler.
- **Idempotent**: Ein zweiter Aufruf ist echter No-Op (kein chown-Churn,
  keine Log-Spam).

### Sicherheit / KISS

- **Kein neuer Manager, keine Subklasse** — reine Helferfunktion im
  schon vorhandenen `blueprints/github_source.py`. Wirkt nur, wenn die
  Source ein `github`-Blueprint ist; alle anderen Sources ignorieren den
  Patch vollständig.
- **Keine zusätzlichen Schreibzugriffe** im Happy-Path (Owner stimmt,
  `*.sh` ist bereits ausführbar).
- **Prozess-scoped**: nutzt `os.getuid()`/`os.getgid()` — funktioniert
  unter rootless Docker (msm:994) genauso wie unter root.
- **Kein API-Bruch, keine DB-Migration, kein Frontend-Touch.**

### Verifikation

- 6 Unit-Tests lokal: chmod-Setzung, Idempotenz, Subdir-Isolation,
  Nicht-`.sh`-Ignoranz, Self-Owner-Pfad, nicht-existenter Pfad — alle
  grün.
- Live-Probe mit v1.4.2-Helper + v1.4.1-Ownership-Trick auf
  `singra_backend_80` lief in 10.6 s durch. Logs zeigen explizit:
  - `ensure_install_dir_writable: chown -R 0:0 /opt/msm/servers/singra_backend_80 (war 994:986)`
  - `ensure_install_dir_writable: chmod +x /opt/msm/servers/singra_backend_80/start.sh`
- Kompletter Pull + Build + Restart zog einen neuen Master-Commit
  (`464170e8 "Add Discord status rotation and env vars"`) und baute
  219 Artefakte frisch (`dist/api/apps/api/src/index.js` = 03:00:35).

### Auswirkungen auf Blueprints / User

- Discord-Bot-Blueprint (`blueprints/community/singra_backend.blueprint.json`)
  und alle anderen `source.type=github`-Blueprints ziehen Pull + Build
  jetzt robust durch — auch wenn `node_modules`/`packages/dist` durch
  externe Aktionen plötzlich einem anderen User gehören oder
  Start-Skripte nach einem Teilabbruch ihr `+x` verloren haben.
- Der Bot braucht keinen manuellen Eingriff und kein `chown` mehr von
  außen.

### Geänderte Dateien

- `backend/blueprints/github_source.py` (+85 / −0)
- `RELEASE_NOTES_v1.4.2.md`
