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
) -> None:
    assert file_history_service.snapshot(8, "game.cfg", "one", 1)
    assert not file_history_service.snapshot(8, "game.cfg", "one", 1)
    assert file_history_service.snapshot(8, "game.cfg", "two", 1)
    assert file_history_service.snapshot(8, "game.cfg", "three", 1)
    versions_before_prune = file_history_service.list_versions(8, "game.cfg")
    oldest_id = versions_before_prune[-1]["id"]
    assert file_history_service.snapshot(8, "game.cfg", "four", 1)

    versions = file_history_service.list_versions(8, "game.cfg")
    assert len(versions) == 3
    assert [file_history_service.read_version(8, "game.cfg", item["id"])["content"] for item in versions] == ["four", "three", "two"]
    assert len(list(history_root.rglob("*.enc"))) == 3
    assert not list(history_root.rglob(f"{oldest_id}.enc"))


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


# ── Fremder Eigentuemer im Versionsverlauf ────────────────────────────
#
# Gemeldet am 18.08.2026: der Betreiber konnte in **keinem** Server mehr eine
# Datei speichern, ueberall "Datei konnte nicht gespeichert werden" (HTTP 500).
# Ursache war nicht die Zieldatei, sondern der Verlauf daneben:
#
#   os.chmod('/opt/msm/.msm-file-history/89/f1209d4b…')
#   PermissionError: [Errno 1] Operation not permitted
#
# Ein als root gelaufenes Wartungsskript hatte den Verlauf angelegt. `chmod`
# wirft `EPERM` bei fremdem Eigentuemer — auch dann, wenn die Rechte laengst
# stimmen und hineingeschrieben werden darf. Weil der Verlauf allen Servern
# gemeinsam ist, fiel damit das Speichern ueberall gleichzeitig aus.


def test_a_foreign_owner_does_not_block_saving(
    history_root: Path,
    fake_dis: dict[str, tuple[str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein nicht setzbarer Modus verhindert den Snapshot nicht.

    Der Modus ist eine Absicherung, kein Selbstzweck: der Verlauf enthaelt
    verschluesselte Dateiinhalte, deshalb 0700. Duerfen wir ihn nicht setzen,
    ist das eine Meldung wert — aber kein Grund, dem Menschen das Speichern
    der Datei zu verweigern, um die es ihm gerade geht.
    """
    def kein_chmod(_pfad, _modus):
        raise PermissionError(1, "Operation not permitted")

    # Nur das **Verzeichnis** ist fremd. Die Snapshot-Datei legt der Dienst
    # gleich selbst an (`os.fchmod` auf dem offenen Deskriptor), sie gehoert
    # also immer uns — genau wie im gemeldeten Fall, wo der Verlaufsordner
    # root gehoerte, aber jede neu geschriebene Datei dem Panel.
    echtes_chmod = file_history_service.os.chmod

    def nur_verzeichnis_gesperrt(pfad, modus):
        if Path(pfad).is_dir():
            return kein_chmod(pfad, modus)
        return echtes_chmod(pfad, modus)

    monkeypatch.setattr(file_history_service.os, "chmod", nur_verzeichnis_gesperrt)

    assert file_history_service.snapshot(11, "server.ini", "Wert=1", 4)
    versionen = file_history_service.list_versions(11, "server.ini")
    assert len(versionen) == 1
    # Und der Inhalt ist trotzdem verschluesselt abgelegt — der Fallback macht
    # den Verlauf nicht unsicherer, er macht ihn nur nicht zur Sperre.
    verschluesselt = list(history_root.rglob("*.enc"))
    assert len(verschluesselt) == 1
    assert "Wert=1" not in verschluesselt[0].read_text(encoding="utf-8")


def test_a_correct_mode_is_not_set_again(
    history_root: Path,
    fake_dis: dict[str, tuple[str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stimmt der Modus schon, wird gar nicht erst `chmod` gerufen.

    Das ist die Haelfte des Fixes, die den Fehler ueberhaupt vermeidet statt
    ihn nur abzufangen: im Normalbetrieb legt das Panel das Verzeichnis selbst
    an, der Modus stimmt ab der ersten Sekunde, und ein `chmod` waere bei
    jedem einzelnen Speichervorgang ein unnoetiger Systemaufruf, der bei
    fremdem Eigentuemer wirft.
    """
    aufrufe: list[int] = []
    echtes_chmod = file_history_service.os.chmod

    def zaehlend(pfad, modus):
        # Nur Verzeichnisse zaehlen: die Snapshot-Dateien bekommen ihren Modus
        # ohnehin bei jedem Schreiben neu, und um die geht es hier nicht.
        if Path(pfad).is_dir():
            aufrufe.append(modus)
        return echtes_chmod(pfad, modus)

    monkeypatch.setattr(file_history_service.os, "chmod", zaehlend)

    file_history_service.snapshot(12, "a.ini", "x=1", 4)
    erste_runde = len(aufrufe)
    # Zweiter Snapshot in dasselbe Verzeichnis: der Modus stimmt bereits.
    file_history_service.snapshot(12, "a.ini", "x=2", 4)

    assert len(aufrufe) == erste_runde, (
        "chmod wurde erneut gerufen, obwohl der Modus schon stimmte"
    )
