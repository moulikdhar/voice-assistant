@echo off
rem ─────────────────────────────────────────────────────────────────────────────
rem  Start everything for backend v2 at once:
rem    1. LiveKit server   (the browser's phone line)        ws://localhost:7880
rem    2. Backend v2       (API + AI voice agent, one process) http://localhost:8000
rem    3. Frontend         (Vite)                              http://localhost:5173
rem  Each service opens in its own window. Close a window (or Ctrl+C in it) to stop it.
rem  First run: creates .venv-v2 and installs the frontend packages if they are missing.
rem ─────────────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

if not exist ".env" if not exist "backend-v2\.env" (
    echo [error] No .env file with MISTRAL_API_KEY found. Copy backend-v2\.env.example to .env and add your key.
    pause & exit /b 1
)
if not exist "tools\livekit-server.exe" (
    echo [error] tools\livekit-server.exe is missing.
    echo         Download livekit_1.13.10_windows_amd64.zip from https://github.com/livekit/livekit/releases
    echo         and put livekit-server.exe in the tools folder.
    pause & exit /b 1
)

rem ── stop: refuse to start twice (v1 and v2 use the same ports) ──
for %%P in (7880 8000 5173) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul && (
        echo [error] Port %%P is already in use. Stop the running services first ^(v1 or an earlier start^).
        pause & exit /b 1
    )
)

rem ── first-run setup ──
if not exist ".venv-v2\Scripts\python.exe" (
    echo Creating .venv-v2 and installing backend v2 requirements...
    python -m venv .venv-v2 || (echo [error] Python not found. & pause & exit /b 1)
    ".venv-v2\Scripts\python.exe" -m pip install -r backend-v2\requirements.txt || (pause & exit /b 1)
)
if not exist "frontend\node_modules" (
    echo Installing frontend packages...
    pushd frontend
    call npm ci || (popd & pause & exit /b 1)
    popd
)

rem ── start the three services, each in its own window ──
echo Starting LiveKit server...
start "LiveKit server (7880)" /D "%~dp0tools" livekit-server.exe --dev

echo Starting backend v2...
start "Backend v2 (8000)" /D "%~dp0backend-v2" "%~dp0.venv-v2\Scripts\python.exe" -m uvicorn backend.server:app --port 8000 --log-level warning

echo Starting frontend...
start "Frontend (5173)" /D "%~dp0frontend" cmd /k npm run dev

rem ── wait until the backend answers, then open the app ──
echo Waiting for the backend...
for /L %%i in (1,1,30) do (
    curl -s -o nul http://127.0.0.1:8000/api/health && goto :ready
    timeout /t 1 /nobreak >nul
)
echo [warning] The backend did not answer within 30 s: check the "Backend v2" window.
goto :end

:ready
timeout /t 2 /nobreak >nul
start "" http://localhost:5173
echo.
echo All services are running:  app http://localhost:5173   ^|   API http://localhost:8000
echo Close the three service windows to stop them.

:end
endlocal
