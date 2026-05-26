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
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .archive_extract import ArchiveExtractError, safe_extract_archive
from .schema import Blueprint, BlueprintArchiveType, BlueprintSourceType


logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB
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

    extract_to = install_path / http.extractTo if http.extractTo else install_path
    try:
        safe_extract_archive(archive_path, extract_to, install_path)
    except (HttpSourceError, ArchiveExtractError) as exc:
        return {"ok": False, "error": f"Entpacken fehlgeschlagen: {exc}"}
    finally:
        archive_path.unlink(missing_ok=True)

    return {"ok": True}
