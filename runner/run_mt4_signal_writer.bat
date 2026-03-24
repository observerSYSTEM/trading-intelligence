@echo off
setlocal

REM Run from repository root regardless of where task is launched
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found
  exit /b 1
)

".venv\Scripts\python.exe" "runner\mt4_signal_writer.py"
set "CODE=%ERRORLEVEL%"

endlocal & exit /b %CODE%
