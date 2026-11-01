@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& '%~dp0start.ps1' *> '%~dp0data\logs\startup-console.log'"
exit /b 0
