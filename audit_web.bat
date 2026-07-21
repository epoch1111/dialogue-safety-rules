@echo off
REM ============================================================
REM  Audit Web Launcher (single-page visualization)
REM  - ASCII only, CRLF line endings, no UTF-8 BOM
REM  - 6-step flow: check Python -> port probe -> set cwd ->
REM    start server -> wait for health -> open browser -> pause
REM ============================================================

setlocal

REM 1. Switch to the script directory and prefer its bundled environment.
cd /d "%~dp0"

if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  where python >nul 2>&1
  if not errorlevel 1 (
    set PY=python
  ) else (
    where py >nul 2>&1
    if not errorlevel 1 (
      set PY=py -3
    ) else (
      echo [ERROR] Python is not on PATH.
      pause
      exit /b 1
    )
  )
)

REM The stdlib-only demo is verified with Python 3.9+.
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.9 or newer is required.
  pause
  exit /b 1
)

REM 3. Pick a port. Honor --port CLI override; otherwise use the first
REM    available localhost port in 8765-8785 so an old demo does not
REM    accidentally keep the browser connected to stale code.
set PORT=8765
set ARG=%1
if defined ARG (
  set PORT=%ARG:--port=%
  goto :port_ready
)

for /L %%P in (8765,1,8785) do (
  netstat -ano | findstr /r /c:":%%P .*LISTENING" >nul
  if errorlevel 1 (
    set PORT=%%P
    goto :port_ready
  )
)

echo [ERROR] No free port found in 8765-8785.
echo [ERROR] Stop an existing audit web server or run audit_web.bat --port N.
pause
exit /b 1

:port_ready

REM 4. Start the server (it auto-opens the browser after a brief delay).
echo [audit_web] starting CURRENT checkout on http://127.0.0.1:%PORT%/
%PY% audit_web.py --port %PORT%
set RC=%errorlevel%

echo.
echo [audit_web] exited with code %RC%
pause
exit /b %RC%
