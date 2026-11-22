#!/bin/bash
# 部署播报鉴权（xiaoqi-auth 批次A最后一项）。
# 作用：life-agent(:8787) 外部调用除 X-Token 外也认 Bearer 票据；回环仍免鉴权。
# 为什么低风险：X-Token 保留 → 现有 life.py/timer 完全不受影响，只是多开一扇门。
# 前置：xiaoqi-auth 在线(:8900)；life-agent 是 systemd 服务。
set -euo pipefail
SRC=/vol1/1000/xiaoqi-auth
AGENT=/vol1/1000/agent
STAMP=$(date +%Y%m%d-%H%M%S)
SVC=$(systemctl list-units --type=service --all --no-legend 2>/dev/null | awk '{print $1}' | grep -xE 'life-agent\.service' | head -1)
[ -n "${SVC:-}" ] || { echo "找不到 life-agent.service"; exit 1; }
echo "目标服务: $SVC"

echo "=== 0. 前置检查 ==="
curl -s --max-time 4 http://127.0.0.1:8900/health | grep -q '"ok"' || { echo "xiaoqi-auth 未就绪"; exit 1; }
python3 -m py_compile "$SRC/app.py.auth-fix"
echo "语法 OK"

echo "=== 1. 备份 ==="
sudo -n mkdir -p "$SRC/backups"
cp -p "$AGENT/app.py" "$SRC/backups/app.py.bak-$STAMP-auth"
echo "备份 $SRC/backups/app.py.bak-$STAMP-auth"

echo "=== 2. 部署补丁 + 注入 auth 地址 ==="
cp -p "$SRC/app.py.auth-fix" "$AGENT/app.py"
DROPIN="/etc/systemd/system/$SVC.service.d/xiaoqi-auth.conf"
sudo -n mkdir -p "$(dirname "$DROPIN")"
printf '[Service]\nEnvironment=XIAOQI_AUTH_URL=http://127.0.0.1:8900\n' | sudo -n tee "$DROPIN" >/dev/null
sudo -n systemctl daemon-reload
sudo -n systemctl restart "$SVC"
sleep 3

echo "=== 3. 验证（回环免鉴权仍应 200，X-Token 仍应通）==="
systemctl is-active "$SVC"
echo -n "回环 /say（无凭证，应 200）: "
curl -s --max-time 8 -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:8787/say?text=deploy-test" || true

echo "=== DONE ==="
echo "回滚：cp $SRC/backups/app.py.bak-$STAMP-auth $AGENT/app.py && sudo -n systemctl restart $SVC"
echo "（不传 STAMP 时按时间戳取最新备份）"
