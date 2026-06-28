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
    PanelSettingsService.set("github_clone_token", "ghp_paneltoken")

    from blueprints.github_source import _clone_url

    url = _clone_url("octocat/Hello-World")
    assert url == "https://x-access-token:ghp_paneltoken@github.com/octocat/Hello-World.git"
    PanelSettingsService.set("github_clone_token", "")