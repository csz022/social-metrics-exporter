@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed or not on PATH.
  echo Install uv first:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
  )
)

echo Syncing Python dependencies...
uv sync
if errorlevel 1 (
  pause
  exit /b 1
)

echo Installing or checking Playwright Chromium...
uv run playwright install chromium
if errorlevel 1 (
  pause
  exit /b 1
)

if "%GUI_PORT%"=="" set GUI_PORT=5001
echo Starting Social Metrics at http://127.0.0.1:%GUI_PORT%
start "" http://127.0.0.1:%GUI_PORT%
uv run python gui_app.py --port %GUI_PORT% --open-browser
pause
