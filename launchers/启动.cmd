@echo off
setlocal
cd /d "%~dp0.."
if not exist "%~dp0..\data\logs" mkdir "%~dp0..\data\logs"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& '%~dp0start.ps1' *> '%~dp0..\data\logs\startup-console.log'"
exit /b 0
