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


# Grenzen der Inhaltssuche. Sie stehen als Vorgabe hier und nicht beim Aufrufer,
# damit ein neuer Aufrufer nicht versehentlich ohne Deckel sucht — die Suche
# liest im Zweifel jede Datei des Servers.
#
# Eine Zeile wird hart bei 2000 Zeichen gekappt: eine minifizierte Datei besteht
# aus einer einzigen Zeile von mehreren hundert Kilobyte, und die gehoert in
# keine Antwort. Auf ein Anzeigemass kuerzt der Aufrufer selbst — die KI erst
# **nach** der Redaktion, sonst zerschnitte die Kuerzung ein Geheimnis und die
# Redaktion faende es nicht mehr.
SEARCH_MAX_FILES = 200
SEARCH_MAX_DEPTH = 8
SEARCH_MAX_MATCHES = 200
SEARCH_MAX_LINE_CHARS = 2_000
SEARCH_MAX_CONTEXT_LINES = 5


def search_file_contents(
    db: Session,
    *,
    server_id: int,
    query: str,
    relative_path: str = "",
    context: int = 0,
    max_files: int = SEARCH_MAX_FILES,
    max_depth: int = SEARCH_MAX_DEPTH,
    max_matches: int = SEARCH_MAX_MATCHES,
) -> dict:
    """Sucht einen Text **in** den Dateien — nicht in ihren Namen.

    Der Dateimanager konnte bisher nur Namen vergleichen
    (`routers/files.py::search_files`). Wer wissen wollte, in welcher Datei ein
    Wert steht, musste ihn selbst suchen — bei einer Spielkonfiguration mit
    dreizehntausend Zeilen heisst das: von Hand durchscrollen.

    Bewusst **eine** Implementierung fuer Panel und KI. Ein zweiter Suchpfad
    waere ein zweiter Ort mit eigenen Grenzen, eigener Pfadpruefung und eigenem
    Verhalten am Node-Agenten — und die Zusage "die KI sieht dasselbe wie der
    Benutzer" waere dann eine Behauptung ueber zwei verschiedene Programme.

    **Ohne Rechtepruefung und ohne Redaktion.** Beides gehoert zum Aufrufer: der
    Endpunkt prueft `server.files.read` wie beim Lesen einer Datei, und ob eine
    Trefferzeile redigiert werden muss, haengt vom Empfaenger ab — ein Mensch
    mit Leserecht sieht seine Zugangsdaten ohnehin im Editor, ein Modell nie.

    Gebaut auf `list_server_directory` und `read_server_text`, weil beide den
    Node-Agenten schon beherrschen. Damit sucht dieselbe Funktion auf einem
    lokalen wie auf einem entfernten Server.
    """
    from services.ai_action_service import is_binary_text

    # Steht bewusst vor `auflisten`: die Funktion setzt es selbst, wenn sie ein
    # Verzeichnis ueberspringen muss. Ein uebersprungenes Verzeichnis ist eine
    # Kuerzung des Ergebnisses und muss dem Aufrufer gesagt werden.
    gekuerzt = False

    def auflisten(pfad: str) -> dict | None:
        """Das Verzeichnis unter ``pfad`` — oder ``None``, wenn es keines ist.

        Die beiden Zugriffswege antworten auf einen *Datei*pfad verschieden: der
        lokale wirft 400 "Pfad ist kein Verzeichnis", der Node-Agent meldet
        nichts Auflistbares. Beides bedeutet dasselbe und wird hier auf dieselbe
        Antwort gebracht, damit der Aufrufer den Unterschied nicht kennen muss.
        """
        nonlocal gekuerzt
        try:
            ergebnis = list_server_directory(
                db, server_id=server_id, relative_path=pfad, limit=MAX_LISTED_ENTRIES
            )
        except HTTPException as exc:
            if exc.status_code >= 500:
                # Ab 500 geht es nicht mehr um dieses eine Verzeichnis, sondern
                # um den Zugriffsweg: 502/503 heisst, der Node-Agent ist weg.
                # Dann liefe jedes weitere Verzeichnis in denselben Fehler, und
                # ein leeres Suchergebnis waere eine Luege statt einer Auskunft.
                raise
            if exc.status_code not in (400, 404):
                # 400 und 404 sind hier keine Stoerung, sondern die Auskunft
                # "das ist kein Verzeichnis" (siehe Docstring). Alles andere ist
                # eine: ein Ordner ohne Leserecht wirft beim `iterdir` einen
                # PermissionError, den `list_server_directory` zu 403 macht, und
                # ein Symlink, der aus dem Serverbaum herauszeigt, wird von
                # `safe_path` ebenfalls mit 403 abgewiesen. Vorher riss ein
                # einziger solcher Ordner die gesamte Inhaltssuche ab, obwohl
                # alle anderen lesbar waren - die Dateileseschleife weiter unten
                # haelt es seit jeher richtig: ueberspringen und weitersuchen.
                # Verschwiegen wird es trotzdem nicht, `gekuerzt` sagt dem
                # Aufrufer, dass er kein vollstaendiges Ergebnis in der Hand hat.
                gekuerzt = True
            return None
        if not ergebnis.get("exists", True) or not ergebnis.get("entries"):
            return None
        return ergebnis

    leer = {
        "path": relative_path,
        "query": query,
        "matches": [],
        "files_searched": 0,
        "truncated": False,
    }

    # Breitensuche statt Rekursion: so gehen die Deckel auf Dateizahl und Tiefe
    # zuerst in die Breite und nicht in den ersten Unterordner, den es findet.
    # Wer in `Data/` sucht, will nicht, dass das Budget in `Data/Bundles/`
    # aufgebraucht ist, bevor `Data/Config/` an die Reihe kommt.
    dateien: list[str] = []
    verzeichnisse: list[tuple[str, int]] = []
    wurzelliste = auflisten(relative_path)
    if wurzelliste is None:
        if not relative_path:
            # Auch die Wurzel selbst kann unlesbar sein. Dann hat `auflisten`
            # bereits `gekuerzt` gesetzt, und die Antwort muss das mittragen:
            # ein leeres Ergebnis mit `truncated: false` waere die Behauptung,
            # es gebe nichts zu finden - dabei wurde gar nicht gesucht.
            return {**leer, "truncated": gekuerzt}
        # Kein Verzeichnis — dann ist der Pfad eine Datei, und die Suche gilt
        # genau ihr. Der haeufigste Fall ueberhaupt.
        dateien.append(relative_path)
    else:
        verzeichnisse.append((relative_path, 0))

    # Die Wurzel ist bereits aufgelistet; ein zweiter Abruf dafuer waere beim
    # Node-Agenten eine Netzrunde umsonst.
    vorgemerkt: dict | None = wurzelliste
    while verzeichnisse and len(dateien) < max_files:
        pfad, tiefe = verzeichnisse.pop(0)
        if vorgemerkt is not None:
            auflistung, vorgemerkt = vorgemerkt, None
        else:
            auflistung = auflisten(pfad)
        if auflistung is None:
            continue
        if auflistung.get("truncated"):
            gekuerzt = True
        for eintrag in auflistung.get("entries", []):
            name = str(eintrag.get("name") or "")
            if not name:
                continue
            voll = f"{pfad}/{name}" if pfad else name
            if eintrag.get("is_dir"):
                if tiefe + 1 <= max_depth:
                    verzeichnisse.append((voll, tiefe + 1))
                else:
                    gekuerzt = True
            elif len(dateien) < max_files:
                dateien.append(voll)
            else:
                gekuerzt = True

    nadel = query.casefold()
    kontext = max(0, min(context, SEARCH_MAX_CONTEXT_LINES))
    treffer: list[dict] = []
    gelesen = 0
    gekuerzt = gekuerzt or bool(verzeichnisse)
    for datei in dateien:
        if len(treffer) >= max_matches:
            gekuerzt = True
            break
        try:
            inhalt = str(read_server_text(
                db, server_id=server_id, relative_path=datei
            )["content"])
        except HTTPException:
            # Zu gross, verschwunden, keine Datei — kein Grund, die ganze Suche
            # scheitern zu lassen.
            continue
        if is_binary_text(inhalt):
            continue
        # Erst hier gezaehlt: `files_searched` soll sagen, worin wirklich gesucht
        # wurde. Uebersprungene Binaerdateien mitzuzaehlen waere eine Auskunft,
        # auf die sich niemand verlassen kann.
        gelesen += 1
        zeilen = inhalt.splitlines()
        for nummer, text in enumerate(zeilen, start=1):
            if nadel not in text.casefold():
                continue
            if len(treffer) >= max_matches:
                gekuerzt = True
                break
            eintragszeile: dict = {
                "path": datei,
                "line": nummer,
                "text": text.strip()[:SEARCH_MAX_LINE_CHARS],
            }
            if kontext:
                von = max(0, nummer - 1 - kontext)
                bis = min(len(zeilen), nummer + kontext)
                eintragszeile["context"] = [
                    zeile.strip()[:SEARCH_MAX_LINE_CHARS] for zeile in zeilen[von:bis]
                ]
                eintragszeile["context_first_line"] = von + 1
            treffer.append(eintragszeile)

    return {
        "path": relative_path,
        "query": query,
        "matches": treffer,
        "files_searched": gelesen,
        "truncated": gekuerzt,
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
