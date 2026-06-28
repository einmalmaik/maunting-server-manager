"""GitHub-Source für ``source.type=github`` (generisch: Bots, Apps, Voice-Tools).

- Nur öffentliche ``github.com``-Repos im Format ``owner/repo``.
- Clone/Pull mit festem Branch (Default ``main``).
- Optionaler ``subPath`` für Monorepos (Arbeitsverzeichnis im Blueprint setzen).
- Optional ``setupCommands``: argv-Listen nach Clone/Pull (npm ci, pip install, …).
- Keine Secrets in Logs; Token nur aus ``MSM_GITHUB_CLONE_TOKEN`` (optional, privat).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .schema import Blueprint, BlueprintSourceType
from services.github_token_service import resolve_token as _resolve_github_token

logger = logging.getLogger(__name__)

_GITHUB_REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,100}/[a-zA-Z0-9_.-]{1,100}$")
_MAX_SETUP_COMMANDS = 8
_MAX_SETUP_ARGS = 32

# npm race condition: TAR_ENTRY_ERROR ENOENT entsteht, wenn parallele Worker
# Dateien in Unterordnern anlegen wollen, bevor das Elternverzeichnis existiert
# (typisch auf overlayfs/rootless Docker). Retry nach Cleanup loest das fast immer.
_NPM_TAR_ENTRY_ERROR_RE = re.compile(
    r"npm\s+warn\s+tar\s+TAR_ENTRY_ERROR\s+ENOENT", re.IGNORECASE
)


class GithubSourceError(RuntimeError):
    pass


def _repo_slug(github_cfg) -> str:
    repo = (github_cfg.repo or "").strip()
    if not _GITHUB_REPO_RE.match(repo):
        raise GithubSourceError(
            "source.github.repo muss 'owner/repo' sein (nur GitHub.com, keine URLs)."
        )
    return repo


def _clone_url(repo: str) -> str:
    token = _resolve_github_token()
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _git_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/false"
    return env


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> None:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GithubSourceError(f"git timeout: {' '.join(args[:4])}…") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # Token niemals leaken
        err = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", err)
        raise GithubSourceError(f"git fehlgeschlagen ({proc.returncode}): {err[:500]}")


def remote_branch_sha(repo: str, branch: str) -> str | None:
    """Liefert Commit-SHA von ``refs/heads/<branch>`` via ls-remote."""
    token = _resolve_github_token()
    if token:
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
    else:
        url = f"https://github.com/{repo}.git"
    ref = f"refs/heads/{branch}"
    try:
        proc = subprocess.run(
            ["git", "ls-remote", url, ref],
            capture_output=True,
            text=True,
            timeout=120,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ref:
            return parts[0]
    return None


def local_repo_sha(install_dir: Path) -> str | None:
    if not (install_dir / ".git").is_dir():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(install_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _is_npm_tar_entry_error(stderr: str, stdout: str, argv0: str) -> bool:
    """Erkennt den npm-Parallel-Extraktions-Race (TAR_ENTRY_ERROR ENOENT).

    Tritt typisch auf overlayfs/rootless Docker auf, wenn Worker Dateien in
    Unterordnern anlegen wollen, bevor das Elternverzeichnis existiert.
    Retry nach Cleanup von ``node_modules`` loest den Fehler praktisch immer.
    """
    if Path(argv0).name not in {"npm", "npx"}:
        return False
    haystack = f"{stderr or ''}\n{stdout or ''}"
    return bool(_NPM_TAR_ENTRY_ERROR_RE.search(haystack))


def _safe_rmtree(path: Path) -> None:
    """Loescht ein Verzeichnis; ignoriert FileNotFoundError."""
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False)


def _run_argv_with_retry(argv: list[str], *, cwd: Path, timeout: int = 900) -> None:
    """Fuehrt argv aus; bei npm-TAR_ENTRY_ERROR wird ``node_modules`` aufgeraeumt
    und der gleiche Befehl genau einmal wiederholt. Andere Fehler werden sofort
    als ``GithubSourceError`` gemeldet."""
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
    )
    if proc.returncode == 0:
        return

    stderr = proc.stderr or ""
    stdout = proc.stdout or ""

    if _is_npm_tar_entry_error(stderr, stdout, argv[0]):
        node_modules = cwd / "node_modules"
        logger.warning(
            "npm TAR_ENTRY_ERROR erkannt in %s, raeume %s auf und retry.",
            cwd,
            node_modules,
        )
        _safe_rmtree(node_modules)
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
        if proc.returncode == 0:
            logger.info("npm-Retry nach TAR_ENTRY_ERROR erfolgreich.")
            return
        stderr = proc.stderr or ""
        stdout = proc.stdout or ""

    raise GithubSourceError(
        f"setupCommand fehlgeschlagen ({argv[0]}): "
        f"{(stderr or stdout or '')[:400]}"
    )


def _run_setup_commands(install_dir: Path, blueprint: Blueprint) -> None:
    gh = blueprint.source.github
    if gh is None:
        return
    commands = gh.setupCommands or []
    root = install_dir
    if gh.subPath:
        root = install_dir / gh.subPath
    if not root.is_dir():
        raise GithubSourceError(f"setupCommands: Verzeichnis fehlt: {root}")
    for argv in commands:
        if not argv or len(argv) > _MAX_SETUP_ARGS:
            raise GithubSourceError("setupCommands: ungültige argv-Liste")
        if any(";" in a or "|" in a or "&" in a for a in argv):
            raise GithubSourceError("setupCommands: Shell-Metazeichen verboten")
        _run_argv_with_retry(argv, cwd=root)


def install_github_source(blueprint: Blueprint, install_dir: str) -> dict:
    """Clone oder Pull (Reinstall/Update) in ``install_dir``."""
    if blueprint.source.type != BlueprintSourceType.GITHUB:
        return {"ok": False, "error": "Keine GitHub-Source"}
    gh = blueprint.source.github
    if gh is None:
        return {"ok": False, "error": "source.github fehlt"}

    repo = _repo_slug(gh)
    branch = (gh.branch or "main").strip() or "main"
    if not re.fullmatch(r"[a-zA-Z0-9._/-]{1,128}", branch):
        return {"ok": False, "error": "Ungültiger Branch-Name"}

    target = Path(install_dir)
    target.mkdir(parents=True, exist_ok=True)
    clone_url = _clone_url(repo)

    has_git = (target / ".git").is_dir()
    try:
        if has_git:
            _run_git(["fetch", "origin", branch, "--depth", "1"], cwd=target)
            _run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=target)
            _run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
        else:
            if any(target.iterdir()):
                raise GithubSourceError(
                    "Installationsverzeichnis nicht leer und kein Git-Repo — "
                    "Reinstall nur in leeres Verzeichnis oder bestehendes Clone."
                )
            _run_git(
                ["clone", "--branch", branch, "--depth", "1", clone_url, str(target)],
                timeout=900,
            )
        _run_setup_commands(target, blueprint)
    except GithubSourceError as exc:
        return {"ok": False, "error": str(exc)}

    sha = local_repo_sha(target)
    return {"ok": True, "commit": sha, "branch": branch, "repo": repo}


def resolve_startup_template(blueprint: Blueprint, host_install_dir: str | None) -> str:
    """Wählt ``runtime.startup`` oder erstes passendes ``startupProfile``."""
    profiles = blueprint.runtime.startupProfiles or []
    if profiles and host_install_dir:
        root = Path(host_install_dir)
        gh = blueprint.source.github
        if gh and gh.subPath:
            root = root / gh.subPath
        for profile in profiles:
            rel = profile.whenFile.strip()
            if not rel:
                continue
            candidate = root / rel
            if candidate.is_file():
                return profile.startup
    return blueprint.runtime.startup