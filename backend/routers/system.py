import psutil

from fastapi import APIRouter, Depends, HTTPException

from models import User
from routers.auth import get_current_user

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/resources")
def system_resources(user: User = Depends(get_current_user)) -> dict:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner")

    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        "cpu_percent": cpu,
        "cpu_count": psutil.cpu_count(),
        "ram_total_mb": memory.total // (1024 * 1024),
        "ram_used_mb": memory.used // (1024 * 1024),
        "ram_free_mb": memory.available // (1024 * 1024),
        "ram_percent": memory.percent,
        "disk_total_gb": disk.total // (1024 * 1024 * 1024),
        "disk_used_gb": disk.used // (1024 * 1024 * 1024),
        "disk_free_gb": disk.free // (1024 * 1024 * 1024),
        "disk_percent": disk.percent,
    }


@router.get("/games")
def supported_games(user: User = Depends(get_current_user)) -> list[dict]:
    # Wird später dynamisch aus dem Plugin-System geladen
    return [
        {
            "id": "conan_exiles_ue5",
            "name": "Conan Exiles (UE5)",
            "platform": "linux",
            "mod_support": True,
        },
        {
            "id": "dayz",
            "name": "DayZ",
            "platform": "linux",
            "mod_support": True,
        },
    ]
