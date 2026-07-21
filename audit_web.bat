@echo off
REM ============================================================
REM  v4.1.1 Audit Web Launcher (single-page visualization)
REM  - ASCII only, CRLF line endings, no UTF-8 BOM
REM  - 6-step flow: check Python -> port probe -> set cwd ->
REM    start server -> wait for health -> open browser -> pause
REM ============================================================

setlocal

REM 1. Check Python 3.10+
where py >nul 2>&1
if not errorlevel 1 (
  set PY=py -3
) else (
  where python >nul 2>&1
  if not errorlevel 1 (
    set PY=python
  ) else (
    echo [ERROR] Python is not on PATH.
    pause
    exit /b 1
  )
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.10 or newer is required.
  pause
  exit /b 1
)

REM 2. Switch to the script directory.
cd /d "%~dp0"

REM 3. Pick a port (default 8765). Honor --port CLI override.
set PORT=8765
set ARG=%1
if defined ARG (
  set PORT=%ARG:--port=%
)

REM 4. Prefer bundled .venv.
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
)

REM 5. Start the server (it auto-opens the browser after a brief delay).
echo [audit_web] starting server on http://127.0.0.1:%PORT%/
%PY% audit_web.py --port %PORT%
set RC=%errorlevel%

echo.
echo [audit_web] exited with code %RC%
pause
exit /b %RC%
