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
import time
from pathlib import Path

from .schema import Blueprint, BlueprintSourceType
from services.github_token_service import resolve_token as _resolve_github_token

logger = logging.getLogger(__name__)

_GITHUB_REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,100}/[a-zA-Z0-9_.-]{1,100}$")
_MAX_SETUP_COMMANDS = 8
_MAX_SETUP_ARGS = 32

# npm race condition: TAR_ENTRY_ERROR ENOENT entsteht, wenn parallele Worker
# Dateien in Unterordnern anlegen wollen, bevor das Elternverzeichnis existiert
# (typisch auf overlayfs/rootless Docker). Retry nach Cleanup loest das fast immer,
# aber nur wenn der zweite Lauf single-threaded laeuft (--network-concurrency=1).
_NPM_TAR_ENTRY_ERROR_RE = re.compile(
    r"npm\s+warn\s+tar\s+TAR_ENTRY_ERROR\s+ENOENT", re.IGNORECASE
)

# Maximale Anzahl Retries bei TAR_ENTRY_ERROR (insgesamt 4 Laeufe: 1 + 3 Retries).
# Backoff in Sekunden zwischen den Versuchen, damit andere Worker/Caches sich beruhigen.
_NPM_TAR_RETRY_MAX = 3
_NPM_TAR_RETRY_BACKOFF = (5, 15, 30)

# npm-Argumente, die wir automatisch beim ersten Lauf ergaenzen, um parallele
# Downloads zu reduzieren. Sie werden VOR argv eingefuegt und ersetzen/ueberschreiben
# bestehende gleichnamige Flags NICHT (nur defaults).
_NPM_STABILIZE_FLAGS = ("--no-audit", "--no-fund", "--prefer-offline")
_NPM_STABILIZE_FORCE = ("--no-audit", "--no-fund", "--network-concurrency=1")


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
    # Git-Dubious-Ownership-Schutz pro Aufruf ausschalten: ``safe.directory=*``
    # via ``-c`` an git reicht als In-Process-Konfig, schreibt keinen State
    # und ueberlebt Cron/Container-Restarts. Wird ergaenzt in ``_run_git``,
    # sobald ``cwd`` gesetzt ist (siehe dort).
    return env


def _ensure_safe_directory(cwd: Path | None) -> dict[str, str]:
    """Bereitet die Env-Variablen vor, um Git-Dubious-Ownership zu umgehen.

    Hintergrund: Git verweigert seit 2.35.2 ``fetch/reset`` in Repos, deren
    Owner nicht der aktuelle Prozess-User ist ("detected dubious ownership").
    MSM laeuft als gleichbleibender System-User (``msm``), aber ``install_dir``
    kann z. B. durch fruehere root- oder Docker-Mounts einem anderen UID/GID
    gehoeren. Per-repo ``safe.directory`` in ``.git/config`` wird von Git
    erst NACH der Sicherheits-Pruefung gelesen, hilft also nicht.

    Loesung: Wir setzen ``GIT_CONFIG_COUNT`` + ``GIT_CONFIG_KEY_0`` und
    ``GIT_CONFIG_VALUE_0`` als Prozess-Env. Git wendet diese In-Memory-Konfig
    vor der Sicherheits-Pruefung an. Kein dauerhafter State, idempotent,
    robust gegen Cron-Jobs/Container-Restarts/Panel-Updates.

    Optional schreiben wir zusaetzlich einen per-repo Eintrag -- der hilft
    externen Tools (z. B. manuelles ``git fetch``), bleibt aber wirkungslos
    fuer MSM ohne diesen Env-Trick (siehe oben). Die Schreibung schlaegt
    stillschweigend fehl, wenn der Owner-Mismatch beim Schreiben selbst
    blockiert; das ist okay.

    Rueckgabe: das angereicherte Env-Dict (callable-sicher, kein Default-``os.environ``).
    """
    base = _git_env()
    if cwd is None:
        return base
    # In-Memory-Config: Git liest das VOR der dubious-ownership-Pruefung.
    # GIT_CONFIG_COUNT=1 + die zwei benannten Variablen ist die offizielle
    # Schnittstelle dafuer (siehe ``git help config``).
    base["GIT_CONFIG_COUNT"] = "1"
    base["GIT_CONFIG_KEY_0"] = "safe.directory"
    base["GIT_CONFIG_VALUE_0"] = "*"

    # Per-Repo-Eintrag (robust gegen manuelle CLI-Aufrufe, optional).
    # ``-c safe.directory=*`` ist noetig, weil sonst der Schreib-Befehl selbst
    # am Ownership-Mismatch scheitert. ``capture_output=True``+``check=False``
    # damit Fehler hier nicht eskalieren -- _run_git meldet den eigentlichen
    # Fetch-Fehler.
    try:
        subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(cwd),
             "config", "--local", "safe.directory", str(cwd)],
            capture_output=True,
            text=True,
            timeout=10,
            env=base,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
    return base


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> None:
    cmd = ["git", *args]
    env = _ensure_safe_directory(cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
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
    # ``ls-remote`` braucht keinen ``safe.directory``-Trick (kein lokales Repo),
    # aber wir nutzen den Standard-Env fuer einheitliche Timeouts/Prompts.
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
    env = _ensure_safe_directory(install_dir)
    try:
        proc = subprocess.run(
            ["git", "-C", str(install_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
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
    Wird durch ``--network-concurrency=1`` + Cleanup praktisch immer geloest.
    """
    if Path(argv0).name not in {"npm", "npx"}:
        return False
    haystack = f"{stderr or ''}\n{stdout or ''}"
    return bool(_NPM_TAR_ENTRY_ERROR_RE.search(haystack))


def _is_npm_invocation(argv: list[str]) -> bool:
    """True, wenn argv ein npm/npx-Top-Level-Befehl ist (nicht Subcommand wie ``npm run build``)."""
    return len(argv) >= 2 and Path(argv[0]).name in {"npm", "npx"}


def _is_npm_install(argv: list[str]) -> bool:
    """True, wenn argv einer der Befehle ist, der den pacote-Race ausloest.

    ``npm ci``, ``npm i``, ``npm install`` und ``npx <pkg>`` (beim ersten Lauf
    mit fehlendem Cache) koennen TAR_ENTRY_ERROR werfen. ``npm run ...`` nicht.

    Flags (``--foo`` / ``--foo=bar``) werden uebersprungen, damit die Erkennung
    auch dann greift, wenn wir selbst bereits Flags injiziert haben.
    """
    if not _is_npm_invocation(argv):
        return False
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return arg in {"ci", "i", "install", "add", "update", "upgrade"}
    return False


def _inject_npm_flags(argv: list[str], flags: tuple[str, ...]) -> list[str]:
    """Fuegt flags ZWISCHEN argv[0] und argv[1] ein, ohne bestehende Flags zu
    duplizieren.

    Beispiel: argv=['npm','ci'], flags=('--no-audit','--no-fund')
      → ['npm','--no-audit','--no-fund','ci']
    """
    if len(argv) < 2:
        return list(argv)
    out = [argv[0]]
    existing = {a.split("=", 1)[0] for a in argv[1:] if a.startswith("--")}
    for f in flags:
        if f.split("=", 1)[0] not in existing:
            out.append(f)
    out.extend(argv[1:])
    return out


def _safe_rmtree(path: Path) -> None:
    """Loescht ein Verzeichnis; ignoriert FileNotFoundError."""
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False)


def _run_argv_with_retry(argv: list[str], *, cwd: Path, timeout: int = 900) -> None:
    """Fuehrt argv aus.

    - Bei ``npm ci``/``npm install``/``npx <pkg>``: ergaenzt automatisch
      ``--no-audit --no-fund --prefer-offline`` und (bei Retries)
      ``--network-concurrency=1``, um den pacote-Race auf overlayfs zu
      entschaerfen.
    - Bei TAR_ENTRY_ERROR: cleant ``cwd/node_modules``, wartet kurz
      (5/15/30s Backoff) und probiert bis zu ``_NPM_TAR_RETRY_MAX`` mal.
    - Andere Fehler werden sofort als ``GithubSourceError`` gemeldet.
    """
    current_argv: list[str] = list(argv)
    if _is_npm_install(current_argv):
        current_argv = _inject_npm_flags(current_argv, _NPM_STABILIZE_FLAGS)

    last_stderr = ""
    last_stdout = ""

    for attempt in range(_NPM_TAR_RETRY_MAX + 1):
        proc = subprocess.run(
            current_argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            if attempt > 0:
                logger.info(
                    "npm-Retry %d/%d nach TAR_ENTRY_ERROR erfolgreich.",
                    attempt,
                    _NPM_TAR_RETRY_MAX,
                )
            return

        last_stderr = proc.stderr or ""
        last_stdout = proc.stdout or ""

        if attempt >= _NPM_TAR_RETRY_MAX:
            break
        if not _is_npm_tar_entry_error(last_stderr, last_stdout, current_argv[0]):
            break

        backoff = _NPM_TAR_RETRY_BACKOFF[attempt]
        node_modules = cwd / "node_modules"
        logger.warning(
            "npm TAR_ENTRY_ERROR erkannt in %s (Versuch %d/%d), "
            "raeume %s auf, warte %ds und retry mit --network-concurrency=1.",
            cwd,
            attempt + 1,
            _NPM_TAR_RETRY_MAX + 1,
            node_modules,
            backoff,
        )
        _safe_rmtree(node_modules)
        time.sleep(backoff)
        # Vor dem naechsten Lauf: --network-concurrency=1 injizieren, um den
        # pacote-Race zu entschaerfen (single-threaded downloads/extraktion).
        if _is_npm_install(current_argv):
            current_argv = _inject_npm_flags(current_argv, _NPM_STABILIZE_FORCE)

    raise GithubSourceError(
        f"setupCommand fehlgeschlagen ({argv[0]}): "
        f"{(last_stderr or last_stdout or '')[:400]}"
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