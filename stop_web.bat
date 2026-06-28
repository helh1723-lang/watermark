@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_web.ps1"
if errorlevel 1 (
  echo.
  echo Close failed.
  pause
  exit /b 1
)
