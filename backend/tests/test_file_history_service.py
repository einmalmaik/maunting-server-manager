from __future__ import annotations

from pathlib import Path

import pytest

from services import file_history_service
from services.dis_client import DisSidecarError


@pytest.fixture
def history_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    panel_root = tmp_path / "panel"
    monkeypatch.setattr(file_history_service.settings, "panel_config_dir", str(panel_root))
    return panel_root / ".msm-file-history"


@pytest.fixture
def fake_dis(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str, str | None]]:
    sealed: dict[str, tuple[str, str | None]] = {}

    def encrypt(plaintext: str, aad: str | None = None) -> str:
        token = f"cipher-{len(sealed) + 1}"
        sealed[token] = (plaintext, aad)
        return token

    def decrypt(ciphertext: str, aad: str | None = None) -> str:
        plaintext, expected_aad = sealed[ciphertext]
        if aad != expected_aad:
            raise DisSidecarError("AAD mismatch")
        return plaintext

    monkeypatch.setattr(file_history_service.DisClient, "encrypt", encrypt)
    monkeypatch.setattr(file_history_service.DisClient, "decrypt", decrypt)
    return sealed


def test_history_is_encrypted_with_server_version_aad_and_roundtrips(
    history_root: Path,
    fake_dis: dict[str, tuple[str, str | None]],
) -> None:
    assert file_history_service.snapshot(7, "config/server.ini", "SecretValue=synthetic", 3)
    versions = file_history_service.list_versions(7, "config/server.ini")
    restored = file_history_service.read_version(7, "config/server.ini", versions[0]["id"])

    assert restored["content"] == "SecretValue=synthetic"
    ciphertext_files = list(history_root.rglob("*.enc"))
    assert len(ciphertext_files) == 1
    assert "SecretValue" not in ciphertext_files[0].read_text(encoding="utf-8")
    assert next(iter(fake_dis.values()))[1] == (
        f"msm:file-history:v1:7:{file_history_service._file_key('config/server.ini')}:{versions[0]['id']}"
    )


def test_history_deduplicates_and_prunes_oldest_deterministically(
    history_root: Path,
    fake_dis: dict[str, tuple[str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_history_service, "MAX_VERSIONS_PER_FILE", 2)
    assert file_history_service.snapshot(8, "game.cfg", "one", 1)
    assert not file_history_service.snapshot(8, "game.cfg", "one", 1)
    assert file_history_service.snapshot(8, "game.cfg", "two", 1)
    assert file_history_service.snapshot(8, "game.cfg", "three", 1)

    versions = file_history_service.list_versions(8, "game.cfg")
    assert len(versions) == 2
    assert [file_history_service.read_version(8, "game.cfg", item["id"])["content"] for item in versions] == ["three", "two"]
    assert len(list(history_root.rglob("*.enc"))) == 2


def test_history_fails_closed_without_plaintext_fallback(
    history_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_plaintext: str, aad: str | None = None) -> str:
        raise DisSidecarError("synthetic unavailable")

    monkeypatch.setattr(file_history_service.DisClient, "encrypt", unavailable)
    with pytest.raises(DisSidecarError):
        file_history_service.snapshot(9, "settings.ini", "sensitive-synthetic", 2)

    assert list(history_root.rglob("*.enc")) == []
    assert list(history_root.rglob("index.json")) == []
