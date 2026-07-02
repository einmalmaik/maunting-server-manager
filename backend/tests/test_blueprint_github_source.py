"""Schema-Tests für source.type=github und startupProfiles."""
import pytest

from blueprints.schema import Blueprint, BlueprintValidationError, load_blueprint_dict


GITHUB_BOT = {
    "version": 1,
    "meta": {
        "id": "test_github_bot",
        "name": "Test GitHub Bot",
        "category": "bot",
    },
    "runtime": {
        "image": "node:22-bookworm-slim",
        "workdir": "/data",
        "env": {"NODE_ENV": "production"},
        "startup": "node index.js",
        "startupProfiles": [
            {"whenFile": "package.json", "startup": "npm start"},
            {"whenFile": "requirements.txt", "startup": "python3 main.py"},
        ],
    },
    "ports": [],
    "source": {
        "type": "github",
        "github": {
            "repo": "octocat/Hello-World",
            "branch": "master",
            "setupCommands": [["npm", "ci"]],
        },
    },
}


def test_github_blueprint_loads():
    bp = load_blueprint_dict(GITHUB_BOT)
    assert bp.source.type.value == "github"
    assert bp.source.github is not None
    assert bp.source.github.repo == "octocat/Hello-World"
    assert len(bp.runtime.startupProfiles) == 2


def test_github_repo_must_be_slug():
    bad = {**GITHUB_BOT}
    bad["source"] = {
        "type": "github",
        "github": {"repo": "https://github.com/foo/bar", "branch": "main"},
    }
    with pytest.raises((BlueprintValidationError, ValueError)):
        load_blueprint_dict(bad)


def test_resolve_startup_profile(tmp_path):
    from blueprints.github_source import resolve_startup_template

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    bp = load_blueprint_dict(GITHUB_BOT)
    assert resolve_startup_template(bp, str(tmp_path)) == "npm start"
    assert resolve_startup_template(bp, None) == "node index.js"


def test_clone_url_uses_none_when_no_token(monkeypatch):
    """Ohne ENV/Setting wird die Public-URL ohne Token gebaut."""
    monkeypatch.delenv("MSM_GITHUB_CLONE_TOKEN", raising=False)
    from services import github_token_service
    import importlib

    importlib.reload(github_token_service)
    from services.panel_settings_service import PanelSettingsService
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set("github_clone_token", "")

    from blueprints.github_source import _clone_url

    assert _clone_url("octocat/Hello-World") == "https://github.com/octocat/Hello-World.git"


def test_clone_url_uses_panel_token(monkeypatch):
    """Panel-Token (DB) fliesst in die Clone-URL ein."""
    monkeypatch.delenv("MSM_GITHUB_CLONE_TOKEN", raising=False)
    from services import github_token_service
    import importlib

    importlib.reload(github_token_service)
    from services.panel_settings_service import PanelSettingsService
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set("github_clone_token", "***")

    from blueprints.github_source import _clone_url

    url = _clone_url("octocat/Hello-World")
    assert url == "https://x-access-token:***@github.com/octocat/Hello-World.git"
    PanelSettingsService.set("github_clone_token", "")


# ── TAR_ENTRY_ERROR Retry-Logik ────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, returncode: int, stderr: str = "", stdout: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _write_pkg(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")


def test_setup_command_retries_npm_tar_entry_error(tmp_path, monkeypatch):
    """npm TAR_ENTRY_ERROR → node_modules wird aufgeraeumt, Retry laeuft mit
    demselben argv. Bei Erfolg kommt kein GithubSourceError."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "broken.txt").write_text("partial")

    tar_err = (
        "npm warn tar TAR_ENTRY_ERROR ENOENT: no such file or directory, "
        "open '/x/node_modules/es-abstract/2025/SetFunctionLength.js'\n"
    )
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(1, stderr=tar_err)
        return _FakeProc(0, stdout="ok")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)
    monkeypatch.setattr("blueprints.github_source.time.sleep", lambda *_a, **_kw: None)

    _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    assert calls["n"] == 2
    assert not node_modules.exists()


def test_setup_command_no_retry_on_other_npm_errors(tmp_path, monkeypatch):
    """npm-Fehler ohne TAR_ENTRY_ERROR wird nicht retried, Fehler propagiert."""
    from blueprints.github_source import GithubSourceError, _run_argv_with_retry

    _write_pkg(tmp_path)
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return _FakeProc(2, stderr="npm ERR! missing script: build")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    with pytest.raises(GithubSourceError) as exc:
        _run_argv_with_retry(["npm", "run", "build"], cwd=tmp_path)
    assert calls["n"] == 1
    assert "npm ERR! missing script: build" in str(exc.value)


def test_setup_command_no_retry_for_non_npm(tmp_path, monkeypatch):
    """Nicht-npm-Befehle werden nicht retried, auch wenn ENOENT im Output steht."""
    from blueprints.github_source import GithubSourceError, _run_argv_with_retry

    _write_pkg(tmp_path)
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return _FakeProc(1, stderr="ENOENT: no such file")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    with pytest.raises(GithubSourceError):
        _run_argv_with_retry(["python", "-m", "pip", "install"], cwd=tmp_path)
    assert calls["n"] == 1


def test_setup_command_retries_until_exhausted(tmp_path, monkeypatch):
    """Nach _NPM_TAR_RETRY_MAX+1 Versuchen wird sauber propagiert (kein Hang)."""
    from blueprints.github_source import GithubSourceError, _run_argv_with_retry

    _write_pkg(tmp_path)
    (tmp_path / "node_modules").mkdir()
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return _FakeProc(1, stderr="npm warn tar TAR_ENTRY_ERROR ENOENT: again\n")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)
    monkeypatch.setattr("blueprints.github_source.time.sleep", lambda *_a, **_kw: None)

    with pytest.raises(GithubSourceError) as exc:
        _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    # 1 erster Lauf + 3 Retries = 4 Aufrufe
    assert calls["n"] == 4
    assert "TAR_ENTRY_ERROR" in str(exc.value)


def test_setup_command_first_run_succeeds(tmp_path, monkeypatch):
    """Happy-Path: kein Retry, keine Aufraeumarbeiten."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    (tmp_path / "node_modules").mkdir()
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return _FakeProc(0, stdout="added 42 packages")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    assert calls["n"] == 1
    # node_modules bleibt unangetastet
    assert (tmp_path / "node_modules").exists()


def test_setup_command_injects_stabilize_flags_on_npm_ci(tmp_path, monkeypatch):
    """Bei ``npm ci`` werden automatisch --no-audit/--no-fund/--prefer-offline
    ergaenzt, ohne bestehende Flags zu duplizieren."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    seen_argv: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen_argv.append(list(argv))
        return _FakeProc(0, stdout="ok")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    # Flags landen zwischen 'npm' und dem Subcommand 'ci'.
    assert seen_argv[0][0] == "npm"
    assert seen_argv[0][-1] == "ci"
    assert "--no-audit" in seen_argv[0]
    assert "--no-fund" in seen_argv[0]
    assert "--prefer-offline" in seen_argv[0]
    # Flags nur 1x pro Run (kein Duplizieren).
    assert seen_argv[0].count("--no-audit") == 1
    assert seen_argv[0].index("--no-audit") > 0
    assert seen_argv[0].index("--no-audit") < seen_argv[0].index("ci")


def test_setup_command_does_not_inject_flags_for_npm_run(tmp_path, monkeypatch):
    """``npm run build`` ist KEIN Install-Befehl — keine Stabilisierungs-Flags,
    weil die Build-Schritte unter ``build:api`` usw. nichts mit pacote-Race zu
    tun haben."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    seen_argv: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen_argv.append(list(argv))
        return _FakeProc(0, stdout="ok")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    _run_argv_with_retry(["npm", "run", "build"], cwd=tmp_path)
    assert seen_argv[0] == ["npm", "run", "build"]


def test_setup_command_inject_does_not_duplicate_existing_flags(tmp_path, monkeypatch):
    """Wenn der User schon --no-audit gesetzt hat, wird es nicht doppelt
    eingefuegt."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    seen_argv: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen_argv.append(list(argv))
        return _FakeProc(0, stdout="ok")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    _run_argv_with_retry(["npm", "ci", "--no-audit"], cwd=tmp_path)
    assert seen_argv[0].count("--no-audit") == 1


def test_setup_command_retry_adds_network_concurrency_1(tmp_path, monkeypatch):
    """Beim Retry nach TAR_ENTRY_ERROR wird zusaetzlich --network-concurrency=1
    eingefuegt (eliminiert den pacote-Race komplett)."""
    from blueprints.github_source import _run_argv_with_retry

    _write_pkg(tmp_path)
    (tmp_path / "node_modules").mkdir()
    seen_argv: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen_argv.append(list(argv))
        if len(seen_argv) == 1:
            return _FakeProc(1, stderr="npm warn tar TAR_ENTRY_ERROR ENOENT: x\n")
        return _FakeProc(0, stdout="ok")

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)
    monkeypatch.setattr("blueprints.github_source.time.sleep", lambda *_a, **_kw: None)

    _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    assert len(seen_argv) == 2
    # Erster Lauf: --no-audit + --no-fund + --prefer-offline,
    # aber KEIN --network-concurrency=1.
    assert "--network-concurrency=1" not in seen_argv[0]
    assert "--no-audit" in seen_argv[0]
    assert "--prefer-offline" in seen_argv[0]
    # Zweiter Lauf: jetzt zusaetzlich --network-concurrency=1.
    assert "--network-concurrency=1" in seen_argv[1]
    assert "--no-audit" in seen_argv[1]
    assert "--prefer-offline" in seen_argv[1]


# ── Regression: Working-Tree-Mutationen duerfen Pull nicht blockieren ─────
#
# Hintergrund: Bis v1.4.5 rief ``install_github_source`` vor dem
# ``git reset --hard origin/<branch>`` ein ``git checkout -B <branch> origin/<branch>``
# auf. ``git checkout`` bricht ab, sobald der Working-Tree lokale Mutationen
# hat (etwa weil ein User im Panel eine Datei manuell editiert hat oder ein
# vorheriger ``npm ci`` Files angelegt hat, die nicht committet sind). Der
# Checkout-Fehler eskalierte als ``GithubSourceError``, BEVOR das eigentliche
# Reset lief -- mit dem Effekt, dass ``origin/<branch>`` zwar auf den neuen
# SHA upgedated wurde, der Working-Tree aber stehen blieb. Auf
# Singra-Discord-bot konkret: HEAD blieb auf PR #15 (a9c2f54), obwohl
# origin/master bereits auf PR #17 (0570540) stand.
#
# Fix: ``checkout -B`` ist redundant -- ``reset --hard`` setzt HEAD+Tree
# atomar ohne Working-Tree-Schutz. Der neue Pfad geht ueber
# ``git branch -f`` + ``git reset --hard``.
def _build_minimal_repo(tmp_path, *, with_subpath=False):
    """Baut ein Bare-Upstream + initialen Clone auf v1. Liefert (upstream, clone)."""
    import subprocess

    upstream = tmp_path / "upstream.git"
    clone = tmp_path / "clone"
    src = tmp_path / "src"
    src.mkdir()
    # Bare-Upstream + Working-Copy in einem Rutsch, damit beide Branches
    # dieselbe History teilen und Push ohne -f / fetch-first durchlaeuft.
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(upstream)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "init", "--initial-branch=main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(src), "config", "user.name", "t"], check=True)
    (src / "README.md").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-m", "v1"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(src), "remote", "add", "origin", str(upstream)],
                   check=True)
    subprocess.run(["git", "-C", str(src), "push", "origin", "main"], check=True,
                   capture_output=True)
    subprocess.run(["git", "clone", "--depth", "1", str(upstream), str(clone)],
                   check=True, capture_output=True)
    return upstream, clone


def _push_v2(upstream, src):
    """Hängt einen v2-Commit an die existierende Working-Copy und pusht."""
    import subprocess

    (src / "README.md").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-m", "v2"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(src), "push", "origin", "main"], check=True,
                   capture_output=True)


def test_pull_updates_working_tree_despite_dirty_workdir(tmp_path, monkeypatch):
    """Reproducer fuer den "neuste Version wird nicht gepullt"-Bug.

    Szenario: Lokaler Clone steht auf v1, Remote hat bereits v2. Working-Tree
    hat lokale Mutationen (simuliert vorherigen npm-ci oder Admin-Edit). Der
    Pull MUSS Working-Tree trotzdem auf v2 bringen -- ohne "git fehlgeschlagen:
    Your local changes would be overwritten".
    """
    import subprocess

    from blueprints.github_source import install_github_source
    from blueprints.schema import load_blueprint_dict

    upstream, clone = _build_minimal_repo(tmp_path)
    src = tmp_path / "src"

    # Working-Tree mutieren (das war der Ausloeser fuer den Bug).
    (clone / "README.md").write_text("lokale mutation\n", encoding="utf-8")

    # v2 nach upstream pushen.
    _push_v2(upstream, src)

    # Lokal origin auf das Bare-Repo umbiegen, damit install_github_source
    # ohne Netz funktioniert. Wir mocken nur die Clone-URL-Berechnung.
    monkeypatch.setattr(
        "blueprints.github_source._clone_url",
        lambda repo: str(upstream),
    )

    bp = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "t", "name": "T", "category": "bot"},
        "runtime": {"image": "node:22", "workdir": "/data",
                    "startup": "node index.js"},
        "ports": [],
        "source": {
            "type": "github",
            "github": {"repo": "fake/repo", "branch": "main"},
        },
    })

    result = install_github_source(bp, str(clone))

    # Pull muss erfolgreich sein und Working-Tree MUSS v2-Inhalt haben.
    assert result["ok"] is True, f"Pull schlug fehl: {result.get('error')!r}"
    assert (clone / "README.md").read_text(encoding="utf-8") == "v2\n", (
        "Working-Tree zeigt nach Pull immer noch alten/vormatierten Inhalt -- "
        "das ist der 'pullt die neuste Version nicht'-Bug."
    )
    # HEAD muss auf den v2-Commit zeigen.
    head = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    upstream_head = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == upstream_head, (
        f"Lokaler HEAD ({head[:12]}) != upstream HEAD ({upstream_head[:12]})"
    )


def test_pull_overwrites_uncommitted_setup_artifacts(tmp_path, monkeypatch):
    """Spezialfall des obigen Tests: Working-Tree enthaelt Artefakte wie
    ``node_modules/`` oder ``.env``-Files, die MSM selbst angelegt hat und
    die durch den Pull ueberschrieben werden muessen.
    """
    import subprocess

    from blueprints.github_source import install_github_source
    from blueprints.schema import load_blueprint_dict

    upstream, clone = _build_minimal_repo(tmp_path)
    src = tmp_path / "src"

    # Artefakte, wie sie nach einem ersten Install (npm ci) uebrig bleiben.
    (clone / "node_modules").mkdir()
    (clone / "node_modules" / "foo.txt").write_text("leftover\n")
    (clone / ".env.local").write_text("LOCAL=1\n")

    _push_v2(upstream, src)

    monkeypatch.setattr(
        "blueprints.github_source._clone_url",
        lambda repo: str(upstream),
    )

    bp = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "t", "name": "T", "category": "bot"},
        "runtime": {"image": "node:22", "workdir": "/data",
                    "startup": "node index.js"},
        "ports": [],
        "source": {
            "type": "github",
            "github": {"repo": "fake/repo", "branch": "main"},
        },
    })

    result = install_github_source(bp, str(clone))
    assert result["ok"] is True, f"Pull schlug fehl: {result.get('error')!r}"
    assert (clone / "README.md").read_text(encoding="utf-8") == "v2\n"


def test_pull_tolerates_local_branch_already_exists_race(tmp_path, monkeypatch):
    """Reproducer fuer den "fatal: a branch named 'master' already exists"-Bug.

    Szenario: Der Blueprint-Branch existiert lokal bereits -- das ist der
    Normalfall nach einem ersten Install (``git clone`` trackt den
    konfigurierten Branch automatisch). Auf Servern mit mehreren kurz
    aufeinanderfolgenden Restart-Versuchen (z. B. nach Blueprint- oder
    Container-Aenderungen) kann ein weiterer Aufruf von
    ``install_github_source`` waehrend der Abarbeitung denselben Branch
    erneut anlegen wollen; die alte, bedingt ausgefuehrte
    ``git branch <name> origin/<name>``-Variante schlug dann mit
    ``fatal: a branch named '<branch>' already exists`` (Exit 128) fehl
    und lies das Working-Tree auf dem alten HEAD stehen.

    Symptom exakt aus dem Panel-Journal:
        ``git fehlgeschlagen (128): fatal: a branch named 'master' already
        exists``

    Der Fix macht den Branch-Schritt race-tolerant: ``git branch`` wird
    immer ausgefuehrt, der "already exists"-Fehler wird geschluckt, der
    nachfolgende ``reset --hard origin/<branch>`` synct trotzdem.
    """
    import subprocess

    from blueprints.github_source import install_github_source
    from blueprints.schema import load_blueprint_dict

    upstream, clone = _build_minimal_repo(tmp_path)
    src = tmp_path / "src"

    # ``clone`` trackt nach ``git clone`` bereits ``main``. Wir simulieren
    # den produktiven Pfad nach einem ersten Install: der lokale Branch
    # existiert bereits, ein erneuter ``install_github_source``-Aufruf
    # muss trotzdem funktionieren.
    assert subprocess.run(
        ["git", "-C", str(clone), "show-ref", "--verify", "refs/heads/main"],
        capture_output=True,
    ).returncode == 0, "Branch sollte nach Clone bereits existieren"

    # v2 nach upstream pushen, damit der Pull etwas zu tun hat.
    _push_v2(upstream, src)

    monkeypatch.setattr(
        "blueprints.github_source._clone_url",
        lambda repo: str(upstream),
    )

    bp = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "t", "name": "T", "category": "bot"},
        "runtime": {"image": "node:22", "workdir": "/data",
                    "startup": "node index.js"},
        "ports": [],
        "source": {
            "type": "github",
            "github": {"repo": "fake/repo", "branch": "main"},
        },
    })

    result = install_github_source(bp, str(clone))
    assert result["ok"] is True, (
        f"Pull schlug fehl trotz existierendem lokalen Branch: "
        f"{result.get('error')!r}"
    )
    assert (clone / "README.md").read_text(encoding="utf-8") == "v2\n"
    head = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    upstream_head = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert head == upstream_head, (
        f"Lokaler HEAD ({head[:12]}) != upstream HEAD ({upstream_head[:12]})"
    )