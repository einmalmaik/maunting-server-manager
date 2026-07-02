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


def _create_local_branch_if_missing(cwd: Path, branch: str) -> None:
    """Legt ``refs/heads/<branch>`` auf ``origin/<branch>`` an, falls fehlend.

    Konsequent idempotent: fuehrt ``git branch <branch> origin/<branch>``
    immer aus und schluckt den einzigen benignen Fehlerfall (``fatal: a
    branch named '<branch>' already exists``). Der wird durch eine
    TOCTOU-Race verursacht -- ein paralleler Prozess (Cron, zweiter
    Restart-Versuch, manueller Pull-Request zwischen Existenz-Pruefung
    und ``git branch``) hat den lokalen Branch in der Zwischenzeit selbst
    angelegt. Auf Servern mit mehreren kurz aufeinanderfolgenden
    Restart-Versuchen (z. B. nach Blueprint-Aenderungen) reproduzierbar
    beobachtbar. Alle anderen Fehler (``not a valid object name``,
    fehlgeschlagenes fetch o. ae.) eskalieren weiterhin als
    ``GithubSourceError`` -- der anschliessende ``git reset --hard
    origin/<branch>`` braucht den lokalen Branch zwingend.

    Eine vorgeschaltete ``rev-parse --verify``-Pruefung wurde bewusst
    weggelassen: sie verkuerzt das Race-Window nur, schliesst es aber
    nicht, und der bedingte Pfad produziert dann sowohl die redundante
    Subprocess-Runde als auch den Race-bedingten Fehler im unguenstigsten
    Fall genau dort, wo der Code laenger ist als noetig.
    """
    cmd = ["git", "-C", str(cwd), "branch", branch, f"origin/{branch}"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_ensure_safe_directory(cwd),
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise GithubSourceError(f"git timeout: branch {branch}") from exc
    if proc.returncode == 0:
        return
    err = (proc.stderr or proc.stdout or "").strip()
    err_sanitized = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", err)
    if "already exists" in err_sanitized:
        # TOCTOU-Race gegen einen parallelen Prozess -- genau der einzige
        # Fehlerfall, den wir hier schlucken duerfen, weil der Branch
        # jetzt existiert und der nachfolgende ``reset --hard`` ihn
        # sowieso auf origin/<branch> zwingt.
        logger.info(
            "branch_already_exists (race-tolerant): %s in %s",
            branch, cwd,
        )
        return
    raise GithubSourceError(
        f"git fehlgeschlagen ({proc.returncode}): {err_sanitized[:500]}"
    )


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


# ── Self-healing fuer Dubious-Ownership / verlorene Execute-Bits ─────────────
#
# Hintergrund: Wenn ein ``install_dir`` einem anderen User gehoert als der
# MSM-Prozess (z. B. durch fruehere root-Klon-Operationen oder eine
# Container-Mount-Aktion), scheitern SetupCommands wie ``npm ci`` mit
# EACCES beim Beschreiben von ``node_modules``. Verliert ``start.sh`` sein
# Execute-Bit (etwa weil ``chmod`` aus dem Blueprint nicht durchlaeuft,
# sobald ein frueherer Schritt abbricht), crash't der Container beim Start
# mit "ERR_UNKNOWN_FILE_EXTENSION".
#
# Wir normalisieren hier das ``install_dir`` auf den aktuellen Prozess-User
# und stellen sicher, dass die gaengigen Start-Skripte ausfuehrbar sind.
#
# Eigenschaften:
# - Idempotent: nur Aktionen, wenn noetig (kein chown-churn).
# - Defensiv: ``OperationNotPermitted`` wird geloggt, nicht eskaliert.
# - KISS: kein neuer Manager, keine Subklasse -- reine Helferfunktion.
# - Korrekt unter rootless Docker / MSM-as-msm: nutzt ``os.getuid()/getgid()``.
def _ensure_install_dir_writable(install_dir: Path) -> None:
    import os
    import stat

    if not install_dir.is_dir():
        return

    uid = os.getuid()
    gid = os.getgid()

    # 1. Owner-Normalisierung, aber nur wenn noetig (kein 0/0 selbst wenn
    #    das Verzeichnis bereits 0/0 ist -- das spart I/O auf grossen Trees).
    try:
        st = install_dir.stat()
    except OSError as exc:
        logger.debug("ensure_install_dir_writable: stat failed for %s: %s", install_dir, exc)
        return
    if (st.st_uid, st.st_gid) != (uid, gid):
        try:
            subprocess.run(
                ["chown", "-R", f"{uid}:{gid}", str(install_dir)],
                capture_output=True,
                text=True,
                timeout=600,
                env=_git_env(),
            )
            logger.info(
                "ensure_install_dir_writable: chown -R %d:%d %s (war %d:%d)",
                uid, gid, install_dir, st.st_uid, st.st_gid,
            )
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
            logger.warning(
                "ensure_install_dir_writable: chown fehlgeschlagen auf %s: %s "
                "(SetupCommands werden trotzdem versucht, koennen aber EACCES kriegen)",
                install_dir, exc,
            )

    # 2. Execute-Bit fuer ``*.sh`` direkt unter install_dir (typische
    #    Start-Skripte). Wird ausgefuehrt BEVOR ein spaeterer Blueprint-Schritt
    #    (chmod +x) abbricht und einen Container mit nicht-ausfuehrbarem
    #    Entrypoint-Cmd hinterlaesst.
    try:
        for entry in install_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix != ".sh":
                continue
            try:
                mode = entry.stat().st_mode
                if mode & stat.S_IXUSR:
                    continue
                entry.chmod(0o755)
                logger.info("ensure_install_dir_writable: chmod +x %s", entry)
            except OSError as exc:
                logger.debug("ensure_install_dir_writable: chmod fehlgeschlagen auf %s: %s", entry, exc)
    except OSError as exc:
        logger.debug("ensure_install_dir_writable: iterdir fehlgeschlagen auf %s: %s", install_dir, exc)


def _best_effort_restore_node_modules(install_dir: Path) -> None:
    """Bereinigt ``node_modules`` als Vorbereitung fuer ``npm ci``.

    Hintergrund: Wenn das Repo vorher einem anderen User gehoert hat und
    der Self-healing-Chown in ``_ensure_install_dir_writable`` fehl-
    schlaegt (z. B. weil der MSM-Prozess keine CAP_CHOWN hat), bleiben
    einige ``node_modules/*``-Subverzeichnisse unbesitzbar. ``npm ci``
    bricht dann beim ``rmdir @alloc/quick-lru`` mit EACCES ab. Indem wir
    das gesamte ``node_modules``-Verzeichnis einmal entfernen -- sofern
    der MSM-Prozess ueberhaupt Schreibrechte hat -- startet ``npm ci``
    bei Null und ohne Stolperfallen.

    Defensiv: Permission-Fehler werden geloggt, nicht eskaliert. Wenn
    ``_ensure_install_dir_writable`` erfolgreich war (MSM ist bereits
    Eigentuemer), bleibt der Schritt ein no-op (shutil.rmtree ist schnell).
    """
    target = install_dir / "node_modules"
    if not target.exists():
        return
    try:
        # shutil.rmtree ist auf C-Ebene schnell, vermeidet aber Subprozesse.
        # Wir umgehen damit auch 'rm -rf'-Probleme mit Mount-Flags.
        shutil.rmtree(target, ignore_errors=False)
        logger.info("best_effort_restore_node_modules: %s entfernt", target)
    except OSError as exc:
        logger.warning(
            "best_effort_restore_node_modules: konnte %s nicht entfernen: %s "
            "(npm ci wird trotzdem versucht; ggf. manuelles chown noetig)",
            target, exc,
        )


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
            # Frischer HEAD -- erst fetch (origin/<branch> aktualisieren),
            # dann Working Tree exakt auf das entfernte HEAD zwingen.
            #
            # Frueher stand hier ``git checkout -B <branch> origin/<branch>``
            # VOR dem ``git reset --hard``. Das war ein Bug: ``git checkout``
            # bricht ab, sobald der Working Tree lokale Mutationen hat
            # (typisch nach SetupCommands wie ``npm ci``, die Files anlegen
            # oder wenn ein Admin-User per Panel-Console manuell editiert
            # hat). Der Checkout-Fehler eskalierte als ``GithubSourceError``,
            # BEVOR das nachfolgende ``git reset --hard`` lief -- mit dem
            # Effekt, dass ``origin/<branch>`` zwar auf den neuen SHA
            # upgedated wurde, der Working-Tree aber auf dem alten Commit
            # stehen blieb. Genau das war der "pullt die neuste Version
            # nicht"-Bug (z. B. auf Singra-Discord-bot: HEAD blieb auf
            # PR #15 statt auf den frischen PR #17 zu springen).
            #
            # Loesung: ``checkout -B`` weg, da redundant. ``reset --hard``
            # setzt HEAD und Working Tree atomar auf ``origin/<branch>`` und
            # ueberschreibt dabei Working-Tree-Mutationen ohne Schutz --
            # exakt was wir wollen.
            #
            # Frueher gab es hier zusaetzlich ein ``show-ref --verify`` +
            # konditional ``git branch <name> origin/<name>``. Das wurde
            # mittlerweile entfernt, weil der Branch-Befehl ohnehin idempotent
            # den Exit-Code 128 mit "fatal: a branch named '<branch>' already
            # exists" liefern kann, sobald zwischen der Existenz-Pruefung
            # und dem Branch-Befehl ein externer Prozess (Cron, paralleler
            # Restart, manueller Pull-Request) denselben Branch angelegt
            # hat. Das Race-Window ist klein, aber auf Servern mit
            # mehreren kurz aufeinanderfolgenden Restart-Versuchen (z. B.
            # nach Blueprint-Aenderungen) reproduzierbar beobachtbar.
            #
            # Stattdessen: Branch IMMER anlegen und den "already exists"-
            # Fehler schlucken -- alles andere macht der nachfolgende
            # ``reset --hard``. ``git branch -f`` waere keine Alternative
            # (scheitert auf Git >=2.40 mit "cannot force update the
            # branch ... used by worktree", sobald der Branch bereits
            # der currently-checked-out-Branch irgendeiner Worktree ist).
            _run_git(["fetch", "origin", branch, "--depth", "1", "--prune"], cwd=target)
            _create_local_branch_if_missing(target, branch)
            _run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
            # Belt-and-suspenders: nochmaliger reset --hard. Falls zwischen den
            # Befehlen ein externer Prozess (cron, anderes Skript) Commits macht
            # oder Working-Tree-Files anlegt, wird der zweite reset das wieder
            # einfangen. Idempotent und billig (ein no-op, wenn nichts passiert ist).
            _run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
            # Falls das Repo Submodule hat: ebenfalls auf Origin-SHA syncen.
            # Wir nutzen ``--init --recursive --force``, damit sowohl fehlende
            # Submodule initialisiert als auch lokale Aenderungen ueberschrieben
            # werden. Bei Repos ohne .gitmodules ein no-op.
            try:
                if (target / ".gitmodules").is_file():
                    _run_git(
                        ["submodule", "update", "--init", "--recursive", "--force"],
                        cwd=target,
                    )
            except GithubSourceError:
                # Submodule-Sync darf den gesamten Pull nicht blockieren --
                # Repos ohne .gitmodules oder mit Lock-Problemen ueberspringen.
                logger.warning(
                    "GitHub-Source %s@%s: Submodule-Update fehlgeschlagen, "
                    "weitere Schritte laufen trotzdem.", repo, branch,
                )
            # Head-Verifikation: Working Tree muss remote-HEAD entsprechen.
            # Falls ein externer Prozess (z. B. paralleler Probe-Aufruf, ein
            # Cronjob, ein anderes Admin-Skript) zwischen den obigen git-Befehlen
            # und hier etwas am Tree geaendert hat, faellt das hier auf und wir
            # brechen mit klarer Diagnose ab -- statt stillschweigend einen
            # gemischten Stand zu bauen (das war der konkrete Bug, der zu
            # dem inkohaerenten Working-Tree-Image gefuehrt hat).
            verify_proc = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "HEAD"],
                capture_output=True, text=True, env=_git_env(),
            )
            actual_head = (verify_proc.stdout or "").strip()
            # Aktuellen Origin-HEAD nochmal frisch abfragen, damit auch ein
            # race-freier Vergleich moeglich ist (origin/<branch> koennte
            # seit dem reset --hard nochmal nachgewandert sein).
            origin_sha_proc = subprocess.run(
                ["git", "-C", str(target), "rev-parse", f"origin/{branch}"],
                capture_output=True, text=True, env=_git_env(),
            )
            expected_head = (origin_sha_proc.stdout or "").strip()
            if expected_head and actual_head != expected_head:
                raise GithubSourceError(
                    f"Working-Tree-HEAD weicht von origin/{branch} ab "
                    f"(got {actual_head[:12]}, expected {expected_head[:12]}). "
                    f"Ein externer Prozess hat den Tree waehrend des Pulls "
                    f"geaendert. Bitte manuell bereinigen."
                )
            logger.info(
                "GitHub-Source: Pull-Check OK -- branch=%s head=%s",
                branch, actual_head[:12],
            )
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
        # Self-healing: Dubious-Ownership + verlorene Execute-Bits normalisieren
        # BEVOR SetupCommands (npm ci / build) laufen. Siehe _ensure_install_dir_writable.
        _ensure_install_dir_writable(target)
        # Workaround: wenn der chown oben mangels Capability fehlgeschlagen ist
        # (z. B. MSM-as-msm auf root-owned FS), kann ``npm ci`` mit EACCES
        # abbrechen, weil ``node_modules``-Subverzeichnisse dem falschen User
        # gehoeren. Wir versuchen daher, ``node_modules`` einmalig wegzuräumen,
        # sodass ``npm ci`` von Null aus installiert. Schlägt auch das fehl
        # (z. B. weil der MSM-Prozess ueberhaupt kein Schreibrecht hat),
        # gehen wir weiter und melden den Fehler klar ueber Logger -- die
        # spaetere ``npm ci``-Fehlermeldung bleibt praezise.
        _best_effort_restore_node_modules(target)
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