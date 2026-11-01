@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0.."

rem ── 读取 mcp_server\mcp.env（没有则用默认值）──────────────────
if exist "mcp_server\mcp.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("mcp_server\mcp.env") do (
        set "%%a=%%b"
    )
)

if not defined TOKBRAIN_MCP_PORT set "TOKBRAIN_MCP_PORT=8010"
if not defined TOKBRAIN_MCP_HOST set "TOKBRAIN_MCP_HOST=127.0.0.1"

echo.
echo   ============================================
echo    TokBrain 只读 MCP 服务
echo   ============================================
echo    监听地址 : http://%TOKBRAIN_MCP_HOST%:%TOKBRAIN_MCP_PORT%/mcp
if defined TOKBRAIN_MCP_TOKEN (
    echo    访问令牌 : 已配置
) else (
    echo    访问令牌 : 未配置 ^(仅允许本机回环使用^)
)
echo.
echo    供 Hermes 使用的完整地址请填本机局域网 IP:
echo      http://本机IP:%TOKBRAIN_MCP_PORT%/mcp
echo.
echo    关闭此窗口即停止服务。 Ctrl+C 也可中断。
echo   ============================================
echo.

".venv-mcp\Scripts\python.exe" "mcp_server\tokbrain_mcp.py"

echo.
echo   服务已退出。
pause
