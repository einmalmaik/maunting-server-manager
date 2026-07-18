"""Deployment environment contract shared by config, examples, and installer."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _settings_fields(path: Path, *, excluded: set[str] | None = None) -> set[str]:
    excluded = excluded or set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    settings_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        f"MSM_{node.target.id.upper()}"
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id not in excluded
    }


def _env_keys(path: Path) -> set[str]:
    return set(
        re.findall(
            r"^((?:MSM|VITE)_[A-Z0-9_]+|NODE_ENV)\s*=",
            path.read_text(encoding="utf-8"),
            re.M,
        )
    )


def _assert_every_value_is_explained(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^(?:(?:MSM|VITE)_[A-Z0-9_]+|NODE_ENV)\s*=", line):
            continue
        comment_block: list[str] = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].startswith("#"):
            comment_block.append(lines[cursor])
            cursor -= 1
        comments = "\n".join(comment_block)
        assert "# Status:" in comments, f"Status fehlt vor {path}:{index + 1}"
        assert "# Zweck:" in comments, f"Zweck fehlt vor {path}:{index + 1}"


def test_backend_example_covers_all_runtime_settings_and_uses_postgres() -> None:
    example = ROOT / "backend" / ".env.example"
    assert _settings_fields(
        ROOT / "backend" / "config.py", excluded={"trusted_postgres_extensions"}
    ) <= _env_keys(example)
    assert "MSM_LOCAL_AGENT_TOKEN" in _env_keys(example)
    text = example.read_text(encoding="utf-8")
    assert "MSM_DATABASE_URL=\"postgresql+psycopg2://" in text
    assert "MSM_DATABASE_URL=\"sqlite" not in text


def test_agent_example_covers_all_operator_settings() -> None:
    assert _settings_fields(ROOT / "msm-agent" / "config.py") <= _env_keys(
        ROOT / "msm-agent" / ".env.example"
    )


def test_all_component_examples_explain_every_value() -> None:
    for relative_path in (
        "backend/.env.example",
        "msm-agent/.env.example",
        "frontend/.env.example",
        "dis-sidecar/.env.example",
    ):
        _assert_every_value_is_explained(ROOT / relative_path)


def test_frontend_and_sidecar_source_variables_are_documented() -> None:
    frontend_source = (ROOT / "frontend" / "src" / "config" / "api.ts").read_text(
        encoding="utf-8"
    )
    frontend_keys = set(re.findall(r"import\.meta\.env\.(VITE_[A-Z0-9_]+)", frontend_source))
    assert frontend_keys <= _env_keys(ROOT / "frontend" / ".env.example")

    sidecar_source = (ROOT / "dis-sidecar" / "server.mjs").read_text(encoding="utf-8")
    sidecar_keys = set(re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", sidecar_source))
    assert sidecar_keys <= _env_keys(ROOT / "dis-sidecar" / ".env.example")


def test_secret_env_files_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"backend/.env", "msm-agent/.env", "dis-sidecar/.env"} <= set(ignored)


def test_installer_writes_multinode_and_split_hosting_settings() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    required = {
        "MSM_DATABASE_URL",
        "MSM_DATABASE_URL_ASYNC",
        "MSM_COOKIE_CROSS_SITE",
        "MSM_CORS_ALLOWED_ORIGINS",
        "MSM_SERVE_FRONTEND",
        "MSM_LOCAL_AGENT_ENV_FILE",
        "MSM_MANAGED_POSTGRES_DATA_DIR",
        "MSM_STEAM_API_KEY",
        "MSM_GITHUB_CLONE_TOKEN",
    }
    assert required <= set(re.findall(r"^(MSM_[A-Z0-9_]+)=", installer, re.M))
    assert "backend/.env.example" in installer
    assert "msm-agent/.env.example" in installer
    assert "dis-sidecar/.env.example" in installer

    updater = (ROOT / "update.sh").read_text(encoding="utf-8")
    remote_installer = (ROOT / "helper-scripts" / "install-msm-agent.sh").read_text(encoding="utf-8")
    for script in (installer, updater):
        assert "MSM_DIS_SIDECAR_PORT=9100" in script
        assert "NODE_ENV=production" in script
        assert "dis-sidecar/.env.example" in script
    assert "${AGENT_DIR}/.env.example" in remote_installer
    assert 'install -m 0600 /dev/null "$TOKEN_HANDOFF_FILE"' in remote_installer
    assert '${YELLOW}${AGENT_TOKEN}${NC}' not in remote_installer
