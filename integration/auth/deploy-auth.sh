#!/bin/bash
# 部署 xiaoqi-auth 阶段1最小闭环（音乐桥示范）。
# 在 NAS 上执行前：先在本地审阅本目录所有文件、生成真 config.json 并同步。
# 部署顺序：auth 服务 → home_mcp 带 token → 音乐桥开鉴权（最后一步才生效）。
set -euo pipefail
SRC=/vol1/1000/xiaoqi-auth            # 脚本/config/补丁同步至此
STATE=/vol1/1000/hermes-state         # home_mcp.py 真实位置
MUSIC=/app/xiaozhi/services/music     # 容器内音乐模块路径（docker exec 用）
STAMP=$(date +%Y%m%d-%H%M%S)

echo "=== 0. 前置检查 ==="
[ -f "$SRC/config.json" ] || { echo "缺 config.json（先跑 gen_config.py）"; exit 1; }
python3 -m py_compile "$SRC/auth_server.py" "$SRC/auth_client.py" "$SRC/music_auth_guard.py"
echo "语法 OK"

echo "=== 1. 备份现有文件 + 核对基线 ==="
mkdir -p "$SRC/backups"
# 基线核对：home_mcp 若被改动过则中止，避免拿旧补丁覆盖新功能（曾踩过 ICE_SIZE 被冲掉的坑）
HMB_MD5=$(md5sum "$STATE/home_mcp.py" | cut -d' ' -f1)
echo "home_mcp 当前 md5=$HMB_MD5（期望 937e6a114d1a6e67b635e52c0dbce8b3）"
[ "$HMB_MD5" = "937e6a114d1a6e67b635e52c0dbce8b3" ] || { echo "⚠️ home_mcp 已变更，中止部署，请重新核对基线"; exit 1; }
SMB_MD5=$(docker exec xiaoqi-bridge md5sum $MUSIC/server.py | cut -d' ' -f1)
echo "music server 当前 md5=$SMB_MD5（期望 bf3b46e0cbc8a32c2745704687d0d773）"
[ "$SMB_MD5" = "bf3b46e0cbc8a32c2745704687d0d773" ] || { echo "⚠️ music server 已变更，中止部署，请重新核对基线"; exit 1; }
cp -p "$STATE/home_mcp.py" "$SRC/backups/home_mcp.py.bak-$STAMP-auth"
# server.py 备份必须落到宿主（rollback 从宿主 docker cp 回）；之前写在容器内，recreate 时会丢失
docker cp xiaoqi-bridge:$MUSIC/server.py "$SRC/backups/server.py.bak-$STAMP-auth"
cp -p /vol1/1000/xiaoqi/docker-compose.yml "$SRC/backups/docker-compose.yml.bak-$STAMP-auth"
echo "备份完成 STAMP=$STAMP"

echo "=== 2. 部署 auth 服务（systemd，独立）==="
# 文件已直接同步到 $SRC，无需再自拷贝（源文件与目标目录相同会导致 cp 报错）
for f in auth_server.py config.json auth_client.py; do
  [ -f "$SRC/$f" ] || { echo "缺文件 $SRC/$f"; exit 1; }
done
sudo tee /etc/systemd/system/xiaoqi-auth.service >/dev/null <<'EOF'
[Unit]
Description=xiaoqi-auth service-to-service auth
After=network.target

[Service]
Type=simple
User=kai
WorkingDirectory=/vol1/1000/xiaoqi-auth
ExecStart=/usr/bin/python3 /vol1/1000/xiaoqi-auth/auth_server.py --config /vol1/1000/xiaoqi-auth/config.json --host 127.0.0.1 --port 8900
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now xiaoqi-auth
sleep 2
systemctl is-active xiaoqi-auth
curl -s http://127.0.0.1:8900/health; echo

echo "=== 3. 改 home_mcp.py 带 token（此时音乐桥还没开鉴权，不影响现状）==="
cp "$SRC/home_mcp.py.auth-fix" "$STATE/home_mcp.py"
# 给 hermes-gateway 注入 auth env（Hermes 是 systemd，用 drop-in）
sudo -n mkdir -p /etc/systemd/system/hermes-gateway-7320d9b7.service.d
# 从 config.json 读 secret，经 stdin 写 drop-in（不落到命令行参数里，权限 600）
HERMES_SECRET=$(python3 -c "import json;print(json.load(open('$SRC/config.json'))['clients']['hermes-home']['secret'])")
printf '[Service]\nEnvironment=XIAOQI_AUTH_URL=http://127.0.0.1:8900\nEnvironment=XIAOQI_AUTH_CLIENT=hermes-home\nEnvironment=XIAOQI_AUTH_SECRET=%s\n' "$HERMES_SECRET" | \
  sudo -n tee /etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-auth.conf >/dev/null
sudo -n chmod 600 /etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-auth.conf
sudo -n systemctl daemon-reload
echo "home_mcp.py 与 gateway env 已就位（稍后与音乐桥一起重启 gateway）"

echo "=== 4. 改 compose environment（auth 地址）==="
# 关键：music 模块是镜像内置的（无 volume 挂载），docker cp 的改动会在 recreate 时丢失。
# 所以顺序必须是：先改 compose → recreate → 再 docker cp 注入代码 → docker restart 重新加载。
COMPOSE=/vol1/1000/xiaoqi/docker-compose.yml
if [ ! -f "$SRC/backups/docker-compose.yml.bak-$STAMP-auth" ]; then
  cp -p "$COMPOSE" "$SRC/backups/docker-compose.yml.bak-$STAMP-auth"
fi
python3 - "$COMPOSE" <<'PY'
import sys
p=sys.argv[1]; s=open(p,encoding='utf-8').read()
if 'XIAOQI_AUTH_URL' not in s:
    s=s.replace("      XIAOZHI_TTS_OUTPUT_MODE: native_xiaomi",
                "      XIAOZHI_TTS_OUTPUT_MODE: native_xiaomi\n      XIAOQI_AUTH_URL: http://127.0.0.1:8900",1)
    open(p,'w',encoding='utf-8').write(s)
    print('compose 已加 XIAOQI_AUTH_URL')
else:
    print('compose 已有 XIAOQI_AUTH_URL，跳过')
PY
echo "compose 已就位"

echo "=== 5. 重建音乐桥容器 → 注入鉴权代码 → restart 加载 ==="
cd /vol1/1000/xiaoqi && docker compose up -d --force-recreate bridge
sleep 8
# recreate 后注入（此时容器层是干净的镜像代码）
docker cp "$SRC/music_auth_guard.py" xiaoqi-bridge:$MUSIC/music_auth_guard.py
docker cp "$SRC/music_server.py.auth-fix" xiaoqi-bridge:$MUSIC/server.py
echo "鉴权代码已注入，md5:"
docker exec xiaoqi-bridge md5sum $MUSIC/server.py
# docker restart 保留容器层文件改动，让 Python 重新加载鉴权版 server.py
docker restart xiaoqi-bridge >/dev/null
sleep 8
sudo -n systemctl restart hermes-gateway-7320d9b7
sleep 3
echo "--- 服务状态 ---"
systemctl is-active xiaoqi-auth hermes-gateway-7320d9b7
docker ps --format '{{.Names}} {{.Status}}' | grep xiaoqi-bridge

echo "=== 6. 验证闭环 ==="
echo "--- GET 放行 ---"
curl -s --max-time 5 http://127.0.0.1:4400/music/status | head -c 120; echo
echo "--- POST 无 token 应 401 ---"
curl -s --max-time 5 -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:4400/music/command -H 'Content-Type: application/json' -d '{"text":"播放晚风作酒"}'
echo "--- auth 审计条数 ---"
curl -s http://127.0.0.1:8900/audit | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])"

echo "=== DONE ==="
echo "回滚：bash $SRC/rollback-auth.sh $STAMP"
