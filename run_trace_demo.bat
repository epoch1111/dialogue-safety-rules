@echo off
REM ============================================================
REM  v4.1.1 Trace Demo Launcher
REM  - ASCII only, CRLF line endings, no UTF-8 BOM
REM  - 6-step flow: check Python -> set cwd -> run -> print -> pause
REM ============================================================

setlocal

REM 1. Check Python 3.10+
python --version >/dev/null 2>&1
if errorlevel 1 (
  echo [ERROR] Python is not installed or not on PATH.
  pause
  exit /b 1
)

REM 2. Switch to the script's own directory so relative paths work.
cd /d "%~dp0"

REM 3. Prefer the bundled .venv if it exists; fall back to system Python.
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)

REM 4. Run the trace demo.
%PY% run_trace_demo.py
set RC=%errorlevel%

REM 5. Tell the user where the logs went.
echo.
echo Logs written to:
echo   logs	race_demo_output.txt
echo   logs	race_demo_output.json
echo.

REM 6. Pause so the window stays open.
pause
exit /b %RC%
