"""Contracts for split release artifacts and public self-hosting docs."""

import json
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
        "MauntingPontis-Setup.exe",
        "latest-msb.json",
        "MauntingSecureBrowser.apk",
        "MauntingSmartSystem.apk",
        "releases.pontis.json",
        "releases.mss.json",
    ):
        assert datei in release.split("name: Verify release assets", 1)[1], datei

    # Android nur aus den Secrets, mit eigenem Schluessel ohne Abfolge.
    assert "bash scripts/android-apk-signieren-msb.sh" in bau
    assert "secrets.MSB_ANDROID_KEYSTORE_B64" in bau
    signieren = (ROOT / "scripts" / "android-apk-signieren-msb.sh").read_text(encoding="utf-8")
    assert '"${MSB_ANDROID_KEYSTORE_B64:?fehlt}"' in signieren
    assert "APK Signature Scheme v3): true" in signieren

    # Der Velopack-Feed und Velopacks Setup.exe werden mit dem Secret signiert
    # (nicht mehr der Tauri-Installer). Den CLI-Einstiegspunkt startet der Job
    # direkt mit node: `npx --prefix <dir>` findet keine lokale Ausfuehrbare.
    assert "TAURI_SIGNING_PRIVATE_KEY" in bau
    signierschritt = bau.split("Sign Velopack feed", 1)[1]
    assert '"$cli" signer sign --app-version' in signierschritt
    assert "releases.pontis.json" in signierschritt
    # vpk nennt den Installer `<PackId>-<Kanal>-Setup.exe`; aeltere Fassungen
    # `*-win-Setup.exe`. Beide Namen deckt die Suche ab.
    assert "*-Setup.exe" in signierschritt


def test_windows_updates_laufen_nur_ueber_velopack() -> None:
    """Ein zweiter Installer neben Velopack machte Updates unmöglich.

    Bis 10/2026 lag der NSIS-Installer von Tauri neben Velopack. Wer den
    NSIS-Installer nahm, hatte eine Installation, die Velopacks `UpdateManager`
    nicht findet („Velopack-Installation konnte nicht gefunden werden“).
    Jetzt baut Tauri nur die App (`bundle.targets: ["app"]`); der einzige
    Installer ist Velopacks `*-Setup.exe`. Damit bestehende v5.5.1-Installationen
    automatisch auf Velopack gehoben werden, wird Velopacks `Setup.exe` mit
    `tauri signer` signiert und in `latest.json`/`latest-msb.json` unter
    `platforms.windows-x86_64` eingetragen.
    """
    for app in ("smart-system", "browser"):
        konfig = (ROOT / app / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        assert "createUpdaterArtifacts" not in konfig, app
        assert '"plugins"' not in konfig, app
        assert '"nsis"' not in konfig, app
        assert '"app"' in konfig, app

    release = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    bau = (ROOT / ".github" / "workflows" / "browser-build.yml").read_text(
        encoding="utf-8"
    )
    assert "MauntingSmartSystem-Setup.exe.sig" in release
    assert "MauntingPontis-Setup.exe.sig" in release
    # Beide Apps bauen nur die App; den Installer packt `vpk pack`.
    assert "npx tauri build" in release and "npx tauri build" in bau
    assert "vpk pack" in release and "vpk pack" in bau

    for skript in ("collect-desktop-artifacts.py", "collect-browser-artifacts.py"):
        sammeln = (ROOT / "scripts" / skript).read_text(encoding="utf-8")
        # Ein NSIS-Paket daneben bricht den Lauf, statt es still mitzunehmen.
        assert "Tauri-NSIS-Installer gefunden" in sammeln, skript
        # Die Manifestdatei migriert v5.5.1-Bestandsnutzer auf die Velopack-Setup.exe.
        manifest = sammeln.split("manifest = {", 1)[1].split("\n}\n", 1)[0]
        assert "platforms" in manifest, skript
        assert "windows-x86_64" in manifest, skript
        assert "signature" in manifest, skript

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


def test_release_prueft_das_vollpaket_mit_kanal_im_namen() -> None:
    """vpk 1.2 schreibt den Kanal in den Paketnamen.

    Die Pflichtliste suchte `Pontis-<Version>-full.nupkg` und machte den Lauf rot,
    obwohl Feed, Paket und Installer vollstaendig im Release lagen (20.10.2026,
    v5.6.0). Aeltere vpk-Fassungen lassen den Kanal weg; beide Namen gelten.
    """
    release = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    pruefung = release.split("name: Verify release assets", 1)[1]
    assert "(-[a-z0-9]+)?-full\\.nupkg$" in pruefung
    assert "MauntingSmartSystem-${TAG#v}-full.nupkg" not in pruefung


def test_ein_zweiter_lauf_fuer_denselben_tag_laedt_kein_delta() -> None:
    """Beim Wiederholen eines Tags liegt das eigene Paket schon im Release.

    `vpk download` holte es als "vorheriges", und `vpk pack` brach mit "exists"
    ab; beim Smart System fehlte zudem der Zielordner vor dem Schreiben des
    Feeds (20.10.2026, v5.6.0). Jetzt bauen beide Apps ohne Delta.
    """
    for name in ("release-artifacts.yml", "browser-build.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert '!= "${{ steps.version.outputs.clean_version }}"' in text, name
    release = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    assert "mkdir -p smart-system-velopack" in release


def test_beide_apps_signieren_mit_derselben_tauri_cli() -> None:
    """Feed und Setup.exe signiert die CLI aus dem eigenen `npm ci` der App.

    `signer sign --app-version` gibt es erst ab 2.12; Smart System stand bis
    20.10.2026 auf 2.11.4. Der Windows-Bau schrieb den Feed und brach dann beim
    Signieren ab, weil der Schalter unbekannt war.
    """
    versionen = {}
    for app in ("smart-system", "browser"):
        lock = json.loads(
            (ROOT / app / "package-lock.json").read_text(encoding="utf-8")
        )
        version = lock["packages"]["node_modules/@tauri-apps/cli"]["version"]
        zahlen = tuple(int(teil) for teil in version.split(".")[:3])
        assert zahlen >= (2, 12, 0), f"{app}: {version} kennt --app-version nicht"
        versionen[app] = version
    assert versionen["smart-system"] == versionen["browser"], versionen
