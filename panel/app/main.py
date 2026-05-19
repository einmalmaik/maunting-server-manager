from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api.account import router as account_router
from .api.actions import router as actions_router
from .api.auth import router as auth_router
from .api.setup import router as setup_router
from .api.autorestart import router as autorestart_router
from .api.backups import router as backups_router
from .api.console import router as console_router
from .api.config_center import router as config_router
from .api.dashboard import router as dashboard_router
from .api.files import router as files_router
from .api.language import router as language_router
from .api.mods import router as mods_router
from .api.rcon import router as rcon_router
from .api.servers import router as servers_router
from .api.users import router as users_router
from .config import get_settings

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Cache index.html content at startup
_index_html_content: str | None = None
if FRONTEND_DIST.exists():
    _index_file = FRONTEND_DIST / "index.html"
    if _index_file.exists():
        _index_html_content = _index_file.read_text(encoding="utf-8")

app = FastAPI(title=settings.app_name, root_path=settings.root_path)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    same_site="lax",
    https_only=settings.https_only,
)

# ── Static assets ─────────────────────────────────────────────────────────────
# Serve the React build assets if the frontend has been built.
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

# ── JSON API router ────────────────────────────────────────────────────────────
api_router = APIRouter(prefix="/api")
api_router.include_router(setup_router, prefix="/setup", tags=["setup"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(account_router, tags=["account"])
api_router.include_router(users_router, tags=["users"])
api_router.include_router(dashboard_router, tags=["dashboard"])
api_router.include_router(backups_router, tags=["backups"])
api_router.include_router(autorestart_router, tags=["autorestart"])
api_router.include_router(actions_router, tags=["actions"])
api_router.include_router(console_router, tags=["console"])
api_router.include_router(config_router, tags=["config"])
api_router.include_router(mods_router, tags=["mods"])
api_router.include_router(rcon_router, tags=["rcon"])
api_router.include_router(servers_router, tags=["servers"])
api_router.include_router(files_router, tags=["files"])
api_router.include_router(language_router, tags=["language"])
app.include_router(api_router)


# ── SPA Catch-All ─────────────────────────────────────────────────────────────
# Must be registered LAST so API routes take priority.

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catchall(full_path: str):
    if _index_html_content is None:
        return HTMLResponse(
            "<html><body>"
            "<h1>Frontend not built.</h1>"
            "<p>Run: <code>cd panel/frontend &amp;&amp; npm install &amp;&amp; npm run build</code></p>"
            "</body></html>",
            status_code=503,
        )
    return HTMLResponse(_index_html_content)
