"""Contracts for split release artifacts and public self-hosting docs."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# Jede Android-App im Repo: Smart System und Secure Browser.
ANDROID_APPS = [
    ROOT / "smart-system" / "src-tauri" / "gen" / "android",
    ROOT / "browser" / "src-tauri" / "gen" / "android",
]


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
    eingecheckt = subprocess.run(
        ["git", "ls-files", "*.keystore", "*.jks", "*.p12", "*.pfx"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert eingecheckt == []
    for android in ANDROID_APPS:
        gradle = (android / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        assert "storePassword" not in gradle, android
        assert "keyPassword" not in gradle, android
        assert "signingConfig" not in gradle, android

    workflow = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    assert "bash scripts/android-apk-signieren.sh" in workflow
    assert "secrets.MSS_ANDROID_KEYSTORE_B64" in workflow
    signieren = (ROOT / "scripts" / "android-apk-signieren.sh").read_text(encoding="utf-8")
    assert "--lineage" in signieren and "--rotation-min-sdk-version 28" in signieren



def test_secure_browser_kommt_vollstaendig_und_signiert_ins_release() -> None:
    """MSB wird mit dem v*-Release gebaut; ohne eine seiner Dateien scheitert er.

    Bis 09.10.2026 baute kein Workflow den Browser, und der Release zaehlte
    seine Dateien nur, statt sie zu pruefen.
    """
    workflows = ROOT / ".github" / "workflows"
    release = (workflows / "release-artifacts.yml").read_text(encoding="utf-8")
    bau = (workflows / "browser-build.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/browser-build.yml" in release
    assert "build-browser]" in release
    assert "continue-on-error" not in release
    for datei in (
        "MauntingSecureBrowser-Setup.exe",
        "MauntingSecureBrowser-Setup.exe.sig",
        "latest-msb.json",
        "MauntingSecureBrowser.apk",
        "MauntingSmartSystem.apk",
    ):
        assert datei in release.split("name: Verify release assets", 1)[1], datei

    # Android nur aus den Secrets, mit eigenem Schluessel ohne Abfolge.
    assert "bash scripts/android-apk-signieren-msb.sh" in bau
    assert "secrets.MSB_ANDROID_KEYSTORE_B64" in bau
    signieren = (ROOT / "scripts" / "android-apk-signieren-msb.sh").read_text(encoding="utf-8")
    assert '"${MSB_ANDROID_KEYSTORE_B64:?fehlt}"' in signieren
    assert "APK Signature Scheme v3): true" in signieren

    # Der Updater des Browsers liest die Datei, die das Sammelskript schreibt.
    sammeln = (ROOT / "scripts" / "collect-browser-artifacts.py").read_text(encoding="utf-8")
    assert 'MANIFEST = "latest-msb.json"' in sammeln
    konfig = (ROOT / "browser" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    assert "releases/latest/download/latest-msb.json" in konfig
    assert "TAURI_SIGNING_PRIVATE_KEY" in bau

def test_android_webview_hears_network_changes() -> None:
    """Ohne ACCESS_NETWORK_STATE bleibt navigator.onLine im WebView immer true.

    Kein `online`-Ereignis: Tresor und Notizen merkten bis 01.10.2026 nicht,
    wenn das Netz zurückkam (Emulator-Probe mit Flugmodus).
    """
    for android in ANDROID_APPS:
        manifest = (android / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
        assert 'android:name="android.permission.ACCESS_NETWORK_STATE"' in manifest, android


def test_android_asks_for_no_overlay_and_no_microphone_service() -> None:
    """Die App zeichnet über keine fremden Apps und hat keinen Mikrofon-Dienst.

    `SYSTEM_ALERT_WINDOW` und `FOREGROUND_SERVICE_MICROPHONE` standen seit dem
    ersten Android-Bau im Manifest, ohne dass Kotlin oder Rust sie je nutzte
    (einen Dienst hat die App nicht mehr). Eine Berechtigung, die niemand braucht,
    ist nur Angriffsfläche; wer sie wieder braucht, nimmt diesen Test mit.
    """
    manifest = (
        ROOT / "smart-system" / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    assert "android.permission.SYSTEM_ALERT_WINDOW" not in manifest
    assert "android.permission.FOREGROUND_SERVICE_MICROPHONE" not in manifest
    assert 'android:foregroundServiceType="microphone' not in manifest


def test_android_reminders_need_no_background_service() -> None:
    """Terminerinnerungen plant die App bei Android, kein Dienst fragt die Instanz.

    Bis 03.10.2026 lief ein Vordergrunddienst in eigenem Prozess, der alle 25 s
    beim Panel nachfragen sollte, aber Konfiguration und Token am falschen Ort
    las und nie durchkam. Jetzt stellt `Erinnerungen.kt` Wecker; der Empfänger
    ist nur für das eigene Paket da, und ohne Dienst entfallen dessen Rechte.
    """
    manifest = (
        ROOT / "smart-system" / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    assert "<service" not in manifest
    assert "android.permission.FOREGROUND_SERVICE" not in manifest
    empfaenger = re.search(r'<receiver\s+android:name="\.ErinnerungEmpfaenger"\s+android:exported="false"', manifest)
    assert empfaenger is not None
    assert ":alert_service" not in manifest
