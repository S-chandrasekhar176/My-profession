@echo off
title UltraBot - Trading Bot
chcp 65001 >nul 2>&1

echo ========================================
echo   UltraBot - Starting Frontend + Backend
echo ========================================
echo.

:: ─── Config ───────────────────────────────────
set PROJECT_DIR=%~dp0
set BACKEND_DIR=%PROJECT_DIR%ultrabot-web\backend
set BACKEND_HOST=127.0.0.1
set BACKEND_PORT=8000
set FRONTEND_PORT=3000

:: ─── Step 1: Check Python + venv ──────────────
if not exist "%BACKEND_DIR%\venv\Scripts\activate.bat" (
    echo [ERROR] Python venv not found at:
    echo         %BACKEND_DIR%\venv
    echo.
    echo Run these commands first:
    echo   cd ultrabot-web\backend
    echo   python -m venv venv
    echo   venv\Scripts\activate
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:: ─── Step 2: Install pip deps if needed ───────
if not exist "%BACKEND_DIR%\venv\Lib\site-packages\fastapi" (
    echo [SETUP] Installing Python dependencies...
    call "%BACKEND_DIR%\venv\Scripts\activate.bat"
    pip install -r "%BACKEND_DIR%\requirements.txt"
    echo.
)

:: ─── Step 2b: Fyers SDK needs --no-deps (hard-pins an old aiohttp) ───
if not exist "%BACKEND_DIR%\venv\Lib\site-packages\fyers_apiv3" (
    echo [SETUP] Installing Fyers SDK (--no-deps) and extra dependencies...
    call "%BACKEND_DIR%\venv\Scripts\activate.bat"
    pip install --no-deps -r "%BACKEND_DIR%\requirements-fyers.txt"
    pip install -r "%BACKEND_DIR%\requirements-fyers-extra.txt"
    echo.
)

:: ─── Step 3: Install npm deps if needed ───────
if not exist "%PROJECT_DIR%node_modules\.package-lock.json" (
    echo [SETUP] Installing npm dependencies...
    cd /d "%PROJECT_DIR%"
    call npm install
    echo.
)

echo [1/2] Starting FastAPI backend on port %BACKEND_PORT%...

:: ─── Step 4: Start Backend ─────────────────────
start "UltraBot-Backend" cmd /c "cd /d "%BACKEND_DIR%" && set PYTHONPATH=. && call venv\Scripts\activate.bat && python -m uvicorn app:app --host %BACKEND_HOST% --port %BACKEND_PORT%"

:: ─── Step 5: Wait for backend ─────────────────
echo        Waiting for backend to be ready...
set BACKEND_READY=0
for /l %%i in (1,1,30) do (
    if !BACKEND_READY! equ 0 (
        curl -s http://%BACKEND_HOST%:%BACKEND_PORT%/health >nul 2>&1 && set BACKEND_READY=1
        if !BACKEND_READY! equ 0 timeout /t 1 /nobreak >nul
    )
)
if %BACKEND_READY% equ 0 (
    echo        [WARN] Backend health check timed out - continuing anyway...
) else (
    echo        Backend is ready!
)
echo.

:: ─── Step 6: Start Frontend ────────────────────
echo [2/2] Starting Next.js frontend on port %FRONTEND_PORT%...
echo.
cd /d "%PROJECT_DIR%"

:: Use npx next dev (works with both npm and bun)
npx next dev -p %FRONTEND_PORT%

:: If frontend exits, show message
echo.
echo ========================================
echo   Frontend stopped.
echo   Close the Backend window manually.
echo ========================================
pause
