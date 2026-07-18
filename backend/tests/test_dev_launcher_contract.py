"""Windows development launcher regression contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_delayed_expansion_cannot_corrupt_dev_service_commands() -> None:
    launcher = (ROOT / "start-dev.bat").read_text(encoding="utf-8")

    assert "EnableDelayedExpansion" in launcher
    assert "long!!" not in launcher
    assert "MSM_SECRET_KEY=test-secret-key-for-dev-only-32-bytes-long&&" in launcher
    assert "python.exe -m uvicorn main:app --reload --port 8000" in launcher
