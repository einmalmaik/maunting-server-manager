@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
if /I "%~1"=="multi-node" set MSM_DEV_MULTI_NODE=1
title MSM Dev Environment Manager
set DOCKER_AVAILABLE=0
echo ===================================================
echo   MSM Dev Environment Installer and Starter
echo ===================================================
echo.

:: 1. Docker and Postgres check
echo [1/5] Checking Docker and Postgres...
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARN] Docker nicht im PATH gefunden. Postgres-Dev-Container uebersprungen.
    goto :skip_docker
)

docker ps >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARN] Docker-Daemon laeuft nicht. Postgres-Dev-Container uebersprungen.
    goto :skip_docker
)

set DOCKER_AVAILABLE=1

docker inspect msm-panel-postgres-dev >nul 2>nul
if %errorlevel% equ 0 (
    echo Panel-Postgres existiert. Starte...
    docker start msm-panel-postgres-dev
) else (
    echo Erstelle Panel-Postgres auf 127.0.0.1:15434...
    docker run -d --name msm-panel-postgres-dev -p 127.0.0.1:15434:5432 -e POSTGRES_DB=msm -e POSTGRES_USER=msm -e POSTGRES_PASSWORD=msm_dev_pass postgres:17-alpine
)
if %errorlevel% neq 0 (
    echo [FEHLER] Panel-Postgres konnte nicht gestartet werden.
    pause
    exit /b 1
)

set POSTGRES_READY=0
for /L %%I in (1,1,30) do (
    if "!POSTGRES_READY!"=="0" (
        docker exec msm-panel-postgres-dev pg_isready -U msm -d msm >nul 2>nul
        if !errorlevel! equ 0 set POSTGRES_READY=1
        if !errorlevel! neq 0 ping 127.0.0.1 -n 2 >nul
    )
)
if "%POSTGRES_READY%"=="0" (
    echo [FEHLER] Panel-Postgres wurde nicht rechtzeitig bereit.
    pause
    exit /b 1
)

:skip_docker
echo.

:: 2. DIS Sidecar Installation check
echo [2/5] Checking DIS Sidecar...
if not exist "dis-sidecar\node_modules" (
    echo Installiere Node-Dependencies fuer DIS Sidecar...
    cd dis-sidecar
    call npm install
    cd ..
) else (
    echo DIS Sidecar Dependencies sind bereits installiert.
)
echo.

:: 3. Frontend Installation check
echo [3/5] Checking Frontend...
if not exist "frontend\node_modules" (
    echo Installiere Node-Dependencies fuer Frontend...
    cd frontend
    call npm install
    cd ..
) else (
    echo Frontend Dependencies sind bereits installiert.
)
echo.

:: 4. Backend Installation check
echo [4/5] Checking Backend...
if not exist "backend\venv" (
    echo Erstelle Python Virtual Environment venv im Backend...
    cd backend
    python -m venv venv
    echo Installiere Python-Requirements...
    call .\venv\Scripts\pip.exe install -r requirements.txt -r dev-requirements.txt
    cd ..
) else (
    echo Python-Virtualenv ist bereits vorhanden.
)
echo.

:: 5. MSM Agent Installation check
echo [5/5] Checking MSM Agent...
if not exist "msm-agent\venv" (
    echo Erstelle Python Virtual Environment fuer MSM Agent...
    cd msm-agent
    python -m venv venv
    echo Installiere Agent-Requirements...
    call .\venv\Scripts\pip.exe install -r requirements.txt
    cd ..
) else (
    echo MSM Agent Virtualenv ist bereits vorhanden.
)
set PREPARE_ARGS=
if /I "%MSM_DEV_MULTI_NODE%"=="1" set PREPARE_ARGS=--multi-node
msm-agent\venv\Scripts\python.exe scripts\prepare-local-dev.py %PREPARE_ARGS%
if %errorlevel% neq 0 (
    echo [FEHLER] Lokale Agent-Konfiguration konnte nicht vorbereitet werden.
    pause
    exit /b 1
)

if exist "backend\msm.db" (
    echo Migriere lokale Legacy-SQLite-Daten einmalig nach PostgreSQL...
    pushd backend
    venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py --sqlite msm.db
    if !errorlevel! neq 0 (
        popd
        echo [FEHLER] Lokale SQLite-Migration fehlgeschlagen. Die Quelldatei blieb erhalten.
        pause
        exit /b 1
    )
    popd
)
echo Pruefe PostgreSQL-Schema...
pushd backend
venv\Scripts\python.exe scripts\manage_schema.py
if !errorlevel! neq 0 (
    popd
    echo [FEHLER] PostgreSQL-Schema ist nicht bereit. Dienste werden nicht gestartet.
    pause
    exit /b 1
)
if exist msm.db.migration-complete (
    venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py --sqlite msm.db --archive-source
    if !errorlevel! neq 0 (
        popd
        echo [FEHLER] SQLite-Archivierung nach erfolgreicher Migration fehlgeschlagen.
        pause
        exit /b 1
    )
)
popd
echo.

if /I "%MSM_DEV_MULTI_NODE%"=="1" (
    if "%DOCKER_AVAILABLE%"=="1" (
        echo Starte lokales MinIO fuer Backup-Tests...
        docker rm -f msm-minio-dev >nul 2>nul
        docker run -d --name msm-minio-dev --env-file msm-agent\.dev\minio.env -p 9002:9000 -p 9003:9001 -v msm-minio-dev-data:/data quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e server /data --console-address ":9001"
    ) else (
        echo [WARN] MinIO wird ohne laufendes Docker nicht gestartet.
    )
    echo.
)

echo ===================================================
echo   Freigeben belegter Ports...
echo ===================================================
echo.

call :kill_port 9100
call :kill_port 8000
call :kill_port 9000
if /I "%MSM_DEV_MULTI_NODE%"=="1" call :kill_port 9001
call :kill_port 3000

echo ===================================================
echo   Starte dev server...
echo ===================================================
echo.

:: Start DIS Sidecar in a new window
echo Starte DIS Sidecar...
start "MSM - DIS Sidecar" /D "%~dp0dis-sidecar" cmd /k "set NODE_ENV=development&& set MSM_SECRET_KEY=test-secret-key-for-dev-only-32-bytes-long&& set MSM_DIS_SALT=qhCLKLPChabuAqcCOqqxRw==&& node server.mjs"

:: Start Backend in a new window
echo Starte Python Backend (Port 8000)...
start "MSM - FastAPI Backend" /D "%~dp0backend" cmd /k "set NODE_ENV=development&& set MSM_SECRET_KEY=test-secret-key-for-dev-only-32-bytes-long&& set MSM_DIS_SALT=qhCLKLPChabuAqcCOqqxRw==&& set MSM_LOCAL_AGENT_ENV_FILE=../msm-agent/.env&& .\venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000"

:: Start MSM Agent in a new window
echo Starte MSM Agent (Port 9000)...
start "MSM - Agent" /D "%~dp0msm-agent" cmd /k ".\venv\Scripts\python.exe main.py"

if /I "%MSM_DEV_MULTI_NODE%"=="1" (
    echo Starte simulierten Remote-Agent mit TLS auf Port 9001...
    start "MSM - Agent Node 2" /D "%~dp0msm-agent\.dev\node-2" cmd /k "..\..\venv\Scripts\python.exe ..\..\main.py"
)

:: Start Frontend in a new window
echo Starte React Frontend (Port 3000)...
start "MSM - Vite Frontend" /D "%~dp0frontend" cmd /k "npm run dev -- --host localhost --port 3000 --strictPort"

echo.
echo Warte auf die lokalen Dienste...
timeout /t 5 /nobreak >nul
call :check_url http://localhost:3000 "Frontend"
call :check_url http://127.0.0.1:8000/api/health "Backend"
call :check_url http://127.0.0.1:9000/health "Local Agent"
call :check_url http://127.0.0.1:9100/health "DIS Sidecar"
if /I "%MSM_DEV_MULTI_NODE%"=="1" (
    call :check_url_insecure https://127.0.0.1:9001/health "Agent Node 2"
    call :check_url http://127.0.0.1:9002/minio/health/live "MinIO"
)

echo.
echo Alle Komponenten wurden gestartet!
echo - Frontend: http://localhost:3000
echo - Backend API: http://localhost:8000
echo - DIS Sidecar: http://localhost:9100
echo - MSM Agent: http://localhost:9000
if /I "%MSM_DEV_MULTI_NODE%"=="1" (
    echo - Simulierter Node: https://localhost:9001
    echo - MinIO API: http://localhost:9002
    echo - MinIO Console: http://localhost:9003
    echo - Node-Fingerprint: msm-agent\.dev\node-2-fingerprint.txt
    echo - Zugangsdaten bleiben lokal in msm-agent\.env, msm-agent\.dev\node-2\.env und msm-agent\.dev\minio.env
    echo.
    echo Smoke-Test nach dem Start:
    echo   msm-agent\venv\Scripts\python.exe scripts\test-local-multi-node.py
)
echo.
pause
exit /b

:kill_port
set port=%1
for /f %%a in ('powershell.exe -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort %port% -ErrorAction SilentlyContinue ^| Select-Object -ExpandProperty OwningProcess -Unique"') do (
    echo Port %port% ist belegt. Schliesse Prozess %%a...
    taskkill /f /pid %%a >nul 2>&1
)
exit /b

:check_url
curl.exe --fail --silent --show-error --max-time 3 %~1 >nul 2>&1
if errorlevel 1 (
    echo [WARN] %~2 ist nicht bereit: %~1
) else (
    echo [OK] %~2 ist bereit.
)
exit /b

:check_url_insecure
curl.exe --insecure --fail --silent --show-error --max-time 3 %~1 >nul 2>&1
if errorlevel 1 (
    echo [WARN] %~2 ist nicht bereit: %~1
) else (
    echo [OK] %~2 ist bereit.
)
exit /b
