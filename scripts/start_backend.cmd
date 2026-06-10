@echo off
setlocal

set "REPO_ROOT=%~dp0.."
set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Root Python virtualenv not found: %PYTHON_EXE%
  echo Run: python -m venv .venv
  exit /b 1
)

echo Using backend Python: %PYTHON_EXE%
"%PYTHON_EXE%" --version
"%PYTHON_EXE%" -m uvicorn app.main:app --app-dir "%REPO_ROOT%\backend" --reload --host 127.0.0.1 --port 8000
