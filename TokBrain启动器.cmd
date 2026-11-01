@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Single entry point for TokBrain. See scripts\menu.ps1 for the menu.
rem Usage: TokBrain.cmd [status, install, start, start-bg, stop, restart, dev, mcp, open]
rem        add -WithMcp / -WithDev / -WithoutBilling for the install action.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\menu.ps1" %*

set "menu_exit_code=%ERRORLEVEL%"
if not "%menu_exit_code%"=="0" (
    echo.
    echo [TokBrain] exit code: %menu_exit_code%
)
exit /b %menu_exit_code%
