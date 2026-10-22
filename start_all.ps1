# Mistral Voice Assistant Launcher (PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "  Starting Mistral Voice Assistant Services (PowerShell) " -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. LiveKit Server
Write-Host "[1/3] Starting LiveKit Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; Write-Host '--- LiveKit Server ---' -ForegroundColor Green; .\tools\livekit-server.exe --dev"

# Wait 2 seconds for LiveKit to bind
Start-Sleep -Seconds 2

# 2. Backend Agent & Server
Write-Host "[2/3] Starting Backend Agent & Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; Write-Host '--- Backend Server & Agent ---' -ForegroundColor Green; if (Test-Path '.\.venv\Scripts\Activate.ps1') { . .\.venv\Scripts\Activate.ps1 }; python -m backend.agent download-files; python run.py"

# Wait 1 second
Start-Sleep -Seconds 1

# 3. Frontend Dev Server
Write-Host "[3/3] Starting Frontend Dev Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\frontend'; Write-Host '--- Frontend Dev Server ---' -ForegroundColor Green; npm run dev"

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "  All 3 services launched in separate terminal windows!  " -ForegroundColor Green
Write-Host "  - LiveKit Server : http://localhost:7880               " -ForegroundColor White
Write-Host "  - Backend API    : http://localhost:8000               " -ForegroundColor White
Write-Host "  - Frontend       : http://localhost:5173               " -ForegroundColor White
Write-Host "=========================================================" -ForegroundColor Green
