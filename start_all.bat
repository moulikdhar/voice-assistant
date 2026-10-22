@echo off
title Mistral Voice Assistant Launcher
setlocal EnableDelayedExpansion

:: Set root working directory
cd /d "%~dp0"

echo =========================================================
echo   Starting Mistral Voice Assistant Services
echo =========================================================
echo.

:: 1. LiveKit Server
echo [1/3] Starting LiveKit Server...
start "1. LiveKit Server" cmd /k "cd /d ""%~dp0"" && tools\livekit-server.exe --dev"

:: Wait 2 seconds for LiveKit to bind its ports
timeout /t 2 /nobreak >nul

:: 2. Backend Agent & Server
echo [2/3] Starting Backend Agent & Server...
start "2. Backend Server" cmd /k "cd /d ""%~dp0"" && (if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat) && python -m backend.agent download-files && python run.py"

:: Wait 1 second
timeout /t 1 /nobreak >nul

:: 3. Frontend Dev Server
echo [3/3] Starting Frontend Dev Server...
start "3. Frontend" cmd /k "cd /d ""%~dp0frontend"" && npm run dev"

echo.
echo =========================================================
echo   All 3 services launched in separate terminal windows!
echo   - LiveKit Server : http://localhost:7880
echo   - Backend API    : http://localhost:8000
echo   - Frontend       : http://localhost:5173
echo =========================================================
echo.
pause
