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
    from blueprints.github_source import _run_setup_commands, _run_argv_with_retry

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


def test_setup_command_retry_also_fails(tmp_path, monkeypatch):
    """Retry nach TAR_ENTRY_ERROR schlaegt ebenfalls fehl → Fehler propagiert."""
    from blueprints.github_source import GithubSourceError, _run_argv_with_retry

    _write_pkg(tmp_path)
    (tmp_path / "node_modules").mkdir()
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return _FakeProc(
            1, stderr="npm warn tar TAR_ENTRY_ERROR ENOENT: again\n"
        )

    monkeypatch.setattr("blueprints.github_source.subprocess.run", fake_run)

    with pytest.raises(GithubSourceError) as exc:
        _run_argv_with_retry(["npm", "ci"], cwd=tmp_path)
    assert calls["n"] == 2
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