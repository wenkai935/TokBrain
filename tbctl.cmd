@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 控制面本身零第三方依赖，任何 Python 3.10+ 都能跑；
rem 但 search 子命令需要 SQLAlchemy + app.*，所以优先用主环境 .venv。
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist ".venv-mcp\Scripts\python.exe" set "PY=.venv-mcp\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" -m tokbrain_control %*
exit /b %ERRORLEVEL%
