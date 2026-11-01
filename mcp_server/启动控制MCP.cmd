@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

set "PY=.venv-mcp\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 找不到 .venv-mcp\Scripts\python.exe
    echo        8011 控制面依赖 mcp 2.3.0，与主服务 .venv 的 pydantic 版本冲突，
    echo        所以必须用独立环境：先运行 setup.cmd -WithMcp 或按 requirements-mcp.txt 安装。
    pause
    exit /b 1
)

if not exist "mcp_server\control.env" (
    echo [提示] 还没有 mcp_server\control.env
    echo        先复制模板：
    echo            copy mcp_server\control.env.example mcp_server\control.env
    echo        然后填入 TOKBRAIN_CONTROL_TOKEN（至少 32 位十六进制）：
    echo            .venv-mcp\Scripts\python.exe -c "import secrets;print(secrets.token_hex(16))"
    pause
    exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%a in ("mcp_server\control.env") do set "%%a=%%b"

if not defined TOKBRAIN_CONTROL_ENABLE set "TOKBRAIN_CONTROL_ENABLE=1"
if not defined TOKBRAIN_CONTROL_PORT set "TOKBRAIN_CONTROL_PORT=8011"
if not defined TOKBRAIN_CONTROL_API_BASE set "TOKBRAIN_CONTROL_API_BASE=http://127.0.0.1:8001"

echo.
echo ============================================================
echo  TokBrain 控制 MCP  ^(8011^)
echo ============================================================

if not defined TOKBRAIN_CONTROL_TOKEN (
    echo  [警告] 未配置 TOKBRAIN_CONTROL_TOKEN
    echo         非回环监听会被直接拒绝启动。本机自测可把 HOST 改回 127.0.0.1。
) else (
    echo  令牌：已配置
)

rem 三个令牌互不相同，避免一处泄漏连带全部失守
if defined TOKBRAIN_MCP_TOKEN if "!TOKBRAIN_MCP_TOKEN!"=="!TOKBRAIN_CONTROL_TOKEN!" (
    echo  [警告] 控制令牌与 8010 只读令牌相同，建议换成不同的值
)
if exist "data\integration_token.txt" (
    set /p EXT_TOKEN=<"data\integration_token.txt"
    if "!EXT_TOKEN!"=="!TOKBRAIN_CONTROL_TOKEN!" (
        echo  [警告] 控制令牌与外部导入令牌相同，建议换成不同的值
    )
)

echo.
"%PY%" "mcp_server\mcp_control.py"
echo.
echo 控制 MCP 已退出（退出码 %ERRORLEVEL%）
pause
