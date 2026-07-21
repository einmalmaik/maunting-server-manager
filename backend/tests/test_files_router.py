"""Tests fuer den File-Manager-Router.

Schwerpunkte:
- Path-Traversal-Vektoren (`..`, absolute Pfade, Symlink-Escapes, `/foo` vs `/foobar`-Boundary).
- Permission-Matrix (read/write/delete via Phase-2 RBAC).
- Chunked-Upload-Lifecycle (init/chunk/status/finalize/abort).
- Move + Search Endpoints.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Server, ServerPermission, User


@pytest.fixture
def server_with_dir(db: Session, owner_user: User, tmp_path: Path) -> Server:
    """Server, dessen ``install_dir`` ein echter, leerer Ordner ist."""
    install_dir = tmp_path / "srv_root"
    install_dir.mkdir(parents=True, exist_ok=True)
    server = Server(
        name="Files Test",
        game_type="dayz",
        install_dir=str(install_dir),
        container_name="msm-srv-files",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _grant(db: Session, user: User, server: Server, keys: list[str]) -> None:
    for key in keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


# ── _safe_path / Path-Traversal ──────────────────────────────────────────


class TestPathTraversal:
    def test_browse_rejects_double_dot(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        res = client.get(f"/api/files/{server_with_dir.id}/browse?path=../etc", cookies=owner_cookies)
        assert res.status_code == 400

    def test_browse_rejects_absolute_path(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        res = client.get(f"/api/files/{server_with_dir.id}/browse?path=/etc", cookies=owner_cookies)
        assert res.status_code == 400

    def test_browse_rejects_nested_traversal(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        res = client.get(f"/api/files/{server_with_dir.id}/browse?path=sub/../../etc", cookies=owner_cookies)
        assert res.status_code == 400

    def test_safe_path_no_boundary_bug(self, client: TestClient, owner_cookies: dict, server_with_dir: Server, tmp_path: Path):
        """Sibling-Ordner mit gleichem Praefix darf nicht via Praefix-Trick erreichbar sein.

        Wir legen neben ``srv_root`` einen Ordner ``srv_root_other`` an und
        versuchen ihn ueber Backlinks zu erreichen. Mit dem alten
        ``startswith``-Check waere ``../srv_root_other`` ein Bypass; mit
        ``relative_to`` ist es korrekt verboten.
        """
        sibling = tmp_path / "srv_root_other"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("nope")
        res = client.get(
            f"/api/files/{server_with_dir.id}/browse?path=../srv_root_other",
            cookies=owner_cookies,
        )
        # 400 wegen `..` (Defense-in-Depth) — ohne die `..`-Sperre waere es 403
        # wegen `relative_to`. Beides ist sicher; KISS: wir blocken `..` frueh.
        assert res.status_code in (400, 403)

    def test_symlink_escape_blocked(self, client: TestClient, owner_cookies: dict, server_with_dir: Server, tmp_path: Path, csrf_token: str):
        """Symlinks innerhalb des Roots, die nach AUSSEN zeigen, muessen gestoppt werden."""
        outside = tmp_path / "outside_secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("you shall not see this")
        # Symlink innerhalb des Server-Roots, der nach aussen zeigt.
        link = Path(server_with_dir.install_dir) / "leak"
        link.symlink_to(outside)

        # Lesezugriff auf den Symlink-Inhalt schlaegt fehl, weil resolve den
        # symlink dereferenziert und relative_to den Pfad ausserhalb sieht.
        res = client.get(
            f"/api/files/{server_with_dir.id}/read?path=leak/secret.txt",
            cookies=owner_cookies,
        )
        assert res.status_code == 403


# ── Permission-Matrix ─────────────────────────────────────────────────────


class TestFilesPermissions:
    def test_user_without_perm_cannot_browse(
        self, client: TestClient, regular_user: User, user_cookies: dict, server_with_dir: Server
    ):
        res = client.get(f"/api/files/{server_with_dir.id}/browse", cookies=user_cookies)
        assert res.status_code == 403

    def test_read_requires_files_read(
        self, client: TestClient, regular_user: User, user_cookies: dict, server_with_dir: Server, db: Session
    ):
        (Path(server_with_dir.install_dir) / "a.txt").write_text("hi")
        # Nur write-Permission → read soll trotzdem failen.
        _grant(db, regular_user, server_with_dir, ["server.files.write"])
        res = client.get(
            f"/api/files/{server_with_dir.id}/read?path=a.txt",
            cookies=user_cookies,
        )
        assert res.status_code == 403

    def test_delete_requires_files_delete(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        server_with_dir: Server, db: Session
    ):
        (Path(server_with_dir.install_dir) / "x.txt").write_text("hi")
        _grant(db, regular_user, server_with_dir, ["server.files.read", "server.files.write"])
        res = client.delete(
            f"/api/files/{server_with_dir.id}/delete?path=x.txt",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403

    def test_csrf_required_on_write(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        res = client.put(
            f"/api/files/{server_with_dir.id}/write?path=t.txt",
            cookies=owner_cookies,
            json={"content": "x"},
        )
        assert res.status_code == 403


# ── Browse / Read / Write ──────────────────────────────────────────────────


class TestBrowseReadWrite:
    def test_browse_empty(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        res = client.get(f"/api/files/{server_with_dir.id}/browse", cookies=owner_cookies)
        assert res.status_code == 200
        assert res.json() == {"path": "", "entries": [], "exists": True}

    def test_write_then_read(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        res = client.put(
            f"/api/files/{server_with_dir.id}/write?path=config.json",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"content": '{"port": 1234}'},
        )
        assert res.status_code == 200

        res = client.get(
            f"/api/files/{server_with_dir.id}/read?path=config.json",
            cookies=owner_cookies,
        )
        assert res.status_code == 200
        assert res.json()["content"] == '{"port": 1234}'

    def test_create_only_never_overwrites_existing_file(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        target = Path(server_with_dir.install_dir) / "existing.ini"
        target.write_text("keep-me", encoding="utf-8")

        res = client.put(
            f"/api/files/{server_with_dir.id}/write?path=existing.ini",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"content": "", "create_only": True},
        )

        assert res.status_code == 409
        assert target.read_text(encoding="utf-8") == "keep-me"

    def test_read_exposes_revision_and_real_metadata(
        self, client: TestClient, owner_cookies: dict, server_with_dir: Server
    ):
        target = Path(server_with_dir.install_dir) / "metadata.ini"
        target.write_text("Port=27015", encoding="utf-8")
        res = client.get(
            f"/api/files/{server_with_dir.id}/read?path=metadata.ini",
            cookies=owner_cookies,
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["revision"].startswith("sha256:")
        assert payload["size"] == len("Port=27015")
        assert payload["modified"] > 0
        assert set(("mode", "owner", "group")).issubset(payload)

    def test_stale_revision_returns_conflict_without_overwriting(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        target = Path(server_with_dir.install_dir) / "race.ini"
        target.write_text("opened", encoding="utf-8")
        opened = client.get(
            f"/api/files/{server_with_dir.id}/read?path=race.ini",
            cookies=owner_cookies,
        ).json()
        target.write_text("external-change", encoding="utf-8")

        res = client.put(
            f"/api/files/{server_with_dir.id}/write?path=race.ini",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"content": "local-change", "expected_revision": opened["revision"]},
        )

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "FILE_REVISION_CONFLICT"
        assert target.read_text(encoding="utf-8") == "external-change"

    def test_write_nested_scum_ini_path(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        server_with_dir: Server,
    ):
        path = "SCUM/Saved/Config/WindowsServer/ServerSettings.ini"
        res = client.put(
            f"/api/files/{server_with_dir.id}/write?path={path}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"content": "ServerName=SCUM"},
        )

        assert res.status_code == 200
        target = Path(server_with_dir.install_dir) / "SCUM" / "Saved" / "Config" / "WindowsServer" / "ServerSettings.ini"
        assert target.read_text(encoding="utf-8") == "ServerName=SCUM"

    def test_write_repairs_permissions_after_permission_error(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        server_with_dir: Server,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from services import file_edit_service

        original_write_text = file_edit_service.write_text
        attempts = 0

        def flaky_write_text(target: Path, *args, **kwargs):
            nonlocal attempts
            if target.name == "locked.ini":
                attempts += 1
                if attempts == 1:
                    raise PermissionError(13, "Permission denied", str(target))
            return original_write_text(target, *args, **kwargs)

        monkeypatch.setattr(file_edit_service, "write_text", flaky_write_text)

        with patch("routers.files._repair_install_permissions", return_value={"ok": True}) as mock_repair:
            res = client.put(
                f"/api/files/{server_with_dir.id}/write?path=locked.ini",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"content": "ServerName=SCUM"},
            )

        assert res.status_code == 200
        mock_repair.assert_called_once_with(server_with_dir.install_dir)
        assert attempts == 2
        assert (Path(server_with_dir.install_dir) / "locked.ini").read_text(encoding="utf-8") == "ServerName=SCUM"

    def test_write_reports_failed_permission_repair(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        server_with_dir: Server,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from services import file_edit_service

        def locked_write_text(target: Path, *args, **kwargs):
            raise PermissionError(13, "Permission denied", str(target))

        monkeypatch.setattr(file_edit_service, "write_text", locked_write_text)

        with patch(
            "routers.files._repair_install_permissions",
            return_value={"ok": False, "error": "repair failed"},
        ):
            res = client.put(
                f"/api/files/{server_with_dir.id}/write?path=locked.ini",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"content": "ServerName=SCUM"},
            )

        assert res.status_code == 500
        assert res.json()["detail"] == "Datei konnte nicht gespeichert werden"

    def test_read_oversized_file_rejected(
        self, client: TestClient, owner_cookies: dict, server_with_dir: Server
    ):
        from routers.files import MAX_EDIT_SIZE
        big = Path(server_with_dir.install_dir) / "big.bin"
        big.write_bytes(b"\0" * (MAX_EDIT_SIZE + 1))
        res = client.get(
            f"/api/files/{server_with_dir.id}/read?path=big.bin",
            cookies=owner_cookies,
        )
        assert res.status_code == 413

    def test_permission_repair_is_scoped_to_server_root(self, server_with_dir: Server):
        from routers.files import _repair_install_permissions
        from services.docker_service import (
            PERMISSION_REPAIR_CAPS,
            PERMISSION_REPAIR_CONTAINER_DIR,
            PERMISSION_REPAIR_IMAGE,
        )

        with patch("routers.files.docker_service.host_uid_gid", return_value=(1001, 1002)), \
             patch("routers.files.docker_service.run_ephemeral", return_value={"ok": True}) as mock_run:
            result = _repair_install_permissions(server_with_dir.install_dir)

        assert result == {"ok": True}
        kwargs = mock_run.call_args.kwargs
        assert kwargs["image"] == PERMISSION_REPAIR_IMAGE
        assert kwargs["entrypoint"] == "bash"
        assert kwargs["user"] == "0:0"
        assert kwargs["cap_adds"] == PERMISSION_REPAIR_CAPS
        assert kwargs["volumes"][0].host_path == str(Path(server_with_dir.install_dir).resolve(strict=False))
        assert kwargs["volumes"][0].container_path == PERMISSION_REPAIR_CONTAINER_DIR
        script = kwargs["command"][1]
        assert f"find {PERMISSION_REPAIR_CONTAINER_DIR} -xdev -type f" in script
        assert "chmod a+rwX" in script
        assert "chown" not in script

    def test_file_permission_repair_does_not_change_runtime_owner(self, server_with_dir: Server):
        from routers.files import _repair_install_permissions

        with patch("routers.files.docker_service.host_uid_gid", return_value=(1001, 1002)), \
             patch("routers.files.docker_service.run_ephemeral", return_value={"ok": True}) as mock_run:
            result = _repair_install_permissions(server_with_dir.install_dir)

        assert result == {"ok": True}
        script = mock_run.call_args.kwargs["command"][1]
        assert "chmod a+rwX" in script
        assert "chown" not in script


# ── Upload (Single-Shot) + Blocked Extensions ─────────────────────────────


class TestUpload:
    def test_upload_creates_file(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload?path=",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            files={"file": ("hello.txt", io.BytesIO(b"hi"), "text/plain")},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "hello.txt").read_bytes() == b"hi"

    def test_upload_blocked_extension(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            files={"file": ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )
        assert res.status_code == 400


# ── Chunked Upload Lifecycle ──────────────────────────────────────────────


class TestChunkedUpload:
    def test_lifecycle_init_chunk_finalize(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        # 1. Init
        body = {"path": "", "filename": "modpack.zip", "total_size": 16}
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/init",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json=body,
        )
        assert res.status_code == 200
        upload_id = res.json()["upload_id"]
        assert len(upload_id) == 32

        # 2. Zwei Chunks (8 + 8 bytes)
        for chunk_bytes in (b"AAAAAAAA", b"BBBBBBBB"):
            res = client.put(
                f"/api/files/{server_with_dir.id}/upload/{upload_id}/chunk",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
                files={"chunk": ("chunk.bin", io.BytesIO(chunk_bytes), "application/octet-stream")},
            )
            assert res.status_code == 200

        # 3. Status (Resume-Hilfe)
        res = client.get(
            f"/api/files/{server_with_dir.id}/upload/{upload_id}/status",
            cookies=owner_cookies,
        )
        assert res.status_code == 200
        assert res.json()["received"] == 16

        # 4. Finalize
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/{upload_id}/finalize",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        final = Path(server_with_dir.install_dir) / "modpack.zip"
        assert final.exists()
        assert final.read_bytes() == b"AAAAAAAABBBBBBBB"

    def test_finalize_rejects_size_mismatch(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/init",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"path": "", "filename": "x.bin", "total_size": 99},
        )
        upload_id = res.json()["upload_id"]
        # Nur 4 von 99 bytes hochladen.
        client.put(
            f"/api/files/{server_with_dir.id}/upload/{upload_id}/chunk",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            files={"chunk": ("c.bin", io.BytesIO(b"abcd"), "application/octet-stream")},
        )
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/{upload_id}/finalize",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 400

    def test_init_rejects_blocked_extension(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/init",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"path": "", "filename": "evil.exe", "total_size": 4},
        )
        assert res.status_code == 400

    def test_init_rejects_path_in_filename(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server
    ):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/init",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"path": "", "filename": "../escape.txt", "total_size": 4},
        )
        assert res.status_code == 400

    def test_abort_removes_temp(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        res = client.post(
            f"/api/files/{server_with_dir.id}/upload/init",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"path": "", "filename": "to_abort.bin", "total_size": 4},
        )
        upload_id = res.json()["upload_id"]
        res = client.delete(
            f"/api/files/{server_with_dir.id}/upload/{upload_id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        from routers.files import CHUNK_TMP_DIRNAME
        leftover = Path(server_with_dir.install_dir) / CHUNK_TMP_DIRNAME / f"{upload_id}.part"
        assert not leftover.exists()


# ── Move / Rename / Mkdir / Delete ────────────────────────────────────────


class TestMutations:
    def test_mkdir_and_move(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        # mkdir mods
        client.post(
            f"/api/files/{server_with_dir.id}/mkdir",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"name": "mods"},
        )
        # write a file at root
        client.put(
            f"/api/files/{server_with_dir.id}/write?path=note.txt",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"content": "hi"},
        )
        # move note.txt into mods/
        res = client.post(
            f"/api/files/{server_with_dir.id}/move",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"from_path": "note.txt", "to_dir": "mods"},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "mods" / "note.txt").exists()
        assert not (Path(server_with_dir.install_dir) / "note.txt").exists()

    def test_move_into_self_blocked(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        client.post(
            f"/api/files/{server_with_dir.id}/mkdir",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"name": "outer"},
        )
        res = client.post(
            f"/api/files/{server_with_dir.id}/move",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"from_path": "outer", "to_dir": "outer", "new_name": "inner"},
        )
        assert res.status_code == 400

    def test_rename(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        (Path(server_with_dir.install_dir) / "old.txt").write_text("hi")
        res = client.post(
            f"/api/files/{server_with_dir.id}/rename?path=old.txt",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"new_name": "new.txt"},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "new.txt").exists()

    def test_delete_install_dir_blocked(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server):
        res = client.delete(
            f"/api/files/{server_with_dir.id}/delete?path=",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code in (403, 404)

    def test_delete_repairs_permissions_after_permission_error(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        server_with_dir: Server,
        monkeypatch: pytest.MonkeyPatch,
    ):
        target = Path(server_with_dir.install_dir) / "locked.txt"
        target.write_text("locked", encoding="utf-8")
        original_unlink = Path.unlink
        attempts = 0

        def flaky_unlink(self: Path, *args, **kwargs):
            nonlocal attempts
            if self.name == "locked.txt":
                attempts += 1
                if attempts == 1:
                    raise PermissionError(13, "Permission denied", str(self))
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        with patch("routers.files._repair_install_permissions", return_value={"ok": True}) as mock_repair:
            res = client.delete(
                f"/api/files/{server_with_dir.id}/delete?path=locked.txt",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert res.status_code == 200
        mock_repair.assert_called_once_with(server_with_dir.install_dir)
        assert attempts == 2
        assert not target.exists()


# ── Search ─────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_finds_files_and_dirs(self, client: TestClient, owner_cookies: dict, server_with_dir: Server):
        root = Path(server_with_dir.install_dir)
        (root / "mods").mkdir()
        (root / "mods" / "ServerConfig.cfg").write_text("x")
        (root / "logs").mkdir()
        (root / "logs" / "server.log").write_text("x")
        res = client.get(
            f"/api/files/{server_with_dir.id}/search?q=server",
            cookies=owner_cookies,
        )
        assert res.status_code == 200
        names = {r["path"] for r in res.json()["results"]}
        # match in Dateinamen (server.log) UND in mods/ServerConfig (Substring "Server").
        assert "logs/server.log" in names
        assert any(p.endswith("ServerConfig.cfg") for p in names)

    def test_search_does_not_leak_outside(self, client: TestClient, owner_cookies: dict, server_with_dir: Server, tmp_path: Path):
        # Datei mit gleichem Suchwort liegt NEBEN dem Server-Root.
        sibling = tmp_path / "sibling_data"
        sibling.mkdir()
        (sibling / "match.bin").write_text("x")
        res = client.get(
            f"/api/files/{server_with_dir.id}/search?q=match",
            cookies=owner_cookies,
        )
        assert res.status_code == 200
        assert res.json()["results"] == []


class TestFileHistoryEndpoints:
    def test_history_list_requires_read_permission(
        self,
        client: TestClient,
        user_cookies: dict,
        server_with_dir: Server,
    ):
        res = client.get(
            f"/api/files/{server_with_dir.id}/versions?path=config.ini",
            cookies=user_cookies,
        )
        assert res.status_code == 403

    def test_restore_requires_csrf(
        self,
        client: TestClient,
        owner_cookies: dict,
        server_with_dir: Server,
    ):
        res = client.post(
            f"/api/files/{server_with_dir.id}/versions/{'a' * 32}/restore?path=config.ini",
            cookies=owner_cookies,
        )
        assert res.status_code == 403

    def test_restore_snapshots_current_content_before_reversible_write(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        server_with_dir: Server,
    ):
        target = Path(server_with_dir.install_dir) / "config.ini"
        target.write_text("current", encoding="utf-8")
        version_id = "b" * 32
        with (
            patch(
                "routers.files.file_history_service.read_version",
                return_value={"id": version_id, "content": "historical"},
            ),
            patch("routers.files.file_history_service.snapshot", return_value=True) as snapshot,
        ):
            res = client.post(
                f"/api/files/{server_with_dir.id}/versions/{version_id}/restore?path=config.ini",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert res.status_code == 200
        assert snapshot.call_count == 1
        assert snapshot.call_args.args[:3] == (server_with_dir.id, "config.ini", "current")
        assert target.read_text(encoding="utf-8") == "historical"


# ── Capability-Flag (system games) ────────────────────────────────────────


class TestCapabilityFlag:
    def test_games_response_exposes_capability(self, client: TestClient, owner_cookies: dict):
        res = client.get("/api/system/games", cookies=owner_cookies)
        assert res.status_code == 200
        games = res.json()
        assert all("supports_steam_workshop" in g for g in games)
        # Conan und DayZ haben Steam Workshop, beide auf True.
        by_id = {g["id"]: g for g in games}
        assert by_id["conan_exiles_ue5"]["supports_steam_workshop"] is True
        assert by_id["dayz"]["supports_steam_workshop"] is True


# ── Tar-Extract ───────────────────────────────────────────────────────────


class TestTarExtract:
    def test_extract_tar_skips_symlinks(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server, tmp_path: Path):
        import tarfile
        archive = Path(server_with_dir.install_dir) / "test.tar.gz"
        with tarfile.open(str(archive), "w:gz") as tf:
            # normal file
            normal = tmp_path / "normal.txt"
            normal.write_text("ok")
            tf.add(str(normal), arcname="normal.txt")
            # symlink
            link = tmp_path / "link"
            link.symlink_to("/etc/passwd")
            tf.add(str(link), arcname="link")
        res = client.post(
            f"/api/files/{server_with_dir.id}/extract?path=test.tar.gz",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "normal.txt").exists()
        assert not (Path(server_with_dir.install_dir) / "link").exists()

    def test_extract_tar_rejects_traversal(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server, tmp_path: Path):
        import tarfile
        archive = Path(server_with_dir.install_dir) / "bad.tar.gz"
        with tarfile.open(str(archive), "w:gz") as tf:
            bad = tmp_path / "evil.txt"
            bad.write_text("bad")
            tf.add(str(bad), arcname="../../evil.txt")
        res = client.post(
            f"/api/files/{server_with_dir.id}/extract?path=bad.tar.gz",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 400
        assert "Zip-Slip" in res.json()["detail"] or "entweichen" in res.json()["detail"]

    def test_extract_tar_small_ok(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server, tmp_path: Path):
        import tarfile
        archive = Path(server_with_dir.install_dir) / "small.tar.gz"
        small = tmp_path / "small.txt"
        small.write_text("hello", encoding="utf-8")
        with tarfile.open(str(archive), "w:gz") as tf:
            tf.add(str(small), arcname="small.txt")
        res = client.post(
            f"/api/files/{server_with_dir.id}/extract?path=small.tar.gz",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "small.txt").read_text() == "hello"

    def test_extract_zip_still_works(self, client: TestClient, owner_cookies: dict, csrf_token: str, server_with_dir: Server, tmp_path: Path):
        import zipfile
        archive = Path(server_with_dir.install_dir) / "test.zip"
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("hello.txt", "world")
        res = client.post(
            f"/api/files/{server_with_dir.id}/extract?path=test.zip",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert (Path(server_with_dir.install_dir) / "hello.txt").read_text() == "world"
