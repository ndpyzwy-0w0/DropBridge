@echo off
setlocal EnableExtensions
title DropBridge - Restore Realtek Wi-Fi driver
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
  exit /b
)

echo ----------------------------------------------------------------
echo  DropBridge rollback: restore original Realtek Wi-Fi driver
echo ----------------------------------------------------------------
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Restore-WiFi.ps1"
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo Done. The adapter has been restored to its Windows Wi-Fi driver.
) else (
  echo Finished with exit code %CODE%. See setup\restore.log for details.
)
echo.
pause
exit /b %CODE%
