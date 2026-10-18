@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title MSM Dev (Docker-Backend + Vite-Frontend)

echo ===================================================
echo   MSM Dev: Backend im Docker (Linux) + Frontend nativ
echo ===================================================
echo.

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [FEHLER] Docker nicht im PATH. Docker Desktop starten.
    pause
    exit /b 1
)
docker ps >nul 2>nul
if %errorlevel% neq 0 (
    echo [FEHLER] Docker-Daemon laeuft nicht. Docker Desktop starten.
    pause
    exit /b 1
)

echo [1/4] Dev-Envs pruefen...
python scripts\setup-dev-env.py
if %errorlevel% neq 0 (
    echo [FEHLER] setup-dev-env.py ist gescheitert.
    pause
    exit /b 1
)
echo.

echo [2/4] Docker-Stack bauen und starten (Backend + Sidecars)...
docker compose -f docker-compose.dev.yml up -d --build
if %errorlevel% neq 0 (
    echo [FEHLER] docker compose up ist gescheitert.
    pause
    exit /b 1
)
echo.

echo [3/4] Warte auf Backend + Sidecars (max. 120s)...
set READY=0
for /L %%I in (1,1,24) do (
    curl.exe --fail --silent --max-time 3 http://127.0.0.1:8000/api/health >nul 2>nul
    if !errorlevel! equ 0 (
        set READY=1
        goto :backend_ready
    )
    ping 127.0.0.1 -n 6 >nul
)
:backend_ready
if "%READY%"=="0" (
    echo [WARN] Backend meldet sich noch nicht. Logs:
    docker compose -f docker-compose.dev.yml logs --tail=40 backend
) else (
    echo [OK] Backend bereit: http://localhost:8000
)
curl.exe --fail --silent --max-time 3 http://127.0.0.1:9100/health >nul 2>nul
if %errorlevel% equ 0 ( echo [OK] DIS-Sidecar bereit. ) else ( echo [WARN] DIS-Sidecar noch nicht bereit. )
echo.

echo [4/4] Frontend-Dev starten (nativ, Port 3000)...
if not exist "frontend\node_modules" (
    echo Installiere Frontend-Dependencies...
    cd frontend
    call npm install
    cd ..
)
start "MSM - Vite Frontend" /D "%~dp0frontend" cmd /k "npm run dev -- --host localhost --port 3000 --strictPort"
echo.
echo Warte kurz auf Vite...
ping 127.0.0.1 -n 6 >nul
curl.exe --fail --silent --max-time 3 http://localhost:3000 >nul 2>nul
if %errorlevel% equ 0 ( echo [OK] Frontend bereit: http://localhost:3000 ) else ( echo [WARN] Frontend braucht noch einen Moment: http://localhost:3000 )

echo.
echo Fertig! URLs (alle localhost):
echo - Frontend:  http://localhost:3000
echo - Backend:   http://localhost:8000  ^(Health: /api/health^)
echo - DIS:       http://localhost:9100  ^(/health^)
echo - SearXNG:   http://localhost:8888
echo - LiveKit:   ws://localhost:7880
echo - Panel-DB:  127.0.0.1:15434
echo.
echo Nuetzlich:
echo   docker compose -f docker-compose.dev.yml logs -f backend
echo   docker compose -f docker-compose.dev.yml ps
echo   docker compose -f docker-compose.dev.yml down
echo.
pause
