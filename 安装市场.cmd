@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-market.ps1" %*
if errorlevel 1 (
  echo Installation failed. Please keep the error details for your administrator.
  pause
  exit /b 1
)
pause
