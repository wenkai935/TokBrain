#!/bin/bash
# 回滚 xiaoqi-auth 阶段1闭环。用法：bash rollback-auth.sh [STAMP]
# 顺序：先恢复音乐桥（关鉴权）→ 再恢复 home_mcp → 停 auth 服务。
set -euo pipefail
SRC=/vol1/1000/xiaoqi-auth
STATE=/vol1/1000/hermes-state
MUSIC=/app/xiaozhi/services/music
COMPOSE=/vol1/1000/xiaoqi/docker-compose.yml

if [ -n "${1:-}" ]; then
  STAMP="$1"
else
  STAMP=$(ls -1t "$SRC"/backups/home_mcp.py.bak-*-auth 2>/dev/null | head -1 | sed -E 's/.*\.bak-(.*)-auth/\1/')
fi
HMB="$SRC/backups/home_mcp.py.bak-$STAMP-auth"
SMB="$SRC/backups/server.py.bak-$STAMP-auth"
CMB="$SRC/backups/docker-compose.yml.bak-$STAMP-auth"
[ -f "$HMB" ] || { echo "找不到备份 STAMP=$STAMP"; ls -1 "$SRC"/backups/*-auth 2>/dev/null; exit 1; }

echo "=== 回滚到 $STAMP ==="
# 1. 音乐桥：恢复 server.py + 关鉴权 + compose
docker cp "$SMB" xiaoqi-bridge:$MUSIC/server.py
docker exec xiaoqi-bridge sh -c "rm -f $MUSIC/music_auth_guard.py"
if [ -f "$CMB" ]; then cp "$CMB" "$COMPOSE"; fi
cd /vol1/1000/xiaoqi && docker compose up -d --force-recreate bridge
sleep 8
echo "音乐桥已恢复（鉴权关闭）"
echo "--- GET 验证 ---"
curl -s --max-time 5 -o /dev/null -w 'status HTTP %{http_code}\n' http://127.0.0.1:4400/music/status

# 2. home_mcp 恢复 + 移除 gateway env drop-in
cp "$HMB" "$STATE/home_mcp.py"
rm -f /etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-auth.conf
systemctl daemon-reload
sudo -n systemctl restart hermes-gateway-7320d9b7
sleep 3
echo "Hermes 已恢复"

# 3. 停 auth 服务
sudo -n systemctl stop xiaoqi-auth 2>/dev/null || true
sudo -n systemctl disable xiaoqi-auth 2>/dev/null || true
echo "auth 服务已停"

echo "=== 回滚完成 ==="
systemctl is-active hermes-gateway-7320d9b7
