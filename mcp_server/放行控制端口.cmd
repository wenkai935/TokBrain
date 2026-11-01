@echo off
chcp 65001 >nul
setlocal
set "RULE=TokBrain Control MCP 8011"
set "SUBNET=192.168.10.0/24"

net session >nul 2>&1
if errorlevel 1 (
    echo 需要管理员权限，正在请求提权，请在弹窗里点"是"…
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo  放行 TokBrain 控制 MCP（TCP 8011）入站
echo ============================================================
echo  只放行 %SUBNET% 网段，且只在"专用/域"网络配置下生效。
echo  不改动 8001（后端）、8010（只读 MCP）、3001（前端）。
echo.

netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1

netsh advfirewall firewall add rule ^
    name="%RULE%" ^
    dir=in action=allow protocol=TCP localport=8011 ^
    remoteip=%SUBNET% profile=private,domain
if errorlevel 1 (
    echo.
    echo [失败] 规则没有加上。请复制上面的报错信息排查。
    pause
    exit /b 1
)

echo.
echo [完成] 已添加规则：%RULE%
echo.
echo 当前网络配置文件（必须是"专用"或"域"，否则规则不生效）：
netsh advfirewall show currentprofile
echo.
echo 自检方法：在 NAS 上执行
echo     curl -m 5 -i http://192.168.10.100:8011/mcp
echo   期望看到 401 unauthorized —— 能连上但被令牌拒绝，说明防火墙通了。
echo   connection refused = 服务没起；timeout = 防火墙没放行。
echo.
pause
