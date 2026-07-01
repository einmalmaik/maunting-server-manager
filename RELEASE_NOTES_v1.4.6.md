# MSM v1.4.6 — Generic GitHub-Source Pull Reliability Fix

## Bug

`source.type=github` Blues (z. B. Singra-Discord-Bot, aber **alle** Repos mit
dieser Source-Form) zogen auf Reinstall/Update nicht zuverlässig den
neuesten Stand. Konkretes Symptom im Panel:

- `origin/<branch>` wurde korrekt auf den neuen Remote-SHA upgedated.
- Der **Working-Tree** blieb aber auf dem alten Commit stehen.
- Nachfolgende `setupCommands` (z. B. `npm ci`, `npm run build`) bauten
  deshalb mit altem Source-Code weiter, ohne dass ein Fehler im Panel
  auffiel.

Auf dem Singra-Discord-Bot konkret: HEAD blieb auf PR #15 (`a9c2f54`),
obwohl `origin/master` längst auf PR #17 (`0570540`) stand.

## Ursache (generisch, betrifft jedes GitHub-Repo mit dieser Source)

`backend/blueprints/github_source.py` rief im Pull-Pfad

```python
_run_git(["fetch", "origin", branch, "--depth", "1", "--prune"], cwd=target)
_run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=target)
_run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
```

`git checkout -B <branch> origin/<branch>` ist ein normaler Checkout,
der **lokale Working-Tree-Mutationen schützt**: sobald der Tree
uncommittete Änderungen hat (typisch nach einem ersten `npm ci`, der
z. B. Dateien wie `.env.local` anlegt, oder wenn ein Admin-User per
Panel-Console manuell editiert), bricht der Befehl mit

```
error: Your local changes to the following files would be overwritten by checkout:
```

ab. Der Fehler eskaliert als `GithubSourceError`, **bevor** das
nachfolgende `git reset --hard` läuft — das den Working-Tree
ohnehin hart auf `origin/<branch>` gesetzt und die Mutationen
überschrieben hätte.

Effekt: Der User bekommt einen nichtssagenden Fehler, `origin/<branch>`
sieht aktuell aus, aber der Working-Tree ist veraltet.

## Fix

`checkout -B` ist redundant — `reset --hard origin/<branch>` setzt HEAD
**und** Working-Tree atomar ohne Working-Tree-Schutz. Der neue Pfad:

```python
_run_git(["fetch", "origin", branch, "--depth", "1", "--prune"], cwd=target)
# Nur falls Branch lokal noch nicht existiert (Edge-Case nach git-init
# oder Branch-Rename): anlegen. ``git branch -f`` wuerde auf neueren
# Git-Versionen (>=2.40) beim currently-checked-out-Branch scheitern
# ("cannot force update the branch ... used by worktree"), daher
# nur ``branch`` ohne -f und nur wenn wirklich noetig.
existing_ref = subprocess.run(
    ["git", "-C", str(target), "show-ref", "--verify",
     f"refs/heads/{branch}"],
    capture_output=True, env=_git_env(),
)
if existing_ref.returncode != 0:
    _run_git(["branch", branch, f"origin/{branch}"], cwd=target)
_run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
```

Eigenschaften:
- **Generisch**: wirkt auf **jedes** GitHub-Repo, das MSM via
  `source.type=github` pullt — Singra, andere Bots, Apps, alles.
- **Idempotent**: bei wiederholtem Pull auf bereits aktuellem Stand
  ein No-Op (außer dem erneuten `fetch --prune`).
- **Robust gegen Working-Tree-Mutationen**: `reset --hard`
  überschreibt sie ohne Rückfrage.
- **Kompatibel mit aktuellen Git-Versionen**: vermeidet den
  Worktree-Schutz, der `git branch -f` auf Git ≥2.40 blockiert.

## Tests

Zwei neue Regression-Tests in
`backend/tests/test_blueprint_github_source.py`:

- `test_pull_updates_working_tree_despite_dirty_workdir` — reproduziert
  exakt den Bug-Pfad (lokaler Clone mit Working-Tree-Mutationen, Remote
  hat neuen Commit) und verifiziert, dass `install_github_source` den
  Tree auf den neuen Stand bringt.
- `test_pull_overwrites_uncommitted_setup_artifacts` — Spezialfall
  mit `node_modules/`, `.env.local` etc. als Überbleibsel von vorherigen
  SetupCommands.

Vorher schlugen beide Tests fehl (`git fehlgeschlagen (128): Your local
changes would be overwritten` bzw. `cannot force update the branch`),
nach dem Fix sind beide grün. Gesamte Blueprint-Test-Suite bleibt
stabil: **260 Tests grün** im Blueprint/Install/GitHub-Slice.

## Verifikation

- Manuelle Reproduktion auf `/opt/msm/servers/singra_backend_80`:
  Working-Tree sprang mit dem Fix-Pfad sauber auf den aktuellen
  Remote-HEAD (`0570540`), `git status` danach clean.
- `pytest tests/ -k "blueprint or github or install"`: 260 passed.
- Keine neuen Dependencies, keine API-Änderungen am Blueprint-Schema,
  keine Änderungen am Frontend.

## Migrations-Hinweis

Keine Migration nötig. Der Fix wirkt beim nächsten Pull/Reinstall
automatisch. Bestehende Installationen mit veraltetem Working-Tree
werden beim nächsten Trigger auf den aktuellen Stand gezogen.