"""Deployment environment contract shared by config, examples, and installer."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _settings_fields(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    settings_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        f"MSM_{node.target.id.upper()}"
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id != "trusted_postgres_extensions"
    }


def _env_keys(path: Path) -> set[str]:
    return set(re.findall(r"^(MSM_[A-Z0-9_]+)\s*=", path.read_text(encoding="utf-8"), re.M))


def test_backend_example_covers_all_runtime_settings_and_uses_postgres() -> None:
    example = ROOT / "backend" / ".env.example"
    assert _settings_fields(ROOT / "backend" / "config.py") <= _env_keys(example)
    text = example.read_text(encoding="utf-8")
    assert "MSM_DATABASE_URL=\"postgresql+psycopg2://" in text
    assert "MSM_DATABASE_URL=\"sqlite" not in text


def test_agent_example_covers_all_operator_settings() -> None:
    assert _settings_fields(ROOT / "msm-agent" / "config.py") <= _env_keys(
        ROOT / "msm-agent" / ".env.example"
    )


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

