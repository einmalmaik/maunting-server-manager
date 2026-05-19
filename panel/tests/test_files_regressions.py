from __future__ import annotations

import stat
from types import SimpleNamespace
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import files


def test_normalize_relative_upload_path_preserves_nested_directories():
    relative = files._normalize_relative_upload_path("mission/custom/events/types.xml", allow_nested=True)

    assert relative == Path("mission/custom/events/types.xml")


def test_normalize_relative_upload_path_rejects_parent_traversal():
    with pytest.raises(HTTPException) as exc:
        files._normalize_relative_upload_path("../outside.txt", allow_nested=True)

    assert exc.value.status_code == 400


def test_safe_extract_zip_extracts_into_target_directory(tmp_path: Path):
    base_dir = tmp_path / "server"
    destination = base_dir / "uploads" / "package"
    archive_path = base_dir / "uploads" / "package.zip"
    destination.parent.mkdir(parents=True)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("configs/cfgeventspawns.xml", "hello")
        archive.writestr("profiles/settings.txt", "world")

    destination.mkdir()
    extracted = files._safe_extract_zip(archive_path, destination, base_dir)

    assert extracted == 2
    assert (destination / "configs" / "cfgeventspawns.xml").read_text(encoding="utf-8") == "hello"
    assert (destination / "profiles" / "settings.txt").read_text(encoding="utf-8") == "world"


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path):
    base_dir = tmp_path / "server"
    destination = base_dir / "uploads" / "package"
    archive_path = base_dir / "uploads" / "package.zip"
    destination.parent.mkdir(parents=True)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "nope")

    destination.mkdir()

    with pytest.raises(HTTPException) as exc:
        files._safe_extract_zip(archive_path, destination, base_dir)

    assert exc.value.status_code == 400


def test_safe_extract_zip_rejects_archives_that_expand_beyond_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base_dir = tmp_path / "server"
    destination = base_dir / "uploads" / "package"
    archive_path = base_dir / "uploads" / "package.zip"
    destination.parent.mkdir(parents=True)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("configs/part1.txt", "1234")
        archive.writestr("configs/part2.txt", "56")

    destination.mkdir()
    monkeypatch.setattr(files, "_MAX_EXTRACT_SIZE", 5)

    with pytest.raises(HTTPException) as exc:
        files._safe_extract_zip(archive_path, destination, base_dir)

    assert exc.value.status_code == 413


def test_write_file_falls_back_to_in_place_update_when_atomic_replace_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    target = server_root / "serverfiles" / "mpmissions" / "chernarusplus" / "db" / "types.xml"
    target.parent.mkdir(parents=True)
    target.write_text("<types />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    original_replace = files.os.replace

    def deny_replace(src: str | bytes | Path, dst: str | bytes | Path) -> None:
        if Path(dst) == target:
            raise PermissionError("replace denied")
        original_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", deny_replace)

    result = files.write_file(
        body=files.FileWriteBody(path="serverfiles/mpmissions/chernarusplus/db/types.xml", content="<types><type name=\"Ammo\" /></types>\r\n"),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "<types><type name=\"Ammo\" /></types>\n"


def test_write_file_makes_readonly_target_temporarily_writable_for_in_place_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    target = server_root / "serverfiles" / "mpmissions" / "chernarusplus" / "db" / "events.xml"
    target.parent.mkdir(parents=True)
    target.write_text("<events />\n", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    original_replace = files.os.replace

    def deny_replace(src: str | bytes | Path, dst: str | bytes | Path) -> None:
        if Path(dst) == target:
            raise PermissionError("replace denied")
        original_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", deny_replace)

    result = files.write_file(
        body=files.FileWriteBody(path="serverfiles/mpmissions/chernarusplus/db/events.xml", content="<events><event name=\"test\" /></events>\n"),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "<events><event name=\"test\" /></events>\n"


def test_write_file_rejects_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    target = server_root / "serverfiles" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"ok": true}\n', encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    with pytest.raises(HTTPException) as exc:
        files.write_file(
            body=files.FileWriteBody(path="serverfiles/settings.json", content='{"broken": }\n'),
            server="alpha",
            user=SimpleNamespace(username="owner"),
        )

    assert exc.value.status_code == 422
    assert "Invalid JSON" in str(exc.value.detail)


def test_write_file_rejects_invalid_xml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    target = server_root / "serverfiles" / "types.xml"
    target.parent.mkdir(parents=True)
    target.write_text("<types />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    with pytest.raises(HTTPException) as exc:
        files.write_file(
            body=files.FileWriteBody(path="serverfiles/types.xml", content="<types><broken></types>\n"),
            server="alpha",
            user=SimpleNamespace(username="owner"),
        )

    assert exc.value.status_code == 422
    assert "Invalid XML" in str(exc.value.detail)


def test_delete_paths_coalesces_nested_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    mission_dir = server_root / "serverfiles" / "mpmissions" / "chernarusplus"
    child_file = mission_dir / "db" / "types.xml"
    child_file.parent.mkdir(parents=True)
    child_file.write_text("<types />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    result = files.delete_paths(
        body=files.DeleteManyBody(paths=[
            "serverfiles/mpmissions/chernarusplus",
            "serverfiles/mpmissions/chernarusplus/db/types.xml",
        ]),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    assert result["ok"] is True
    assert result["paths"] == ["serverfiles/mpmissions/chernarusplus"]
    assert not mission_dir.exists()


def test_rename_path_renames_selected_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    source = server_root / "serverfiles" / "mpmissions" / "chernarusplus" / "db" / "types.xml"
    source.parent.mkdir(parents=True)
    source.write_text("<types />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    result = files.rename_path(
        body=files.RenameBody(path="serverfiles/mpmissions/chernarusplus/db/types.xml", new_name="events.xml"),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    assert result["ok"] is True
    assert result["path"] == "serverfiles/mpmissions/chernarusplus/db/events.xml"
    assert not source.exists()
    assert (source.parent / "events.xml").read_text(encoding="utf-8") == "<types />\n"


def test_rename_path_rejects_conflicting_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    directory = server_root / "serverfiles" / "mpmissions" / "chernarusplus" / "db"
    directory.mkdir(parents=True)
    (directory / "types.xml").write_text("<types />\n", encoding="utf-8")
    (directory / "events.xml").write_text("<events />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    with pytest.raises(HTTPException) as exc:
        files.rename_path(
            body=files.RenameBody(path="serverfiles/mpmissions/chernarusplus/db/types.xml", new_name="events.xml"),
            server="alpha",
            user=SimpleNamespace(username="owner"),
        )

    assert exc.value.status_code == 409


def test_prepare_upload_destination_rejects_existing_item_with_different_casing(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    destination.mkdir(parents=True)
    (destination / "types.xml").write_text("<types />\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        files._prepare_upload_destination(destination / "Types.xml", server_root)

    assert exc.value.status_code == 409
    assert "different casing" in str(exc.value.detail)


def test_prepare_upload_destination_allows_overwriting_exact_existing_file(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    destination.mkdir(parents=True)
    target = destination / "events.xml"
    target.write_text("<events />\n", encoding="utf-8")

    files._prepare_upload_destination(target, server_root)


def test_prepare_batch_uploads_rejects_internal_case_only_duplicates(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    destination.mkdir(parents=True)

    uploads = [
        SimpleNamespace(filename="missions/Types.xml"),
        SimpleNamespace(filename="missions/types.xml"),
    ]

    with pytest.raises(HTTPException) as exc:
        files._prepare_batch_uploads(destination, uploads, server_root)

    assert exc.value.status_code == 409
    assert "only differ by letter casing" in str(exc.value.detail)


def test_prepare_batch_uploads_rejects_existing_directory_segment_with_different_casing(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    existing_directory = destination / "mpmissions"
    existing_directory.mkdir(parents=True)

    uploads = [SimpleNamespace(filename="MPMissions/chernarusplus/db/types.xml")]

    with pytest.raises(HTTPException) as exc:
        files._prepare_batch_uploads(destination, uploads, server_root)

    assert exc.value.status_code == 409
    assert "different casing" in str(exc.value.detail)


def test_prepare_batch_uploads_allows_overwriting_exact_existing_file(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    destination.mkdir(parents=True)
    (destination / "events.xml").write_text("<events />\n", encoding="utf-8")

    uploads = [SimpleNamespace(filename="events.xml")]

    prepared = files._prepare_batch_uploads(destination, uploads, server_root)

    assert prepared[0][1] == destination / "events.xml"


def test_prepare_batch_uploads_validates_existing_nested_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    destination = server_root / "serverfiles"
    existing_directory = destination / "configs"
    existing_directory.mkdir(parents=True)

    validate_calls: list[str] = []
    original_validate = files._validate_path

    def tracking_validate(raw_path: str, base_dir: Path, follow_leaf_symlink: bool = True) -> Path:
        validate_calls.append(raw_path)
        return original_validate(raw_path, base_dir, follow_leaf_symlink)

    monkeypatch.setattr(files, "_validate_path", tracking_validate)

    uploads = [SimpleNamespace(filename="configs/subdir/types.xml")]

    files._prepare_batch_uploads(destination, uploads, server_root)

    assert any(str(existing_directory) in call for call in validate_calls)


def test_find_case_insensitive_match_finds_existing_directory_with_different_casing(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    existing_directory = server_root / "serverfiles" / "profiles"
    existing_directory.mkdir(parents=True)

    match = files._find_case_insensitive_match(existing_directory.parent, "Profiles")

    assert match == existing_directory


def test_find_case_insensitive_match_or_403_converts_permission_error(monkeypatch: pytest.MonkeyPatch):
    def deny(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(files, "_find_case_insensitive_match", deny)

    with pytest.raises(HTTPException) as exc:
        files._find_case_insensitive_match_or_403(Path("/tmp"), "types.xml")

    assert exc.value.status_code == 403


def test_ensure_directory_writable_adds_user_write_and_execute_bits(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    target_dir = server_root / "serverfiles" / "mpmissions"
    target_dir.mkdir(parents=True)
    target_dir.chmod(stat.S_IRUSR)

    files._ensure_directory_writable(target_dir, server_root)

    mode = stat.S_IMODE(target_dir.stat().st_mode)
    assert mode & stat.S_IWUSR
    assert mode & stat.S_IXUSR


def test_rename_path_allows_case_only_rename_of_same_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    source = server_root / "serverfiles" / "mpmissions" / "chernarusplus" / "db" / "Types.xml"
    source.parent.mkdir(parents=True)
    source.write_text("<types />\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    result = files.rename_path(
        body=files.RenameBody(path="serverfiles/mpmissions/chernarusplus/db/Types.xml", new_name="types.xml"),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    assert result["ok"] is True
    assert result["path"].endswith("types.xml")
    assert (source.parent / "types.xml").exists()


def test_coalesce_download_targets_drops_nested_children_when_parent_directory_is_selected(tmp_path: Path):
    server_root = tmp_path / "servers" / "alpha"
    mission_dir = server_root / "serverfiles" / "mpmissions" / "chernarusplus"
    mission_dir.mkdir(parents=True)
    (mission_dir / "db" / "types.xml").parent.mkdir(parents=True)
    (mission_dir / "db" / "types.xml").write_text("<types />\n", encoding="utf-8")
    profile_file = server_root / "serverprofile" / "settings.txt"
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text("profile\n", encoding="utf-8")

    targets = files._coalesce_download_targets(
        [
            "serverfiles/mpmissions/chernarusplus",
            "serverfiles/mpmissions/chernarusplus/db/types.xml",
            "serverprofile/settings.txt",
        ],
        server_root,
    )

    assert sorted(target.relative_path for target in targets) == [
        "serverfiles/mpmissions/chernarusplus",
        "serverprofile/settings.txt",
    ]


def test_download_batch_writes_mixed_archive_and_skips_nested_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    mission_dir = server_root / "serverfiles" / "mpmissions" / "chernarusplus"
    mission_file = mission_dir / "db" / "types.xml"
    profile_file = server_root / "serverprofile" / "settings.txt"
    mission_file.parent.mkdir(parents=True)
    profile_file.parent.mkdir(parents=True)
    mission_file.write_text("<types />\n", encoding="utf-8")
    profile_file.write_text("profile\n", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    response = files.download_batch(
        body=files.DownloadManyBody(paths=[
            "serverfiles/mpmissions/chernarusplus",
            "serverfiles/mpmissions/chernarusplus/db/types.xml",
            "serverprofile/settings.txt",
        ]),
        server="alpha",
        user=SimpleNamespace(username="owner"),
    )

    archive_path = Path(response.path)
    try:
        assert response.media_type == "application/zip"
        with zipfile.ZipFile(archive_path) as archive:
            assert sorted(archive.namelist()) == [
                "serverfiles/mpmissions/chernarusplus/",
                "serverfiles/mpmissions/chernarusplus/db/types.xml",
                "serverprofile/settings.txt",
            ]
            assert archive.read("serverfiles/mpmissions/chernarusplus/db/types.xml").decode("utf-8").replace("\r\n", "\n") == "<types />\n"
            assert archive.read("serverprofile/settings.txt").decode("utf-8").replace("\r\n", "\n") == "profile\n"
    finally:
        archive_path.unlink(missing_ok=True)


def test_download_batch_rejects_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    server_root.mkdir(parents=True)

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)

    with pytest.raises(HTTPException) as exc:
        files.download_batch(
            body=files.DownloadManyBody(paths=["../escape.txt"]),
            server="alpha",
            user=SimpleNamespace(username="owner"),
        )

    assert exc.value.status_code == 403


def test_download_batch_rejects_archives_above_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    target = server_root / "serverfiles" / "logs" / "latest.log"
    target.parent.mkdir(parents=True)
    target.write_text("123456", encoding="utf-8")

    monkeypatch.setattr(files, "_get_server_base_dir", lambda server: server_root)
    monkeypatch.setattr(files, "_MAX_DOWNLOAD_ARCHIVE_SIZE", 5)

    with pytest.raises(HTTPException) as exc:
        files.download_batch(
            body=files.DownloadManyBody(paths=["serverfiles/logs/latest.log"]),
            server="alpha",
            user=SimpleNamespace(username="owner"),
        )

    assert exc.value.status_code == 413
    assert "Download archive too large" in str(exc.value.detail)
