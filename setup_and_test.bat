@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Dialogue Agent Safety Demo - Setup and Test
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo Dialogue Agent Safety Demo - Setup and Test
echo ============================================================
echo.

set "PYTHON_CMD="

where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD goto NO_PYTHON

echo [1/5] Checking Python version...
%PYTHON_CMD% -c "import sys; print(sys.version); raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto BAD_VERSION

echo.
echo [2/5] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto VENV_FAILED
) else (
    echo Existing .venv found. Reusing it.
)

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "logs" mkdir logs

echo.
echo [3/5] Running unit tests...
"%VENV_PYTHON%" -m unittest discover -s tests -v > "logs\test_output.txt" 2>&1
set "TEST_CODE=%ERRORLEVEL%"
type "logs\test_output.txt"
if not "%TEST_CODE%"=="0" goto TEST_FAILED

echo.
echo [4/5] Running demo...
"%VENV_PYTHON%" run_demo.py > "logs\demo_output.txt" 2>&1
set "DEMO_CODE=%ERRORLEVEL%"
type "logs\demo_output.txt"
if not "%DEMO_CODE%"=="0" goto DEMO_FAILED

echo.
echo [5/6] Running performance test...
"%VENV_PYTHON%" tests\perf_test.py > "logs\perf_output.txt" 2>&1
set "PERF_CODE=%ERRORLEVEL%"
type "logs\perf_output.txt"
if not "%PERF_CODE%"=="0" goto PERF_FAILED

echo.
echo [6/6] Running trace demo (writes logs\trace_demo_output.*)...
"%VENV_PYTHON%" run_trace_demo.py > "logs\trace_demo_output.txt" 2>&1
echo Trace text log: %CD%\logs\trace_demo_output.txt
echo Trace JSON log: %CD%\logs\trace_demo_output.json

echo.
echo ============================================================
echo SUCCESS: setup, tests, demo, trace demo, and performance test completed.
echo Test log: %CD%\logs\test_output.txt
echo Demo log: %CD%\logs\demo_output.txt
echo Perf log: %CD%\logs\perf_output.txt
echo Trace  : %CD%\logs\trace_demo_output.txt / .json
echo ============================================================
echo.
pause
exit /b 0

:NO_PYTHON
echo ERROR: Python was not found.
echo Install Python 3.10 or newer and enable "Add Python to PATH".
echo https://www.python.org/downloads/
goto FAILED

:BAD_VERSION
echo ERROR: Python 3.10 or newer is required.
goto FAILED

:VENV_FAILED
echo ERROR: Failed to create the virtual environment.
goto FAILED

:TEST_FAILED
echo.
echo ERROR: Unit tests failed.
echo See: %CD%\logs\test_output.txt
goto FAILED

:DEMO_FAILED
echo.
echo ERROR: Demo execution failed.
echo See: %CD%\logs\demo_output.txt
goto FAILED

:PERF_FAILED
echo.
echo ERROR: Performance test failed.
echo See: %CD%\logs\perf_output.txt
goto FAILED

:FAILED
echo.
pause
exit /b 1