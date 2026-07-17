from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.prepare_component_migration import (
    disable_local_agent,
    merge_target_environment,
)


SOURCE_SECRET = "source-secret-value-that-must-not-rotate"
DIS_SALT = "source-dis-salt-value-that-must-not-rotate"
DIS_TOKEN = "source-sidecar-token-that-must-not-rotate"


def _write(path: Path, content: str) -> Path:
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def test_merge_keeps_source_secrets_and_target_database_credentials(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "source.env",
        f'''MSM_SECRET_KEY="{SOURCE_SECRET}"
MSM_DIS_SALT="{DIS_SALT}"
MSM_DIS_SIDECAR_TOKEN="{DIS_TOKEN}"
MSM_DATABASE_URL="postgresql+psycopg2://source:source-password@localhost/source"
MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://source:source-password@localhost/source"
MSM_PANEL_URL="https://old.example.com"
MSM_CORS_ALLOWED_ORIGINS="https://preview.example.com"
MSM_LOCAL_AGENT_ENABLED=true
MSM_DOCKER_HOST="unix:///source/docker.sock"''',
    )
    target = _write(
        tmp_path / "target.env",
        '''MSM_DATABASE_URL="postgresql+psycopg2://msm:target-password@localhost/msm"
MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://msm:target-password@localhost/msm"''',
    )
    output = tmp_path / "merged.env"
    dis_output = tmp_path / "dis.env"

    merge_target_environment(
        source_env=source,
        target_env=target,
        output_env=output,
        dis_output=dis_output,
        api_origin="https://api.example.com",
        frontend_origin="https://panel.vercel.app",
    )

    merged = output.read_text(encoding="utf-8")
    dis = dis_output.read_text(encoding="utf-8")
    assert SOURCE_SECRET in merged and SOURCE_SECRET in dis
    assert DIS_SALT in merged and DIS_SALT in dis
    assert DIS_TOKEN in merged and DIS_TOKEN in dis
    assert "target-password" in merged
    assert "source-password" not in merged
    assert 'MSM_PANEL_URL="https://panel.vercel.app"' in merged
    assert 'MSM_API_URL="https://api.example.com"' in merged
    assert "MSM_LOCAL_AGENT_ENABLED=false" in merged
    assert "MSM_COOKIE_CROSS_SITE=true" in merged
    assert "MSM_SERVE_FRONTEND=false" in merged
    assert "https://preview.example.com,https://panel.vercel.app" in merged
    assert 'MSM_DOCKER_HOST=""' in merged
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
        assert dis_output.stat().st_mode & 0o777 == 0o600


def test_merge_rejects_invalid_origin_without_writing_outputs(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "source.env",
        f'''MSM_SECRET_KEY="{SOURCE_SECRET}"
MSM_DIS_SALT="{DIS_SALT}"
MSM_DIS_SIDECAR_TOKEN="{DIS_TOKEN}"''',
    )
    target = _write(
        tmp_path / "target.env",
        '''MSM_DATABASE_URL="postgresql+psycopg2://msm:pw@localhost/msm"
MSM_DATABASE_URL_ASYNC="postgresql+asyncpg://msm:pw@localhost/msm"''',
    )
    output = tmp_path / "merged.env"

    with pytest.raises(RuntimeError, match="HTTPS-Origin"):
        merge_target_environment(
            source_env=source,
            target_env=target,
            output_env=output,
            dis_output=tmp_path / "dis.env",
            api_origin="https://api.example.com/path",
            frontend_origin=None,
        )
    assert not output.exists()


def test_disable_local_agent_updates_atomically_without_touching_secrets(tmp_path: Path) -> None:
    env_file = _write(
        tmp_path / "backend.env",
        f'''MSM_SECRET_KEY="{SOURCE_SECRET}"
MSM_LOCAL_AGENT_ENABLED=true''',
    )

    disable_local_agent(env_file)

    content = env_file.read_text(encoding="utf-8")
    assert SOURCE_SECRET in content
    assert "MSM_LOCAL_AGENT_ENABLED=false" in content
    assert content.count("MSM_LOCAL_AGENT_ENABLED=") == 1
    if os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600
