from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import settings
from database import engine, Base
from routers import (
    auth_router,
    admin_router,
    servers_router,
    backups_router,
    mods_router,
    config_editor_router,
    system_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    yield
    # Shutdown


app = FastAPI(
    title=settings.app_name,
    description="Maunting Server Manager — Universeller Game Server Manager",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Produktion: auf Panel-URL einschränken
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(servers_router)
app.include_router(backups_router)
app.include_router(mods_router)
app.include_router(config_editor_router)
app.include_router(system_router)

# Static Frontend (nur in Produktion)
import os
if os.path.exists("/opt/msm/frontend/dist"):
    app.mount("/", StaticFiles(directory="/opt/msm/frontend/dist", html=True), name="frontend")


@app.get("/")
def root():
    return {"name": settings.app_name, "version": "1.0.0"}


@app.get("/api/health")
def health():
    return {"status": "ok"}
