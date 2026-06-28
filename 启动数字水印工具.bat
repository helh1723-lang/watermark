@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Run: python run_gui.py
  pause
  exit /b 1
)
