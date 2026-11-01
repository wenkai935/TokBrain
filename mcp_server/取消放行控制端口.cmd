@echo off
chcp 65001 >nul
setlocal
set "RULE=TokBrain Control MCP 8011"

net session >nul 2>&1
if errorlevel 1 (
    echo 需要管理员权限，正在请求提权…
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
if errorlevel 1 (
    echo [提示] 没有找到名为 "%RULE%" 的规则，可能本来就没加过。
) else (
    echo [完成] 已删除规则：%RULE%
)
echo.
echo 现在只有本机 127.0.0.1 能访问控制 MCP，NAS 上的 Hermes 连不上。
pause
