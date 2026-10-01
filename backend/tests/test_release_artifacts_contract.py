"""Contracts for split release artifacts and public self-hosting docs."""

import subprocess
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


def test_android_signing_key_never_lives_in_the_repo() -> None:
    """Bis 5.0.4 lagen Keystore und Passwort im oeffentlichen Repo."""
    android = ROOT / "smart-system" / "src-tauri" / "gen" / "android"
    eingecheckt = subprocess.run(
        ["git", "ls-files", "*.keystore", "*.jks", "*.p12", "*.pfx"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert eingecheckt == []
    gradle = (android / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "storePassword" not in gradle
    assert "keyPassword" not in gradle

    workflow = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    assert "bash scripts/android-apk-signieren.sh" in workflow
    assert "secrets.MSS_ANDROID_KEYSTORE_B64" in workflow
    signieren = (ROOT / "scripts" / "android-apk-signieren.sh").read_text(encoding="utf-8")
    assert "--lineage" in signieren and "--rotation-min-sdk-version 28" in signieren


def test_android_webview_hears_network_changes() -> None:
    """Ohne ACCESS_NETWORK_STATE bleibt navigator.onLine im WebView immer true.

    Kein `online`-Ereignis: Tresor und Notizen merkten bis 01.10.2026 nicht,
    wenn das Netz zurückkam (Emulator-Probe mit Flugmodus).
    """
    manifest = (
        ROOT / "smart-system" / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    assert 'android:name="android.permission.ACCESS_NETWORK_STATE"' in manifest
