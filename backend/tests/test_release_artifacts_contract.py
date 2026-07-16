"""Contracts for split release artifacts and public self-hosting docs."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_workflow_publishes_explicit_component_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    packaging = (ROOT / "scripts" / "build-release-artifacts.sh").read_text(
        encoding="utf-8"
    )

    for prefix in ("msm-panel-", "msm-frontend-", "msm-agent-"):
        assert prefix in packaging
    assert "SHA256SUMS" in packaging
    assert "gh release upload" in workflow
    assert "release/*.tar.gz" in workflow
    assert "vite_api_url" in workflow
    assert "vite_ws_url" in workflow


def test_updater_selects_only_the_panel_asset() -> None:
    updater = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert 'expected = "msm-panel-{}.tar.gz"' in updater
    assert 'asset.get("name") == expected' in updater
    assert "sha256sum --check panel.sha256" in updater


def test_bootstrap_prefers_release_and_keeps_safe_fallback() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "releases/latest" in bootstrap
    assert 'expected = f"msm-panel-{tag}.tar.gz"' in bootstrap
    assert "git clone --depth 1" in bootstrap
    assert "sha256sum --check panel.sha256" in bootstrap


def test_canonical_docs_cover_deployment_and_secret_free_enrollment() -> None:
    docs = (ROOT / "docs" / "self-hosting.md").read_text(encoding="utf-8")
    required = {
        "msm-panel-<VERSION>.tar.gz",
        "msm-frontend-<VERSION>.tar.gz",
        "msm-agent-<VERSION>.tar.gz",
        "PostgreSQL ist die einzige unterstützte Panel-Runtime-Datenbank",
        "weder einen Repository-Clone noch manuelles Kopieren",
        "/docs/self-hosting",
    }
    for item in required:
        assert item in docs
