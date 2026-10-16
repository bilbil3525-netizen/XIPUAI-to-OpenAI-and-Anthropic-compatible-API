@echo off
REM ============================================================
REM  Minimal launcher: just run the relay in the foreground.
REM  Used by the autostart scheduled task (see tools\install_autostart.ps1).
REM ============================================================
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
echo ==== start %DATE% %TIME% ==== > relay_out.log
.venv\Scripts\python.exe -m uvicorn adapter:app --host 0.0.0.0 --port 8899 >> relay_out.log 2>&1
