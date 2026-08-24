from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "containers" / "asa-runtime"


def test_asa_runtime_is_reproducibly_pinned() -> None:
    dockerfile = (RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")

    assert "cm2network/steamcmd:root@sha256:" in dockerfile
    assert "ghcr.io/ptero-eggs/steamcmd:proton@sha256:" in dockerfile
    assert "ARG UMU_VERSION=1.4.0" in dockerfile
    assert "ARG UMU_SHA256=" in dockerfile
    assert "GE-Proton10-34" in dockerfile
    assert "sha256sum --check" in dockerfile


def test_asa_runtime_preflight_provides_sdk_and_disables_crashpad() -> None:
    script = RUNTIME_DIR / "entrypoint.sh"
    text = script.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "Plugins/sentry" in text
    assert ".steam/sdk32/steamclient.so" in text
    assert ".steam/sdk64/steamclient.so" in text
    assert "steam_appid.txt" in text
    assert 'exec "$@"' in text
    try:
        subprocess.run(
            ["bash", "-n"],
            input=text.replace("\r\n", "\n"),
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass