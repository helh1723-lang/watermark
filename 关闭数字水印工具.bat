@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\close_app.ps1"
if errorlevel 1 (
  echo.
  echo Close failed.
  pause
  exit /b 1
)
