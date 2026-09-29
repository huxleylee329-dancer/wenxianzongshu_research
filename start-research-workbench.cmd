@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title GPT Researcher Workbench

set "APP_URL=http://127.0.0.1:8000/"
set "PYTHON=%CD%\.venv\Scripts\python.exe"

echo.
echo  GPT Researcher Workbench
echo  ========================
echo.

if not exist "%PYTHON%" (
  echo [ERROR] Project virtual environment was not found:
  echo         %PYTHON%
  echo.
  echo Create .venv and install the project dependencies first.
  pause
  exit /b 1
)

if not exist "%CD%\.env" (
  echo [ERROR] Project configuration file was not found: %CD%\.env
  echo Create .env from .env.example and add the required API keys.
  pause
  exit /b 1
)

if /i "%~1"=="--check" (
  echo [OK] Python: %PYTHON%
  echo [OK] Environment: %CD%\.env
  echo [OK] Launcher preflight passed.
  exit /b 0
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%CD%;%CD%\backend"

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri '%APP_URL%'; if ($r.StatusCode -eq 200 -and $r.Content -match 'GPT Researcher') { exit 0 } } catch {}; exit 1"
  if not errorlevel 1 (
    echo [OK] GPT Researcher is already running on port 8000.
    echo [OPEN] %APP_URL%
    start "" "%APP_URL%"
    echo.
    echo You may close this window. The existing server will keep running.
    pause
    exit /b 0
  )

  echo [ERROR] Port 8000 is already used by another application.
  echo Close that application, then double-click this launcher again.
  pause
  exit /b 1
)

echo [START] Starting the local service at %APP_URL%
echo [INFO] The browser will open when the service is ready.
echo [INFO] Keep this window open. Press Ctrl+C to stop the service.
echo.

if /i not "%~1"=="--no-browser" (
  start "" /b powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command ^
    "$deadline = (Get-Date).AddSeconds(60); while ((Get-Date) -lt $deadline) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%APP_URL%'; if ($r.StatusCode -eq 200) { Start-Process '%APP_URL%'; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"
)

"%PYTHON%" -m uvicorn backend.server.app:app --host 127.0.0.1 --port 8000 --env-file ".env"
set "SERVER_EXIT=%ERRORLEVEL%"

echo.
if "%SERVER_EXIT%"=="0" (
  echo [STOP] The service has stopped.
) else (
  echo [ERROR] The service exited with code %SERVER_EXIT%.
  echo Review the log above for details.
)
echo.
pause
exit /b %SERVER_EXIT%
