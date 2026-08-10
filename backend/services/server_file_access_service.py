"""Gemeinsamer, revisionssicherer Textdateizugriff fuer Panel und AI."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server, User
from services import docker_service, file_edit_service, file_history_service
from services.dis_client import DisSidecarError
from services.file_edit_service import FileRevisionConflict
from services.node_client import NodeClient, NodeClientError
from services.node_service import resolve_server_node


MAX_EDIT_SIZE = 5 * 1024 * 1024


def safe_path(install_dir: str, relative_path: str) -> Path:
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts:
        raise HTTPException(status_code=400, detail="Ungueltiger relativer Dateipfad")
    base = Path(install_dir).resolve(strict=False)
    target = (base / relative_path).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Dateipfad liegt ausserhalb des Servers") from exc
    return target


def _server(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _agent_key(server: Server) -> str:
    install = (server.install_dir or "").strip()
    base = os.path.basename(os.path.normpath(install)) if install else ""
    if base and base not in {".", ".."} and "/" not in base and "\\" not in base and ".." not in base:
        return base
    return str(server.id)


def _agent(server: Server, db: Session) -> NodeClient | None:
    from services.node_service import NODE_OFFLINE_MSG, is_node_offline

    node = resolve_server_node(server, db)
    if node is None:
        return None
    if is_node_offline(node) and not node.is_local:
        raise HTTPException(status_code=503, detail=NODE_OFFLINE_MSG)
    if node.is_local and server.install_dir and os.path.isdir(server.install_dir):
        return None
    try:
        return NodeClient.from_node(node)
    except NodeClientError as exc:
        if node.is_local:
            return None
        raise HTTPException(status_code=503, detail=exc.message or "Node-Agent nicht erreichbar") from exc


def _agent_error(exc: NodeClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code not in {400, 403, 404, 409, 413}:
        status_code = 502
    return HTTPException(status_code=status_code, detail=exc.message or "Node-Agent Fehler")


# Temporaeres Verzeichnis der Chunk-Uploads. Es gehoert nicht in eine
# Dateiliste — weder fuer einen Menschen noch fuer die KI.
CHUNK_TMP_DIRNAME = ".msm-uploads"

# Wieviele Eintraege eine Auflistung hoechstens zurueckgibt. Fuer die
# Oberflaeche gilt keine Grenze; fuer die KI schon, weil jeder Eintrag als
# unvertrauenswuerdiger Text in den Modellkontext und damit ins Kostenbudget des
# Benutzers geht. Ein Serververzeichnis mit tausenden Mod-Dateien wuerde eine
# einzige Frage teuer machen.
MAX_LISTED_ENTRIES = 200


def wipe_server_root(db: Session, server: Server) -> int | None:
    """Leert das Serververzeichnis und **weist nach**, dass es leer ist.

    Gebaut fuer den Blueprint-Wechsel, und zwar wegen eines Betriebsfalls: ein
    Minecraft-Server wurde auf eine andere Version umgestellt, der Wechsel
    meldete Erfolg — und der Start scheiterte daran, dass die alte Welt noch
    dalag.

    Der Vorgaenger in `switch_server_blueprint` benutzte `os` und `shutil`, ohne
    dass sein Modul beides je importiert haette. Der lokale Zweig warf deshalb
    `NameError`, ein `except Exception` schluckte ihn, und der Wechsel lief
    weiter. Auf lokalen Nodes wurde damit **nie** eine Datei geloescht — vier
    Zeilen darunter stand trotzdem "erfolgreich gewechselt".

    Der zweite, echte Grund fuer misslingende Loeschungen bleibt bestehen:
    `itzg/minecraft-server` und viele andere Images legen ihre Daten unter der
    **Container-UID** an, und der Panel-Prozess kommt an `world/` nicht heran.
    Dafuer gibt es `repair_bind_mount_permissions` — der Loeschpfad
    (`server_deletion_service`) ruft es seit jeher, der Wechsel tat es nie.

    Drei Zusicherungen, die der alte Code nicht gab:

    1. **Der Container ist vorher weg.** Ein gestoppter Container haelt den
       Bind-Mount weiterhin; erst ohne ihn ist das Verzeichnis wirklich frei.
    2. **Kein Fehler wird verschluckt.** Weder `ignore_errors` noch ein blankes
       `except: pass`. Bleibt nach dem Rechte-Reparaturversuch etwas uebrig,
       fliegt `HTTPException` — und der Aufrufer bricht ab, statt Erfolg zu
       melden.
    3. **Das Ergebnis wird geprueft, nicht angenommen.** Zum Schluss steht ein
       `os.listdir`. Was dort noch liegt, steht in der Fehlermeldung.

    Der lokale Node ohne direkt sichtbares Verzeichnis geht ueber den Agenten —
    dieselbe Entscheidung, die `_agent` fuer jeden anderen Dateizugriff dieses
    Moduls trifft. Der Wechsel war die einzige Stelle im Projekt, die den
    Agenten nur bei *entfernten* Nodes kannte und lokal stillschweigend nichts
    tat.

    Rueckgabe ist die Zahl der entfernten Eintraege auf oberster Ebene, oder
    ``None``, wenn der Agent geleert hat — der meldet keine Anzahl zurueck, und
    eine erfundene Null waere schlechter als ein ehrliches "unbekannt".
    """
    from games.base import container_name_for

    node = resolve_server_node(server, db)
    entfernt = docker_service.remove(container_name_for(server.id), force=True, node=node)
    if not entfernt.get("ok"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "server_container_remove_failed",
                "message": "Der Container konnte nicht entfernt werden; das Serververzeichnis bleibt unangetastet.",
            },
        )

    agent = _agent(server, db)
    if agent is not None:
        try:
            agent.files_delete_server_root(_agent_key(server))
            agent.files_ensure_server_root(_agent_key(server))
        except NodeClientError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "server_root_wipe_failed",
                    "message": exc.message or "Der Node-Agent konnte das Serververzeichnis nicht leeren.",
                },
            ) from exc
        return None

    wurzel = (server.install_dir or "").strip()
    if not wurzel:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "server_root_wipe_failed",
                "message": "Der Server hat kein Installationsverzeichnis.",
            },
        )
    if not os.path.isdir(wurzel):
        # Nichts zu leeren, aber der Bind-Mount braucht das Verzeichnis. Wuerde
        # Docker es selbst anlegen, gehoerte es root — und der Spielprozess
        # koennte nicht hineinschreiben.
        os.makedirs(wurzel, mode=0o750, exist_ok=True)
        return 0

    geloescht = _leeren(wurzel)
    if geloescht is None:
        docker_service.repair_bind_mount_permissions(wurzel)
        geloescht = _leeren(wurzel)
    rest = sorted(os.listdir(wurzel))[:10]
    if rest:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "server_root_wipe_failed",
                "message": (
                    "Das Serververzeichnis konnte nicht geleert werden. Uebrig: "
                    + ", ".join(rest)
                ),
            },
        )
    return geloescht or 0


def _leeren(wurzel: str) -> int | None:
    """Loescht den Inhalt eines Verzeichnisses. ``None`` heisst: etwas ging schief.

    Bewusst ohne `ignore_errors` — der ganze Zweck dieses Moduls ist, dass ein
    misslungenes Loeschen sichtbar wird. Der Aufrufer repariert dann die Rechte
    und laesst es erneut laufen, so wie `write_server_text` es bei einem
    `PermissionError` schon immer tut.
    """
    import shutil

    anzahl = 0
    fehler = False
    for name in os.listdir(wurzel):
        pfad = os.path.join(wurzel, name)
        try:
            if os.path.isdir(pfad) and not os.path.islink(pfad):
                shutil.rmtree(pfad)
            else:
                os.remove(pfad)
            anzahl += 1
        except OSError:
            fehler = True
    return None if fehler else anzahl


def list_server_directory(
    db: Session, *, server_id: int, relative_path: str = "", limit: int | None = None
) -> dict:
    """Listet ein Verzeichnis im Serververzeichnis auf.

    Dieselbe Logik, die der Dateimanager nutzt — lokaler Pfad oder Node-Agent,
    dieselbe Pfadpruefung, dieselben Metadaten. Herausgeloest, weil die KI ohne
    Verzeichnisbaum nicht wissen kann, welche Dateien es ueberhaupt gibt: sie
    haette Namen raten muessen, und ein geratener Name ist entweder ein Treffer
    oder eine Fehlermeldung, aus der man Namen abzaehlen kann.

    ``limit`` kuerzt die Liste und meldet das ausdruecklich in ``truncated``.
    Stillschweigend abzuschneiden waere schlimmer als gar nicht aufzulisten: das
    Modell haelte die Datei, die es sucht, dann fuer nicht vorhanden.
    """
    server = _server(db, server_id)
    agent = _agent(server, db)
    if agent is not None:
        try:
            roh = agent.files_list(_agent_key(server), relative_path or "")
        except NodeClientError as exc:
            if exc.status_code == 404:
                return {"path": relative_path, "entries": [], "exists": False}
            raise _agent_error(exc) from exc
        eintraege = [
            {
                "name": e.get("name", ""),
                "is_dir": bool(e.get("is_dir")),
                "size": int(e.get("size") or 0),
                "modified": float(e.get("modified") or e.get("mtime") or 0),
            }
            for e in roh
            if e.get("name") != CHUNK_TMP_DIRNAME
        ]
    else:
        ziel = safe_path(server.install_dir, relative_path)
        if not ziel.exists():
            return {"path": relative_path, "entries": [], "exists": False}
        if not ziel.is_dir():
            raise HTTPException(status_code=400, detail="Pfad ist kein Verzeichnis")
        wurzel = Path(server.install_dir).resolve(strict=False)
        eintraege = []
        try:
            for item in sorted(
                ziel.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
            ):
                if item.name == CHUNK_TMP_DIRNAME and item.parent == wurzel:
                    continue
                try:
                    daten = file_edit_service.metadata(item)
                except (PermissionError, OSError):
                    # Einzelne Eintraege ohne Leserechte ueberspringen, den Rest
                    # zeigen — genau wie der Dateimanager.
                    continue
                eintraege.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": daten["size"],
                    "modified": daten["modified"],
                })
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="Keine Berechtigung für dieses Verzeichnis"
            )

    gesamt = len(eintraege)
    if limit is not None and gesamt > limit:
        eintraege = eintraege[:limit]
    return {
        "path": relative_path,
        "entries": eintraege,
        "exists": True,
        "truncated": limit is not None and gesamt > limit,
        "total": gesamt,
    }


def read_server_text(db: Session, *, server_id: int, relative_path: str) -> dict:
    server = _server(db, server_id)
    agent = _agent(server, db)
    if agent is not None:
        try:
            return {"path": relative_path, "name": relative_path.rsplit("/", 1)[-1], **agent.files_read_info(_agent_key(server), relative_path)}
        except NodeClientError as exc:
            raise _agent_error(exc) from exc
    target = safe_path(server.install_dir, relative_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Pfad ist keine Datei")
    if target.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Datei ist zu gross")
    try:
        return {"path": relative_path, "name": target.name, **file_edit_service.read_text(target)}
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Datei konnte nicht gelesen werden") from exc


def _apply_permissions(install_dir: str, target: Path) -> None:
    def normalize(path: Path) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return
        path.chmod(0o750 if path.is_dir() or stat.S_IMODE(info.st_mode) & 0o111 else 0o640)

    try:
        normalize(target)
        base = Path(install_dir).resolve()
        parent = target.parent.resolve()
        while parent != base and parent != parent.parent:
            normalize(parent)
            parent = parent.parent
    except OSError:
        return


def write_server_text(
    db: Session,
    *,
    user: User,
    server_id: int,
    relative_path: str,
    content: str,
    expected_revision: str | None,
    create_only: bool = False,
    repair_permissions: Callable[[str], dict] | None = None,
) -> dict:
    server = _server(db, server_id)
    agent = _agent(server, db)
    if agent is not None:
        try:
            try:
                current = agent.files_read_info(_agent_key(server), relative_path)
            except NodeClientError as exc:
                if exc.status_code != 404:
                    raise
                current = None
            if expected_revision is not None and (
                current is None or current.get("revision") != expected_revision
            ):
                raise HTTPException(status_code=409, detail={"code": "FILE_REVISION_CONFLICT"})
            if create_only and current is not None:
                raise HTTPException(status_code=409, detail="Zieldatei existiert bereits")
            if current is not None:
                file_history_service.snapshot(server_id, relative_path, str(current.get("content", "")), user.id)
            result = agent.files_write(
                _agent_key(server), relative_path, content, expected_revision, create_only
            )
            return {"path": relative_path, **result}
        except NodeClientError as exc:
            raise _agent_error(exc) from exc
        except (DisSidecarError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail="Versionsspeicher ist nicht verfuegbar") from exc

    target = safe_path(server.install_dir, relative_path)
    try:
        current = file_edit_service.read_text(target) if target.is_file() else None
        if expected_revision is not None and (
            current is None or current.get("revision") != expected_revision
        ):
            raise FileRevisionConflict(current.get("revision") if current else None)
        if create_only and current is not None:
            raise FileExistsError
        if current is not None:
            file_history_service.snapshot(server_id, relative_path, str(current["content"]), user.id)
        try:
            result = file_edit_service.write_text(
                target,
                content,
                expected_revision=expected_revision,
                create_only=create_only,
            )
        except PermissionError as first_error:
            repair = (repair_permissions or docker_service.repair_bind_mount_permissions)(
                server.install_dir
            )
            if not repair.get("ok"):
                raise HTTPException(
                    status_code=500, detail="Datei konnte nicht gespeichert werden"
                ) from first_error
            result = file_edit_service.write_text(
                target,
                content,
                expected_revision=expected_revision,
                create_only=create_only,
            )
        _apply_permissions(server.install_dir, target)
        return {"path": relative_path, **result}
    except FileRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "FILE_REVISION_CONFLICT", "current_revision": exc.current_revision},
        ) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Zieldatei existiert bereits") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="Datei konnte nicht gespeichert werden") from exc
    except (DisSidecarError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Versionsspeicher ist nicht verfuegbar") from exc
