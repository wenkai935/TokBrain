@echo off
rem 启动飞书同步桥（bridge_feishu_obsidian.py --watch 常驻）
rem 用法：双击本文件，或在普通终端运行（勿在 WorkBuddy 沙箱内跑，避免 safe-delete shim 干扰文件操作）
setlocal
cd /d "%~dp0"

rem ---- 飞书多维表格（obsidian知识库收件箱 -> 视频收藏表）----
set FEISHU_BITABLE_APP_TOKEN=WmDJbX7jaaiDvksBX22c7IuLnAf
set FEISHU_BITABLE_TABLE_ID=tblgX5UNFSZu31aj
rem ---- Obsidian Vault（默认值已内置，可覆盖）----
if not defined VAULT_PATH set VAULT_PATH=D:\ObsidianVault

rem ---- 本机后端，别让系统代理（Clash/VPN/CI）把它推给代理 ----
rem 踩过：HTTP_PROXY 存在时 requests 会连 127.0.0.1:8001 都走代理，
rem       后端收到 path 是 "http://127.0.0.1:8001/api/..."，直接 404。
set "NO_PROXY=127.0.0.1,localhost,::1"
set "no_proxy=127.0.0.1,localhost,::1"

if not exist "data\logs" mkdir "data\logs"
echo [启动] 飞书同步桥 --watch 常驻轮询（Ctrl+C 停止），日志: data\logs\bridge-watch.log
.\.venv\Scripts\python.exe -u bridge_feishu_obsidian.py --watch >> data\logs\bridge-watch.log 2>&1
