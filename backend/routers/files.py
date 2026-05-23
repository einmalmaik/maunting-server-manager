"""File Manager API — browse, read, write, upload, download, mkdir, delete, zip extract.

All paths are scoped to server.install_dir for security (no path traversal).
"""
import os
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Server, User
from dependencies import get_current_user, verify_csrf, require_server_permission

router = APIRouter(prefix="/api/files", tags=["files"])

BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".vbs", ".vbe", ".js", ".wsh", ".wsf"}
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB
MAX_EDIT_SIZE = 5 * 1024 * 1024  # 5 MB for text editing


def _safe_path(install_dir: str, rel_path: str) -> Path:
    """Resolves a relative path within install_dir. Prevents path traversal."""
    base = Path(install_dir).resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Zugriff verweigert: Pfad außerhalb des Server-Verzeichnisses")
    return target


def _get_server(server_id: int, db: Session) -> Server:
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


class FileWriteRequest(BaseModel):
    content: str


class MkdirRequest(BaseModel):
    name: str


class RenameRequest(BaseModel):
    new_name: str


@router.get("/{server_id}/browse")
def browse_directory(
    server_id: int,
    path: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """List files and directories at the given path."""
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        return {"path": path, "entries": [], "exists": False}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Pfad ist kein Verzeichnis")

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            stat = item.stat()
            entries.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": stat.st_size if item.is_file() else 0,
                "modified": stat.st_mtime,
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Verzeichnis")

    return {"path": path, "entries": entries, "exists": True}


@router.get("/{server_id}/read")
def read_file(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Read a text file's content."""
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Pfad ist keine Datei")
    if target.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu groß zum Bearbeiten (max 5 MB)")

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
    require_server_permission(user, server_id, db, "can_edit_config")
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
    """Upload a file to the given directory."""
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
    target_dir = _safe_path(server.install_dir, path)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Kein Dateiname")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Dateityp {ext} ist nicht erlaubt")

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
                    raise HTTPException(status_code=413, detail="Datei zu groß (max 500 MB)")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload fehlgeschlagen: {e}")

    return {"message": "Datei hochgeladen", "name": file.filename, "size": total}


@router.get("/{server_id}/download")
def download_file(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download a file."""
    require_server_permission(user, server_id, db, "can_edit_config")
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
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
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
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Pfad nicht gefunden")

    # Never delete the install_dir itself
    if target.resolve() == Path(server.install_dir).resolve():
        raise HTTPException(status_code=403, detail="Server-Stammverzeichnis kann nicht gelöscht werden")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Löschen fehlgeschlagen: {e}")

    return {"message": "Gelöscht", "path": path}


@router.post("/{server_id}/rename")
def rename_path(
    server_id: int,
    path: str = Query(...),
    body: RenameRequest = ...,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Rename a file or directory."""
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
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


@router.post("/{server_id}/extract")
def extract_zip(
    server_id: int,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Extract a zip archive in place."""
    require_server_permission(user, server_id, db, "can_edit_config")
    server = _get_server(server_id, db)
    target = _safe_path(server.install_dir, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archiv nicht gefunden")
    if not target.name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Nur ZIP-Archive werden unterstützt")

    extract_dir = target.parent
    try:
        with zipfile.ZipFile(str(target), "r") as zf:
            for member in zf.namelist():
                member_path = _safe_path(server.install_dir, os.path.join(os.path.dirname(path), member))
                # Extra traversal check for zip entries
                if not str(member_path).startswith(str(Path(server.install_dir).resolve())):
                    raise HTTPException(status_code=403, detail=f"Zip-Eintrag {member} versucht Pfad-Traversal")
            zf.extractall(str(extract_dir))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Ungültiges ZIP-Archiv")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Entpacken fehlgeschlagen: {e}")

    return {"message": "Archiv entpackt", "path": os.path.dirname(path)}
