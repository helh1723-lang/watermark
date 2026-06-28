@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_web.ps1" -OpenBrowser
if errorlevel 1 (
  echo.
  echo Startup failed. Please run setup_env.bat first.
  pause
  exit /b 1
)
