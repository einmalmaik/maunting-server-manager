"""Validated on-node HTTP and GitHub source installation."""

from __future__ import annotations

import hashlib
import base64
import ipaddress
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from services import docker_service, file_service

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024 * 1024


class SourceInstallError(RuntimeError):
    pass


def _validate_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourceInstallError("Only public HTTPS source URLs are allowed")
    for info in socket.getaddrinfo(parsed.hostname, None):
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise SourceInstallError("Source host resolved to a non-public address")


def install_http(
    server_id: str | int,
    *,
    url: str,
    sha256: str | None,
    archive_type: str,
    extract_to: str | None,
) -> dict[str, Any]:
    _validate_public_https(url)
    if archive_type not in {"zip", "tar.gz", "tgz", "tar.xz", "txz", "tar.bz2", "tbz2"}:
        raise SourceInstallError("Unsupported archive type")
    destination = file_service.safe_path(server_id, extract_to or "")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f".msm-source.{archive_type}"
    digest = hashlib.sha256()
    written = 0
    current_url = url
    try:
        timeout = httpx.Timeout(60.0, read=60.0, connect=15.0)
        with httpx.Client(timeout=timeout) as client:
            for _ in range(6):
                _validate_public_https(current_url)
                with client.stream("GET", current_url, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceInstallError("Redirect is missing a target")
                        current_url = location
                        continue
                    if response.status_code != 200:
                        raise SourceInstallError(f"Source download failed with HTTP {response.status_code}")
                    with archive.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            written += len(chunk)
                            if written > MAX_DOWNLOAD_BYTES:
                                raise SourceInstallError("Source archive exceeds maximum size")
                            digest.update(chunk)
                            handle.write(chunk)
                    break
            else:
                raise SourceInstallError("Too many source redirects")
        if sha256 and digest.hexdigest() != sha256:
            raise SourceInstallError("Source archive checksum mismatch")
        relative = archive.relative_to(file_service.server_root(server_id)).as_posix()
        file_service.extract_archive(server_id, relative)
        return {"ok": True}
    finally:
        archive.unlink(missing_ok=True)


def install_github(
    server_id: str | int,
    *,
    repo: str,
    branch: str,
    token: str | None,
    setup_commands: list[list[str]],
    sub_path: str | None,
    runtime_image: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo):
        raise SourceInstallError("Invalid GitHub repository")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", branch):
        raise SourceInstallError("Invalid GitHub branch")
    root = file_service.server_root(server_id)
    root.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        })
    try:
        if (root / ".git").is_dir():
            commands = [
                ["git", "-c", "safe.directory=*", "-C", str(root), "fetch", "origin", branch, "--depth", "1", "--prune"],
                ["git", "-c", "safe.directory=*", "-C", str(root), "reset", "--hard", f"origin/{branch}"],
            ]
        else:
            if any(root.iterdir()):
                raise SourceInstallError("Server directory is not empty and is not a Git repository")
            commands = [["git", "clone", "--branch", branch, "--depth", "1", url, str(root)]]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900, env=env)
            if result.returncode != 0:
                raise SourceInstallError("GitHub source operation failed")
        for command in setup_commands:
            if not command or len(command) > 32:
                raise SourceInstallError("Invalid setup command")
            workdir = "/data" + (f"/{sub_path}" if sub_path else "")
            result = docker_service.run_ephemeral(
                image=runtime_image,
                command=command,
                volumes={str(root): {"bind": "/data", "mode": "rw"}},
                workdir=workdir,
                entrypoint="",
                timeout=1800,
            )
            if not result.get("ok"):
                raise SourceInstallError("Source setup command failed")
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        return {"ok": True, "commit": sha.stdout.strip()[:40], "branch": branch, "repo": repo}
    finally:
        env.pop("GIT_CONFIG_VALUE_0", None)
        basic = "" if token else None
        token = None
