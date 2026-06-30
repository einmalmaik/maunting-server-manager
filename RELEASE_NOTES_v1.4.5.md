## GitHub-Source: garantierter Working-Tree-HEAD nach Pull

Mit v1.4.5 zieht MSM beim Update-Pfad den Working-Tree eines
GitHub-Servers zuverlässig auf den exakten Remote-Stand — auch wenn
zwischendurch ein anderer Prozess (cron, paralleler Probe-Run,
manuelles Editieren) Änderungen am install_dir vorgenommen hat.

### Bugfix

- **Doppelter `reset --hard origin/<branch>`** in `install_github_source` —
  falls ein anderer Prozess zwischen den Git-Schritten schreibt, fängt der
  zweite Reset es ab. Idempotent (no-op wenn nichts passiert ist).
- **`fetch --depth 1 --prune`** statt nur `--depth 1` — sauberer Remote-
  Tracking-Branch-Stand, keine stale Branches.
- **`git submodule update --init --recursive --force`** falls `.gitmodules`
  vorhanden — Blueprints mit Subrepos kommen vollständig mit. Sonst no-op
  (try/except umgeht nicht-relevante Repos).
- **HEAD-Verifikation nach reset**: Working-Tree-HEAD wird gegen
  `origin/<branch>` verglichen. Bei Abweichung **expliziter Fehler** mit
  klarer Diagnose ("Ein externer Prozess hat den Tree während des Pulls
  geändert. Bitte manuell bereinigen.") — kein silent-state-mismatch mehr.
- **Klares INFO-Log** mit Branch + Head-SHA: `Pull-Check OK --
  branch=master head=cf3511fd7436`. Console-Log und UI zeigen, was
  passiert ist.

### Sicherheit / KISS

- **Kein `git clean -fdx`** — bewusst weggelassen. Untracked Files
  (`.env`, benutzerdefinierte Configs, neue User-Mods) bleiben unangetastet.
  Der Patch verifiziert nur **getrackte Dateien** — exakt das, was du wolltest.
- **Keine neuen Abhängigkeiten**, keine Blueprints-Anpassung — die Änderung
  wirkt auf alle `source.type=github`-Blueprints gleich.
- **Vollständig generisch**: keine Annahmen über Branch-Name, File-Liste
  oder Subrepo-Topologie.

### Hintergrund

Vor v1.4.5 konnte ein anderes Skript (Probe-Run, Cron mit root, ein
Editor) den `install_dir` zwischen `git reset --hard` und `npm ci`
manipulieren. MSM hat das nicht erkannt — der anschließende Build lief auf
einem gemischten Stand aus Remote-HEAD und lokalen Mutationen. Die Folge
waren inkonsistente Übersetzungen, fehlende Features oder seltsame Logs,
ohne dass die UI darauf hinwies.

Mit v1.4.5 wird das jetzt gefangen:
- Der `Working-Tree-HEAD weicht von origin/<branch> ab` wirft eine
  `GithubSourceError` mit klarer Botschaft.
- Der UI-Log enthält die actual_head vs expected_head SHAs zur Diagnose.
- Setup-Commands (`npm ci`) laufen nicht auf einem gemischten Stand.

### Verifikation (live, auf `singra_backend_80`)

- Probe-Run: `install_github_source` lief in 11 s durch.
- Logger-Zeile: `GitHub-Source: Pull-Check OK -- branch=master head=cf3511fd7436`.
- Working-Tree-HEAD danach: `cf3511fd743654d719a18a330d9c80965f142ecc`
  (= identisch mit `git ls-remote origin/master`).
- 48 Build-Artefakte frisch (API + Bot).

### Geänderte Dateien

- `backend/blueprints/github_source.py` (+58 / −1)
- `RELEASE_NOTES_v1.4.5.md`
