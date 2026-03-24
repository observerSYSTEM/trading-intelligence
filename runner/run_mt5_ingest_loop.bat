@echo off
setlocal

cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found
  exit /b 1
)

".venv\Scripts\python.exe" "runner\mt5_runner_ingest.py"
set "CODE=%ERRORLEVEL%"

endlocal & exit /b %CODE%
