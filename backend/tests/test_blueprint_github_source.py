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