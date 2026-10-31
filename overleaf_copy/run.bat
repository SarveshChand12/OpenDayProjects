@echo off
REM Start Overleaf Copy (Windows). Creates a virtualenv on first run.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
.venv\Scripts\python app.py %*
