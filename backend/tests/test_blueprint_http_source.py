"""Security-Tests fuer den HTTP-Source-Downloader.

Wir testen die statischen Schutz-Pfade (URL-Validierung, SSRF-Schutz, Pfad-
Resolution) ohne echten Netz-Traffic. Vollstaendiger E2E-Test mit Mock-Server
wird im Zuge eines kuenftigen Integration-Test-Setups ergaenzt.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from blueprints.archive_extract import (
    ArchiveExtractError,
    _extract_tar,
    _extract_zip,
    _resolve_into,
)
from blueprints.http_source import (
    HttpSourceError,
    _ensure_public_host,
    _ensure_safe_https_url,
)


def test_ensure_https_rejects_http() -> None:
    with pytest.raises(HttpSourceError):
        _ensure_safe_https_url("http://example.org/x.zip")


def test_ensure_https_accepts_https() -> None:
    host, port = _ensure_safe_https_url("https://example.org/x.zip")
    assert host == "example.org"
    assert port == 443


def test_ensure_https_rejects_no_host() -> None:
    with pytest.raises(HttpSourceError):
        _ensure_safe_https_url("https:///x.zip")


def test_ensure_public_host_rejects_loopback() -> None:
    with pytest.raises(HttpSourceError):
        _ensure_public_host("127.0.0.1")


def test_ensure_public_host_rejects_private() -> None:
    with pytest.raises(HttpSourceError):
        _ensure_public_host("10.0.0.1")


def test_ensure_public_host_rejects_link_local() -> None:
    with pytest.raises(HttpSourceError):
        _ensure_public_host("169.254.1.1")


def test_resolve_into_rejects_absolute_member(tmp_path: Path) -> None:
    with pytest.raises(ArchiveExtractError):
        _resolve_into(tmp_path, "/etc/passwd", None)


def test_resolve_into_rejects_dotdot(tmp_path: Path) -> None:
    with pytest.raises(ArchiveExtractError):
        _resolve_into(tmp_path, "../escape", None)


def test_resolve_into_happy(tmp_path: Path) -> None:
    target = _resolve_into(tmp_path, "sub/file.txt", None)
    assert tmp_path in target.parents or target.parent == tmp_path or str(target).startswith(str(tmp_path))


def test_extract_zip_skips_symlink(tmp_path: Path) -> None:
    """Eine zip-Datei mit Symlink-Mode soll uebersprungen werden."""
    archive = tmp_path / "evil.zip"
    target = tmp_path / "extract"
    target.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("normal.txt", "ok")
        info = zipfile.ZipInfo("evil_link")
        # UNIX S_IFLNK = 0o120000, shifted into the external_attr.
        info.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(info, "/etc/passwd")

    with zipfile.ZipFile(archive, "r") as zf:
        _extract_zip(zf, target, None)

    assert (target / "normal.txt").read_text() == "ok"
    assert not (target / "evil_link").exists()


def test_extract_zip_rejects_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "slip.zip"
    target = tmp_path / "extract"
    target.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../../etc/passwd", "haxx")

    with zipfile.ZipFile(archive, "r") as zf:
        with pytest.raises(ArchiveExtractError):
            _extract_zip(zf, target, None)


def test_extract_tar_skips_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar"
    target = tmp_path / "extract"
    target.mkdir()

    # Tar mit Symlink-Member
    with tarfile.open(archive, "w") as tf:
        normal = tarfile.TarInfo(name="normal.txt")
        normal.size = 2
        tf.addfile(normal, BytesIO(b"ok"))
        link_info = tarfile.TarInfo(name="evil_link")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        tf.addfile(link_info)

    with tarfile.open(archive, "r") as tf:
        _extract_tar(tf, target, None)

    assert (target / "normal.txt").read_text() == "ok"
    assert not (target / "evil_link").exists()


def test_extract_tar_skips_hardlink(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar"
    target = tmp_path / "extract"
    target.mkdir()

    with tarfile.open(archive, "w") as tf:
        normal = tarfile.TarInfo(name="normal.txt")
        normal.size = 2
        tf.addfile(normal, BytesIO(b"ok"))
        info = tarfile.TarInfo(name="evil_hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "anything"
        tf.addfile(info)

    with tarfile.open(archive, "r") as tf:
        _extract_tar(tf, target, None)

    assert (target / "normal.txt").read_text() == "ok"
    assert not (target / "evil_hard").exists()


def test_extract_zip_happy_path(tmp_path: Path) -> None:
    archive = tmp_path / "good.zip"
    target = tmp_path / "extract"
    target.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("hello.txt", "world")
        zf.writestr("sub/inner.txt", "inner")

    with zipfile.ZipFile(archive, "r") as zf:
        _extract_zip(zf, target, None)

    assert (target / "hello.txt").read_text() == "world"
    assert (target / "sub" / "inner.txt").read_text() == "inner"
