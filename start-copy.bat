@echo off
rem Starts the Xpense copy at http://127.0.0.1:5056 (the original keeps running on 5055).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
set XPENSE_PREVIEW_PORT=5056
set PYTHONDONTWRITEBYTECODE=1
echo Xpense copy: http://127.0.0.1:5056/
".venv\Scripts\python.exe" tools\preview.py
