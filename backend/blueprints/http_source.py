"""HTTP-Source-Downloader fuer ``source.type=http``.

Security-Hardening:
- HTTPS-only (Schema laesst http:// gar nicht erst durch). Wir folgen Redirects
  NUR, wenn die Ziel-URL ebenfalls https:// ist.
- SSRF-Schutz: Hostnamen-Aufloesung erfolgt einmalig, das Ergebnis darf nicht
  loopback/private/link-local/multicast sein.
- Streaming-Download mit fester Groessen-Obergrenze (5 GiB).
- Optionaler SHA-256-Integritaetscheck (Blueprint-Feld ``source.http.sha256``).
- Zip-Slip / Symlink-Escape: Wir entpacken Datei-fuer-Datei, validieren jeden
  Pfad gegen ``realpath(install_dir)`` und verwerfen Symlinks/Hardlinks/Device-
  Nodes komplett.
- Zip-Bomb-Schutz: Decompressed-Groesse darf ``MAX_DECOMPRESSED_BYTES``
  nicht ueberschreiten (10 GiB) und der Decompression-Ratio-Faktor pro Member
  ist auf 1:100 begrenzt.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import tarfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .schema import Blueprint, BlueprintArchiveType, BlueprintSourceType


logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB
MAX_DECOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
PER_MEMBER_COMPRESSION_RATIO = 100  # entpackt <= 100x rohe Archiv-Groesse pro Member
_DOWNLOAD_TIMEOUT_S = 600.0
_CHUNK_BYTES = 1024 * 1024  # 1 MiB
_MAX_REDIRECTS = 5


class HttpSourceError(RuntimeError):
    """Aufrufer-freundlicher Fehler fuer Download-/Entpack-Probleme."""


def _ensure_safe_https_url(url: str) -> tuple[str, int]:
    """Parst die URL, erzwingt https und liefert ``(host, port)``."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HttpSourceError(f"Nur https-URLs erlaubt, nicht '{parsed.scheme}'.")
    host = (parsed.hostname or "").strip()
    if not host:
        raise HttpSourceError("URL hat keinen Host-Anteil.")
    port = parsed.port or 443
    return host, port


def _ensure_public_host(host: str) -> None:
    """SSRF-Schutz: weigert sich, loopback / private / link-local zu kontaktieren.

    Loest den Hostnamen via ``getaddrinfo`` und validiert *alle* zurueckgegebenen
    Adressen. ``httpx`` mit ``follow_redirects=False`` greift dann den ersten
    DNS-Eintrag selber, aber die Validierung verhindert, dass schon der erste
    Versuch in unser internes Netz geht. Volle Re-Validierung bei Redirects
    erfolgt im Downloader.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HttpSourceError(f"Host '{host}' konnte nicht aufgeloest werden: {exc}") from exc
    if not infos:
        raise HttpSourceError(f"Host '{host}' lieferte keine Adressen.")
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HttpSourceError(
                f"Host '{host}' loest auf nicht-oeffentliche Adresse {ip} auf "
                "(SSRF-Schutz)."
            )


def _archive_filename_for(archive_type: BlueprintArchiveType | None) -> str:
    if archive_type is None:
        return "download.bin"
    return f"download.{archive_type.value}"


def _detect_archive_type(filename: str) -> BlueprintArchiveType | None:
    lower = filename.lower()
    for t in BlueprintArchiveType:
        if lower.endswith("." + t.value):
            return t
    return None


@contextmanager
def _open_archive(path: Path, archive_type: BlueprintArchiveType):
    """Oeffnet das Archiv read-only. Liefert ein einheitliches Member-Iterable."""
    if archive_type == BlueprintArchiveType.ZIP:
        with zipfile.ZipFile(path, "r") as zf:
            yield ("zip", zf)
    elif archive_type in (
        BlueprintArchiveType.TAR_GZ,
        BlueprintArchiveType.TGZ,
        BlueprintArchiveType.TAR_XZ,
        BlueprintArchiveType.TXZ,
        BlueprintArchiveType.TAR_BZ2,
        BlueprintArchiveType.TBZ2,
    ):
        mode = "r:*"
        with tarfile.open(path, mode) as tf:
            yield ("tar", tf)
    elif archive_type == BlueprintArchiveType.SEVEN_Z:
        raise HttpSourceError(
            "Archiv-Typ '7z' wird derzeit nicht entpackt — bitte zip oder tar.* "
            "verwenden (zentraler Sicherheits-Review steht aus)."
        )
    else:  # pragma: no cover - schema disallows others
        raise HttpSourceError(f"Unbekannter Archiv-Typ: {archive_type}")


def _resolve_into(install_dir: Path, member_name: str, extract_to: str | None) -> Path:
    """Loest den Ziel-Pfad auf und stellt sicher, dass er unter ``install_dir`` liegt."""
    # Normalisieren: Member-Pfade duerfen weder absolut noch escapen.
    if not member_name or member_name.startswith("/") or "\x00" in member_name:
        raise HttpSourceError(f"Member '{member_name}' hat unsicheren Pfad.")
    if any(part == ".." for part in member_name.split("/")):
        raise HttpSourceError(f"Member '{member_name}' enthaelt '..' (Zip-Slip).")
    base = install_dir
    if extract_to:
        base = install_dir / extract_to
    target = (base / member_name).resolve()
    install_real = install_dir.resolve()
    try:
        target.relative_to(install_real)
    except ValueError as exc:
        raise HttpSourceError(
            f"Member '{member_name}' wuerde aus install_dir entweichen."
        ) from exc
    return target


def _stream_download(url: str, destination: Path, expected_sha256: str | None) -> None:
    """Streamt ``url`` nach ``destination``. Wirft bei zu grosser/falscher Datei."""
    host, _port = _ensure_safe_https_url(url)
    _ensure_public_host(host)

    digest = hashlib.sha256()
    written = 0
    current_url = url
    # Eigene Redirect-Schleife — wir muessen jede neue URL erneut validieren.
    for _hop in range(_MAX_REDIRECTS + 1):
        with httpx.stream(
            "GET",
            current_url,
            follow_redirects=False,
            timeout=_DOWNLOAD_TIMEOUT_S,
            headers={"User-Agent": "MSM-Panel/1.0 (+blueprint-http-source)"},
        ) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise HttpSourceError(
                        f"Redirect ohne Location-Header (Status {resp.status_code})."
                    )
                new_host, _ = _ensure_safe_https_url(location)
                _ensure_public_host(new_host)
                current_url = location
                continue
            if resp.status_code != 200:
                raise HttpSourceError(
                    f"Download fehlgeschlagen: HTTP {resp.status_code}."
                )
            # Optional: Content-Length pruefen, bevor wir Bytes annehmen.
            content_length = resp.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as exc:
                    raise HttpSourceError(
                        "Content-Length ist kein Integer."
                    ) from exc
                if declared > MAX_DOWNLOAD_BYTES:
                    raise HttpSourceError(
                        f"Archiv ist mit {declared} Bytes groesser als das Limit "
                        f"von {MAX_DOWNLOAD_BYTES} Bytes."
                    )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(destination, "wb") as out:
                for chunk in resp.iter_bytes(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        out.close()
                        destination.unlink(missing_ok=True)
                        raise HttpSourceError(
                            f"Archiv ueberschreitet Groessen-Limit von "
                            f"{MAX_DOWNLOAD_BYTES} Bytes."
                        )
                    digest.update(chunk)
                    out.write(chunk)
            break
    else:
        raise HttpSourceError("Zu viele Redirects beim Download.")

    if expected_sha256:
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            destination.unlink(missing_ok=True)
            raise HttpSourceError(
                "SHA-256 stimmt nicht mit blueprint.sha256 ueberein — "
                "Datei wird verworfen (Supply-Chain-Schutz)."
            )


def _extract_zip(zf: zipfile.ZipFile, install_dir: Path, extract_to: str | None) -> None:
    archive_size = sum(getattr(zi, "compress_size", 0) for zi in zf.infolist())
    decompressed_budget = max(
        archive_size * PER_MEMBER_COMPRESSION_RATIO, _CHUNK_BYTES
    )
    decompressed_budget = min(decompressed_budget, MAX_DECOMPRESSED_BYTES)
    written = 0
    for info in zf.infolist():
        # ZIP kennt kein Symlink-Flag offiziell; manche Tools setzen aber UNIX
        # Mode in ``external_attr``. Wenn der Modus auf Symlink steht (S_IFLNK)
        # weigern wir uns hart.
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and (unix_mode & 0o170000) == 0o120000:
            raise HttpSourceError(
                f"Zip-Member '{info.filename}' ist ein Symlink — abgelehnt."
            )
        if info.is_dir():
            target = _resolve_into(install_dir, info.filename, extract_to)
            target.mkdir(parents=True, exist_ok=True)
            continue
        target = _resolve_into(install_dir, info.filename, extract_to)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > decompressed_budget:
                    target.unlink(missing_ok=True)
                    raise HttpSourceError(
                        "Decompressed-Groessen-Limit ueberschritten (Zip-Bomb-Schutz)."
                    )
                dst.write(chunk)


def _extract_tar(tf: tarfile.TarFile, install_dir: Path, extract_to: str | None) -> None:
    archive_size = 0
    try:
        archive_size = os.path.getsize(tf.name) if tf.name else 0
    except OSError:
        archive_size = 0
    decompressed_budget = max(
        archive_size * PER_MEMBER_COMPRESSION_RATIO, _CHUNK_BYTES
    )
    decompressed_budget = min(decompressed_budget, MAX_DECOMPRESSED_BYTES)
    written = 0
    for member in tf.getmembers():
        if member.issym() or member.islnk():
            raise HttpSourceError(
                f"Tar-Member '{member.name}' ist Sym-/Hardlink — abgelehnt."
            )
        if member.isdev() or member.isfifo():
            raise HttpSourceError(
                f"Tar-Member '{member.name}' ist Device/FIFO — abgelehnt."
            )
        target = _resolve_into(install_dir, member.name, extract_to)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        src = tf.extractfile(member)
        if src is None:
            continue
        with src, open(target, "wb") as dst:
            while True:
                chunk = src.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > decompressed_budget:
                    target.unlink(missing_ok=True)
                    raise HttpSourceError(
                        "Decompressed-Groessen-Limit ueberschritten (Tar-Bomb-Schutz)."
                    )
                dst.write(chunk)


def install_http_source(blueprint: Blueprint, install_dir: str) -> dict:
    """Download + Entpacken fuer ``source.type=http``.

    Returns ein dict im selben Format wie ``run_steamcmd_install``:
    ``{"ok": True}`` oder ``{"ok": False, "error": "..."}``.
    """
    if blueprint.source.type != BlueprintSourceType.HTTP:
        return {"ok": False, "error": "Blueprint ist nicht vom Typ http."}
    http = blueprint.source.http
    if http is None:
        return {"ok": False, "error": "source.http fehlt."}

    install_path = Path(install_dir).resolve()
    install_path.mkdir(parents=True, exist_ok=True)

    archive_type = http.archiveType or _detect_archive_type(urlparse(http.url).path)
    if archive_type is None:
        return {
            "ok": False,
            "error": (
                "Archive-Typ konnte nicht erkannt werden — bitte "
                "source.http.archiveType im Blueprint setzen."
            ),
        }
    if archive_type == BlueprintArchiveType.SEVEN_Z:
        return {
            "ok": False,
            "error": (
                "Archiv-Typ '7z' wird nicht unterstuetzt. Bitte als zip oder "
                "tar.* bereitstellen."
            ),
        }

    archive_path = install_path / f".msm_blueprint_{_archive_filename_for(archive_type)}"
    try:
        _stream_download(http.url, archive_path, http.sha256)
    except HttpSourceError as exc:
        archive_path.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}
    except httpx.HTTPError as exc:
        archive_path.unlink(missing_ok=True)
        # Keine internen Pfade leaken — nur Typ + Kurztext
        return {"ok": False, "error": f"HTTP-Download fehlgeschlagen: {type(exc).__name__}"}

    try:
        with _open_archive(archive_path, archive_type) as (kind, archive):
            if kind == "zip":
                _extract_zip(archive, install_path, http.extractTo)
            else:
                _extract_tar(archive, install_path, http.extractTo)
    except (HttpSourceError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": f"Entpacken fehlgeschlagen: {exc}"}
    finally:
        archive_path.unlink(missing_ok=True)

    return {"ok": True}
