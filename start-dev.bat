@echo off
title MSM Dev Environment Manager
echo ===================================================
echo   MSM Dev Environment Installer and Starter
echo ===================================================
echo.

:: 1. Docker and Postgres check
echo [1/4] Checking Docker and Postgres...
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

docker inspect msm-postgres-dev >nul 2>nul
if %errorlevel% equ 0 (
    echo Postgres-Container msm-postgres-dev existiert. Starte...
    docker start msm-postgres-dev
) else (
    echo Erstelle neuen Postgres-Container msm-postgres-dev...
    docker run -d --name msm-postgres-dev -p 5432:5432 -e POSTGRES_DB=msm -e POSTGRES_USER=msm -e POSTGRES_PASSWORD=msm_dev_pass postgres:15
)

:skip_docker
echo.

:: 2. DIS Sidecar Installation check
echo [2/4] Checking DIS Sidecar...
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
echo [3/4] Checking Frontend...
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
if not exist "msm-agent\.env" (
    echo Erstelle msm-agent\.env fuer Dev...
    (
        echo MSM_AGENT_TOKEN=dev-agent-token-change-me
        echo MSM_AGENT_HOST=127.0.0.1
        echo MSM_AGENT_PORT=9000
        echo MSM_SERVERS_DIR=./servers
        echo MSM_AGENT_LOG_LEVEL=INFO
    ) > msm-agent\.env
)
echo.

echo ===================================================
echo   Starte dev server...
echo ===================================================
echo.

:: Start DIS Sidecar in a new window
echo Starte DIS Sidecar...
start "MSM - DIS Sidecar" cmd /k "cd dis-sidecar && set NODE_ENV=development && set MSM_SECRET_KEY=test-secret-key-for-dev-only-32-bytes-long!! && set MSM_DIS_SALT=qhCLKLPChabuAqcCOqqxRw== && node server.mjs"

:: Start Backend in a new window
echo Starte Python Backend (Port 8000)...
start "MSM - FastAPI Backend" cmd /k "cd backend && .\venv\Scripts\activate && set NODE_ENV=development && set MSM_SECRET_KEY=test-secret-key-for-dev-only-32-bytes-long!! && set MSM_DIS_SALT=qhCLKLPChabuAqcCOqqxRw== && uvicorn main:app --reload --port 8000"

:: Start MSM Agent in a new window
echo Starte MSM Agent (Port 9000)...
start "MSM - Agent" cmd /k "cd msm-agent && .\venv\Scripts\activate && python main.py"

:: Start Frontend in a new window
echo Starte React Frontend (Port 3000)...
start "MSM - Vite Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo Alle Komponenten wurden gestartet!
echo - Frontend: http://localhost:3000
echo - Backend API: http://localhost:8000
echo - DIS Sidecar: http://localhost:9100
echo - MSM Agent: http://localhost:9000
echo.
pause
