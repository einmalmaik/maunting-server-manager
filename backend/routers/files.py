"""File Manager API.

Bietet einen sicheren, server-scopierten Zugriff auf das `install_dir` jedes
Servers: browse/read/write/upload (einfach + chunked), download, mkdir, delete,
rename, move, search und zip extract.

Sicherheits-Invarianten:
- Alle Pfade laufen durch `_safe_path()`. `_safe_path()` verbietet jeden Pfad,
  der nach Aufloesung der Symlinks ausserhalb von `install_dir` landen wuerde
  (kein `startswith`-Boundary-Bug mehr).
- Jeder mutierende Endpunkt verlangt `verify_csrf` + die passende
  `server.files.*`-Permission (Phase-2-RBAC).
- Blockierte Extensions (`exe`, `bat`, ...) duerfen weder per Upload noch per
  Chunked-Upload-Finalize ins Server-Root.
- Chunked-Uploads laufen ueber Temp-Dateien INNERHALB des Server-Roots und
  werden am Ende per `os.replace` atomar ans Ziel geschoben. Kein
  Tempfile-Outside-Root, kein Symlink-Trick.
"""
from __future__ import annotations

import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from blueprints.archive_extract import ArchiveExtractError, safe_extract_archive
from database import get_db
from models import Server, User
from dependencies import get_current_user, verify_csrf, require_server_permission

router = APIRouter(prefix="/api/files", tags=["files"])

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".vbs", ".vbe", ".js", ".wsh", ".wsf"}
# Klassisches Single-Shot-Upload-Limit. Groessere Dateien laufen ueber die
# Chunked-Upload-Routen (init/chunk/finalize). 100 MB ist gross genug fuer
# Mod-Archive, Configs, Screenshots; alles darueber soll resumable laufen.
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_EDIT_SIZE = 5 * 1024 * 1024  # 5 MB fuer Text-Editing
# Obergrenze fuer Chunked-Uploads. 10 GB deckt manuelle Mod-Uploads
# (DayZ-Modpacks, UE5-Pak-Dateien) komfortabel ab. Hoeher wuerden wir nur
# zulassen, wenn der Use-Case real existiert.
MAX_CHUNKED_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
# Max Suchergebnisse (Server-side capped, damit ein wildes "q=a" nicht
# zehntausende Pfade ueber den Wire schickt).
MAX_SEARCH_RESULTS = 200
# Verzeichnis fuer Chunked-Upload-Temp-Dateien, relativ zum Server-Root.
# Liegt INNERHALB des Roots, damit `_safe_path` weiter greift und keine
# externen Tempfile-Pfade entstehen.
CHUNK_TMP_DIRNAME = ".msm-uploads"


def _safe_path(install_dir: str, rel_path: str) -> Path:
    """Resolves ``rel_path`` strictly within ``install_dir``.

    - Verbietet absolute Pfade.
    - Resolved Symlinks (``Path.resolve(strict=False)``) und checkt anschliessend
      via ``relative_to`` gegen den ebenfalls aufgeloesten Server-Root.
    - Verbietet zusaetzlich `..`-Segmente im Eingabe-Pfad, bevor Resolve
      passieren kann (Defense in Depth).
    """
    # Kein absoluter Pfad — Frontend liefert immer relativ zum Server-Root.
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise HTTPException(status_code=400, detail="Absolute Pfade sind nicht erlaubt")
    # Defense-in-depth: `..`-Segmente direkt blockieren. `resolve` wuerde diese
    # zwar normalisieren, aber wir wollen gar nicht erst Aufrufe sehen, die
    # versuchen aus dem Server-Root rauszubrechen.
    parts = Path(rel_path).parts
    if any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail="Pfad-Traversal (..) ist nicht erlaubt")

    base = Path(install_dir).resolve(strict=False)
    candidate = (base / rel_path).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Zugriff verweigert: Pfad ausserhalb des Server-Verzeichnisses")
    return candidate


def _get_server(server_id: int, db: Session) -> Server:
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _ensure_allowed_extension(filename: str) -> None:
    ext = os.path.splitext(filename)[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Dateityp {ext} ist nicht erlaubt")


def _chunk_tmp_dir(install_dir: str) -> Path:
    """Temp-Verzeichnis fuer aktive Chunked-Uploads.

    Liegt INNERHALB von ``install_dir`` (damit `_safe_path` greift) und ist
    fuer den Container irrelevant — Game-Server lesen `.msm-uploads` nicht.
    """
    base = Path(install_dir).resolve(strict=False)
    tmp = base / CHUNK_TMP_DIRNAME
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tmp, 0o750)
    except OSError:
        pass
    return tmp


class FileWriteRequest(BaseModel):
    content: str


class MkdirRequest(BaseModel):
    name: str


class RenameRequest(BaseModel):
    new_name: str


class MoveRequest(BaseModel):
    """Verschiebt ``from_path`` (Datei oder Verzeichnis) nach
    ``to_dir`` / Basename von ``from_path``. Ziel-Verzeichnis muss existieren.
    Optional kann ``new_name`` einen anderen Zielnamen vorgeben (combined
    Move + Rename)."""
    from_path: str = Field(..., min_length=1)
    to_dir: str = ""
    new_name: str | None = None


class ChunkedUploadInitRequest(BaseModel):
    path: str = Field("", description="Relatives Ziel-Verzeichnis im Server-Root")
    filename: str = Field(..., min_length=1, max_length=255)
    total_size: int = Field(..., ge=0)


@router.get("/{server_id}/browse")
def browse_directory(
    server_id: int,
    path: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """List files and directories at the given path."""
    require_server_permission(user, server_id, db, "server.files.read")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        return {"path": path, "entries": [], "exists": False}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Pfad ist kein Verzeichnis")

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # Versteckte Chunk-Tempfiles nicht in den Tree mischen.
            if item.name == CHUNK_TMP_DIRNAME and item.parent == Path(server.install_dir).resolve(strict=False):
                continue
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else 0,
                    "modified": stat.st_mtime,
                })
            except (PermissionError, OSError):
                # Einzelne Eintraege ohne Leserechte ueberspringen, Rest anzeigen
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Keine Berechtigung fuer dieses Verzeichnis")

    return {"path": path, "entries": entries, "exists": True}


@router.get("/{server_id}/search")
def search_paths(
    server_id: int,
    q: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Server-side, capped, recursive Substring-Suche auf Datei-/Ordnernamen.

    Suche ist Case-Insensitive. Pfade werden relativ zum Server-Root
    zurueckgegeben. Maximal ``MAX_SEARCH_RESULTS`` Eintraege; danach setzen
    wir ``truncated=True``.
    """
    require_server_permission(user, server_id, db, "server.files.read")
    server = _get_server(server_id, db)
    base = Path(server.install_dir).resolve(strict=False)
    if not base.exists():
        return {"q": q, "results": [], "truncated": False}

    needle = q.lower()
    results: list[dict] = []
    truncated = False
    for root, dirs, files in os.walk(base):
        # Versteckte Upload-Tempfiles aussparen.
        dirs[:] = [d for d in dirs if d != CHUNK_TMP_DIRNAME]
        for name in dirs + files:
            if needle not in name.lower():
                continue
            full = Path(root) / name
            try:
                rel = full.relative_to(base).as_posix()
            except ValueError:
                continue
            results.append({
                "name": name,
                "path": rel,
                "is_dir": full.is_dir(),
            })
            if len(results) >= MAX_SEARCH_RESULTS:
                truncated = True
                break
        if truncated:
            break

    return {"q": q, "results": results, "truncated": truncated}


@router.get("/{server_id}/read")
def read_file(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Read a text file's content."""
    require_server_permission(user, server_id, db, "server.files.read")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Pfad ist keine Datei")
    if target.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu gross zum Bearbeiten (max 5 MB)")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lesen fehlgeschlagen: {e}")

    return {"path": path, "name": target.name, "content": content, "size": target.stat().st_size}


@router.put("/{server_id}/write")
def write_file(
    server_id: int,
    path: str = Query(...),
    body: FileWriteRequest = ...,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Write/create a text file."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schreiben fehlgeschlagen: {e}")

    return {"message": "Datei gespeichert", "path": path}


@router.post("/{server_id}/upload")
async def upload_file(
    server_id: int,
    path: str = Query(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Single-Shot-Upload (≤ ``MAX_UPLOAD_SIZE``). Fuer groessere Dateien die
    Chunked-Upload-Routen nutzen."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)
    target_dir = _safe_path(server.install_dir, path)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Kein Dateiname")
    _ensure_allowed_extension(file.filename)

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = _safe_path(server.install_dir, os.path.join(path, file.filename))

    total = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Datei zu gross fuer Direkt-Upload (max {MAX_UPLOAD_SIZE // (1024*1024)} MB) "
                            "— bitte den resumable Upload verwenden."
                        ),
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload fehlgeschlagen: {e}")

    return {"message": "Datei hochgeladen", "name": file.filename, "size": total}


# ── Chunked-Upload (resumable) ────────────────────────────────────────────


@router.post("/{server_id}/upload/init")
def chunked_upload_init(
    server_id: int,
    body: ChunkedUploadInitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Startet einen resumable Upload. Gibt eine Upload-ID zurueck.

    Der Client laedt anschliessend N Chunks ueber ``/upload/chunk`` hoch und
    ruft ``/upload/finalize`` auf. Die Upload-ID identifiziert eine
    Temp-Datei in ``.msm-uploads/`` innerhalb des Server-Roots.
    """
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)

    if body.total_size > MAX_CHUNKED_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu gross (max {MAX_CHUNKED_UPLOAD_SIZE // (1024*1024*1024)} GB)",
        )

    _ensure_allowed_extension(body.filename)
    # Pfad-Trennzeichen im Dateinamen sind verboten — verhindert dass jemand
    # via "filename": "../../etc/passwd" die Zielzeile aushebelt.
    if "/" in body.filename or "\\" in body.filename:
        raise HTTPException(status_code=400, detail="Dateiname darf keine Pfad-Trennzeichen enthalten")

    # Ziel-Verzeichnis validieren und sicherstellen, dass es existiert.
    target_dir = _safe_path(server.install_dir, body.path or "")
    target_dir.mkdir(parents=True, exist_ok=True)

    # Endpfad-Validierung jetzt schon, damit der Client nicht erst alle Chunks
    # hochladen muss, bevor wir merken, dass das Ziel verboten ist.
    _safe_path(server.install_dir, os.path.join(body.path or "", body.filename))

    upload_id = secrets.token_hex(16)
    tmp_dir = _chunk_tmp_dir(server.install_dir)
    tmp_path = tmp_dir / f"{upload_id}.part"
    # Datei leer anlegen, damit der naechste Chunk-Append direkt funktioniert.
    tmp_path.touch()
    try:
        os.chmod(tmp_path, 0o640)
    except OSError:
        pass

    # Metadaten neben der Tempdatei ablegen, damit ein Server-Restart nicht
    # alle laufenden Uploads verliert (Best-effort — wir cleanen alte Reste
    # ohnehin opportunistisch).
    meta_path = tmp_dir / f"{upload_id}.meta"
    meta_path.write_text(
        f"{int(time.time())}\n{body.path}\n{body.filename}\n{body.total_size}\n",
        encoding="utf-8",
    )

    return {
        "upload_id": upload_id,
        "chunk_size_recommendation": 8 * 1024 * 1024,  # 8 MB Chunks empfohlen
    }


@router.put("/{server_id}/upload/{upload_id}/chunk")
async def chunked_upload_chunk(
    server_id: int,
    upload_id: str,
    chunk: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Haengt einen Chunk an die Tempdatei. Idempotent ist das NICHT — der
    Client muss die Chunks in Reihenfolge senden; bei Wiederaufnahme nach
    Verbindungsabbruch liest er ``/upload/{id}/status`` und schickt ab Offset
    weiter (siehe ``/status``)."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)

    if not upload_id.isalnum() or len(upload_id) != 32:
        raise HTTPException(status_code=400, detail="Ungueltige Upload-ID")

    tmp_dir = _chunk_tmp_dir(server.install_dir)
    tmp_path = tmp_dir / f"{upload_id}.part"
    if not tmp_path.exists():
        raise HTTPException(status_code=404, detail="Upload-ID unbekannt")

    written = 0
    try:
        with open(tmp_path, "ab") as f:
            while data := await chunk.read(1024 * 1024):
                written += len(data)
                # Sicherheits-Cap: ein einzelner Chunk darf nicht das gesamte
                # Limit sprengen. Wir lassen 64 MB pro Chunk durch — das ist
                # mehr als die Default-Empfehlung (8 MB), aber begrenzt
                # Speicherspitzen.
                if written > 64 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Chunk zu gross (max 64 MB)")
                # Gesamt-Tempgroesse darf das Hard-Cap nicht ueberschreiten.
                if tmp_path.stat().st_size + 0 > MAX_CHUNKED_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="Upload ueberschreitet maximales Limit")
                f.write(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chunk-Upload fehlgeschlagen: {e}")

    return {"received": written, "total_received": tmp_path.stat().st_size}


@router.get("/{server_id}/upload/{upload_id}/status")
def chunked_upload_status(
    server_id: int,
    upload_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Aktueller Offset eines laufenden Chunked-Uploads (fuer Wiederaufnahme)."""
    require_server_permission(user, server_id, db, "server.files.read")
    server = _get_server(server_id, db)

    if not upload_id.isalnum() or len(upload_id) != 32:
        raise HTTPException(status_code=400, detail="Ungueltige Upload-ID")

    tmp_dir = _chunk_tmp_dir(server.install_dir)
    tmp_path = tmp_dir / f"{upload_id}.part"
    if not tmp_path.exists():
        raise HTTPException(status_code=404, detail="Upload-ID unbekannt")

    return {"upload_id": upload_id, "received": tmp_path.stat().st_size}


@router.post("/{server_id}/upload/{upload_id}/finalize")
def chunked_upload_finalize(
    server_id: int,
    upload_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Verschiebt die fertige Tempdatei atomar an das endgueltige Ziel."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)

    if not upload_id.isalnum() or len(upload_id) != 32:
        raise HTTPException(status_code=400, detail="Ungueltige Upload-ID")

    tmp_dir = _chunk_tmp_dir(server.install_dir)
    tmp_path = tmp_dir / f"{upload_id}.part"
    meta_path = tmp_dir / f"{upload_id}.meta"
    if not tmp_path.exists() or not meta_path.exists():
        raise HTTPException(status_code=404, detail="Upload-ID unbekannt")

    try:
        meta_lines = meta_path.read_text(encoding="utf-8").splitlines()
        if len(meta_lines) < 4:
            raise ValueError("meta corrupt")
        _ts, rel_dir, filename, total_size_str = meta_lines[0], meta_lines[1], meta_lines[2], meta_lines[3]
        total_size = int(total_size_str)
    except Exception:
        raise HTTPException(status_code=500, detail="Upload-Metadaten beschaedigt — bitte neu starten")

    actual_size = tmp_path.stat().st_size
    if total_size and actual_size != total_size:
        raise HTTPException(
            status_code=400,
            detail=f"Upload unvollstaendig ({actual_size}/{total_size} bytes)",
        )

    _ensure_allowed_extension(filename)
    dest = _safe_path(server.install_dir, os.path.join(rel_dir, filename))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Wir ueberschreiben nicht ungefragt; der Client soll vorher renamen
        # oder explizit "overwrite=true" senden (Phase-4 KISS: kein
        # Overwrite-Schalter — Client kann einfach via rename gehen).
        raise HTTPException(status_code=409, detail="Zieldatei existiert bereits")

    try:
        os.replace(tmp_path, dest)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verschieben fehlgeschlagen: {e}")

    # Meta jetzt erst loeschen — wenn `os.replace` oben fehlschlaegt, lebt der
    # Upload weiter und der User kann es nochmal probieren.
    meta_path.unlink(missing_ok=True)

    return {
        "message": "Upload abgeschlossen",
        "name": filename,
        "size": actual_size,
        "path": os.path.join(rel_dir, filename) if rel_dir else filename,
    }


@router.delete("/{server_id}/upload/{upload_id}")
def chunked_upload_abort(
    server_id: int,
    upload_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Bricht einen laufenden Chunked-Upload ab und raeumt die Tempdatei auf."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)

    if not upload_id.isalnum() or len(upload_id) != 32:
        raise HTTPException(status_code=400, detail="Ungueltige Upload-ID")

    tmp_dir = _chunk_tmp_dir(server.install_dir)
    for suffix in (".part", ".meta"):
        p = tmp_dir / f"{upload_id}{suffix}"
        if p.exists():
            p.unlink(missing_ok=True)
    return {"message": "Upload abgebrochen"}


# ── Download / mkdir / delete / rename / move / extract ─────────────────


@router.get("/{server_id}/download")
def download_file(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download a file."""
    require_server_permission(user, server_id, db, "server.files.read")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")

    return FileResponse(path=str(target), filename=target.name)


@router.post("/{server_id}/mkdir")
def make_directory(
    server_id: int,
    path: str = Query(default=""),
    body: MkdirRequest = ...,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Create a new directory."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)
    if "/" in body.name or "\\" in body.name or body.name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Ungueltiger Ordnername")
    target = _safe_path(server.install_dir, os.path.join(path, body.name))

    if target.exists():
        raise HTTPException(status_code=409, detail="Verzeichnis existiert bereits")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erstellen fehlgeschlagen: {e}")

    return {"message": "Verzeichnis erstellt", "path": os.path.join(path, body.name)}


@router.delete("/{server_id}/delete")
def delete_path(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Delete a file or directory."""
    require_server_permission(user, server_id, db, "server.files.delete")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Pfad nicht gefunden")

    # Never delete the install_dir itself
    if target.resolve() == Path(server.install_dir).resolve():
        raise HTTPException(status_code=403, detail="Server-Stammverzeichnis kann nicht geloescht werden")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Loeschen fehlgeschlagen: {e}")

    return {"message": "Geloescht", "path": path}


@router.post("/{server_id}/rename")
def rename_path(
    server_id: int,
    path: str = Query(...),
    body: RenameRequest = ...,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Rename a file or directory in place (kein Verzeichniswechsel)."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)
    if "/" in body.new_name or "\\" in body.new_name or body.new_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Ungueltiger neuer Name")
    target = _safe_path(server.install_dir, path)
    new_target = _safe_path(server.install_dir, os.path.join(os.path.dirname(path), body.new_name))

    if not target.exists():
        raise HTTPException(status_code=404, detail="Pfad nicht gefunden")
    if new_target.exists():
        raise HTTPException(status_code=409, detail="Zielname existiert bereits")

    try:
        target.rename(new_target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Umbenennen fehlgeschlagen: {e}")

    return {"message": "Umbenannt", "old_path": path, "new_path": os.path.join(os.path.dirname(path), body.new_name)}


@router.post("/{server_id}/move")
def move_path(
    server_id: int,
    body: MoveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Verschiebt Datei/Verzeichnis innerhalb des Server-Roots.

    ``from_path`` muss existieren. ``to_dir`` muss ein Verzeichnis sein,
    das innerhalb des Server-Roots existiert (root = ``""``).
    """
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)

    source = _safe_path(server.install_dir, body.from_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    # Server-Root selbst kann nicht verschoben werden.
    if source.resolve() == Path(server.install_dir).resolve():
        raise HTTPException(status_code=403, detail="Server-Stammverzeichnis kann nicht verschoben werden")

    dest_dir = _safe_path(server.install_dir, body.to_dir or "")
    if not dest_dir.exists() or not dest_dir.is_dir():
        raise HTTPException(status_code=404, detail="Ziel-Verzeichnis nicht gefunden")

    final_name = body.new_name if body.new_name else source.name
    if "/" in final_name or "\\" in final_name or final_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Ungueltiger Zielname")

    # Ziel-Pfad ueber _safe_path bauen (relative_to(install_dir) statt
    # naked-join, damit das Resultat sicher im Root bleibt).
    rel_target = os.path.join(body.to_dir or "", final_name)
    target = _safe_path(server.install_dir, rel_target)
    if target.exists():
        raise HTTPException(status_code=409, detail="Zielpfad existiert bereits")

    # Move INTO yourself blockieren (z.B. Verzeichnis in ein Unterverzeichnis
    # von sich selbst zu verschieben wuerde shutil.move zerschiessen).
    try:
        target.relative_to(source)
        raise HTTPException(status_code=400, detail="Ziel liegt innerhalb der Quelle")
    except ValueError:
        pass

    try:
        shutil.move(str(source), str(target))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verschieben fehlgeschlagen: {e}")

    return {"message": "Verschoben", "from_path": body.from_path, "to_path": rel_target}


_ALLOWED_EXTRACT_EXTS = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz2",
)


def _is_archive(name: str) -> bool:
    n = name.lower()
    return n.endswith(".zip") or any(n.endswith(ext) for ext in _ALLOWED_EXTRACT_EXTS)


@router.post("/{server_id}/extract")
def extract_archive(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Extract a zip or tar archive in place."""
    require_server_permission(user, server_id, db, "server.files.write")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archiv nicht gefunden")
    if not _is_archive(target.name):
        raise HTTPException(
            status_code=400,
            detail="Nur ZIP- und Tar-Archive werden unterstuetzt.",
        )

    extract_dir = target.parent
    try:
        safe_extract_archive(target, extract_dir, Path(server.install_dir).resolve())
    except ArchiveExtractError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Entpacken fehlgeschlagen: {e}")

    return {"message": "Archiv entpackt", "path": os.path.dirname(path)}
