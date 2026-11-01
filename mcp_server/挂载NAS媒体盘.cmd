@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ============================================================
echo  把 NAS 媒体目录映射成本机盘符
echo ============================================================
echo  说明：Hermes 在 NAS 上，控制 MCP 在 Windows 上。
echo        只有"这台 Windows 能读到的路径"才能被 tb_import_files 上传。
echo        映射之后，Hermes 传 Z:\videos\a.mp4 就能用。
echo.
echo  注意：只映射**媒体文件目录**。绝对不要把 NAS 上的 SQLite（data 目录）
echo        映射过来直接打开——TokBrain 只允许本机 8001 写库。
echo.

set "DEFAULT_UNC="
set /p UNC=请输入 NAS 共享路径（例如 \\192.168.10.104\media）:
if "!UNC!"=="" (
    echo [取消] 没有输入路径。
    pause
    exit /b 1
)

set "DRIVE=Z"
set /p DRIVE_IN=盘符（回车默认 Z）:
if not "!DRIVE_IN!"=="" set "DRIVE=!DRIVE_IN!"
set "DRIVE=!DRIVE::=!"
set "DRIVE=!DRIVE:~0,1!"

echo.
echo 正在映射 !DRIVE!: -^> !UNC!
net use !DRIVE!: "!UNC!" /persistent:yes
if errorlevel 1 (
    echo.
    echo [失败] 映射没成功。常见原因：
    echo   - NAS 的 SMB 服务没开，或路径拼错
    echo   - 需要账号密码：先执行  net use !DRIVE!: "!UNC!" /user:用户名 密码
    echo   - 本机凭据管理器里存了旧密码
    pause
    exit /b 1
)

echo.
echo [完成] !DRIVE!: 已映射，并设为开机自动重连。
echo.
echo ------------------------------------------------------------
echo  还差一步：把这个约定写进 mcp_server\control.env
echo ------------------------------------------------------------
echo  在 control.env 里加一行（把两边都改成你的真实路径）：
echo.
echo      TOKBRAIN_CONTROL_MEDIA_ROOTS=!DRIVE!:\videos^|/vol1/1000/media/videos
echo.
echo  竖线左边 = 本机 Windows 路径（就是刚映射的 !DRIVE!: 盘）
echo  竖线右边 = 你在 NAS 文件管理器里看到的原始路径
echo.
echo  填好之后重启控制 MCP，大肥鱼传 NAS 路径会被自动翻译，
echo  传 Windows 路径或裸文件名也能用。验证：
echo      tbctl.cmd fs roots
echo.
pause
