@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Paper virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create the virtual environment. Make sure Python is installed and on PATH.
        pause
        exit /b 1
    )
)

echo Installing or checking Paper dependencies...
".venv\Scripts\python.exe" -m pip install -r api\requirements.txt
if errorlevel 1 (
    echo Could not install dependencies.
    pause
    exit /b 1
)

echo Starting Paper at http://127.0.0.1:8000
set "PAPER_TELEMETRY_FILE=output\telemetry\events.jsonl"
echo Local telemetry: http://127.0.0.1:8000/telemetry
echo Press Ctrl+C to stop the server.
".venv\Scripts\python.exe" -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8000

endlocal
