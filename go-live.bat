@echo off
REM ============================================================
REM  XJTLU Junmou AI relay - one-click launcher
REM  Refresh token, then (re)start the OpenAI/Anthropic compatible service.
REM
REM  Adapted for the 2026 platform redesign. Upstream auth.py no longer works.
REM ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
title XJTLU Junmou AI Relay

REM Resolve our own directory - no absolute paths hardcoded.
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

set "PY=%DIR%\.venv\Scripts\python.exe"
set "PORT=8899"
set "PROBE=%TEMP%\xjtlu_probe.txt"

echo ============================================================
echo  XJTLU Junmou AI Relay
echo ============================================================
echo.

if not exist "%PY%" (
  echo [X] Python venv not found: %PY%
  echo     Create it first:
  echo       python -m venv .venv
  echo       .venv\Scripts\pip install -r requirements-deployed.txt
  pause
  exit /b 1
)

echo [1/5] Checking the campus tunnel (aTrust / campus network)...
curl.exe -s --max-time 15 -X POST "https://xipuai.xjtlu.edu.cn/jmapi/api/chat/session?sf_request_type=ajax" -H "jm-token: probe" -o "%PROBE%" 2>nul
findstr /i "aTrust 2.0" "%PROBE%" >nul 2>&1
if not errorlevel 1 (
  echo   [X] Blocked by the aTrust gateway.
  echo       Connect aTrust / campus network first, then rerun.
  pause
  exit /b 2
)
echo   [OK] gateway reachable
echo.

echo [2/5] Refreshing jm-token (headless SSO login)...
cd /d "%DIR%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%PY%" auth_v4.py
findstr /b "JM_TOKEN=" "%DIR%\.env" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [X] Token refresh failed.
  echo       Manual fallback: "%PY%" set_tokens.py
  pause
  exit /b 3
)
echo   [OK] token refreshed
echo.

echo [3/5] Allowing firewall port %PORT%...
netsh advfirewall firewall show rule name="XJTLU Relay" >nul 2>&1
if errorlevel 1 (
  netsh advfirewall firewall add rule name="XJTLU Relay" dir=in action=allow protocol=TCP localport=%PORT% >nul 2>&1
  echo   [OK] firewall rule added
) else (
  echo   [OK] firewall rule already present
)
echo.

echo [4/5] Restarting service...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
timeout /t 2 >nul
start "XJTLU Relay" /min "%PY%" -m uvicorn adapter:app --host 0.0.0.0 --port %PORT% >> "%DIR%\relay_out.log" 2>&1
timeout /t 10 >nul

echo [5/5] Verifying...
curl.exe -s --max-time 15 "http://127.0.0.1:%PORT%/v1/models" -o "%TEMP%\xjtlu_models.txt" 2>nul
findstr /i "gpt-6-astra" "%TEMP%\xjtlu_models.txt" >nul 2>&1
if errorlevel 1 (
  echo   [X] Service not ready - see relay_out.log
  pause
  exit /b 4
)
echo   [OK] service is running
echo.
echo ============================================================
echo  DONE
echo.
echo    Base URL : http://^<this-host-ip^>:%PORT%/v1
echo    API Key  : any string, e.g. xjtlu
echo.
echo  Endpoints:
echo    POST /v1/chat/completions   (OpenAI format)
echo    POST /v1/messages           (Anthropic format - Claude Code)
echo    GET  /v1/models
echo ============================================================
echo.
pause
