@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_env.ps1"
if errorlevel 1 (
  echo.
  echo Environment setup failed.
  pause
  exit /b 1
)
echo.
echo Environment setup completed.
pause
