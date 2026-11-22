#!/bin/bash
# 回滚 HA 反代（xiaoqi-auth 批次A）。
# 顺序：先关 home_mcp 的反代开关（恢复直连）→ 再停反代服务。
# 这样即使停了反代，home_mcp 也不会因为 fail-closed 而报错。
set -euo pipefail
DROPIN=/etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-ha-proxy.conf

echo "=== 1. 关 home_mcp 反代开关（恢复直连 HA）==="
if [ -f "$DROPIN" ]; then
  sudo -n rm -f "$DROPIN"
  sudo -n systemctl daemon-reload
  sudo -n systemctl restart hermes-gateway-7320d9b7
  sleep 3
  echo "已移除 $DROPIN 并重启 gateway"
else
  echo "反代开关本就未开启，跳过"
fi

echo "=== 2. 停 HA 反代服务 ==="
sudo -n systemctl disable --now ha-proxy 2>/dev/null || true
echo "ha-proxy 已停"

echo "=== 3. 验证 HA 直连恢复 ==="
systemctl is-active hermes-gateway-7320d9b7
curl -s --max-time 5 -o /dev/null -w 'HA 直连 states: %{http_code}\n' \
  -H "Authorization: Bearer $(grep '^HA_TOKEN=' /vol1/1000/agent/.env | cut -d= -f2-)" \
  http://192.168.10.107:8123/api/states

echo "=== 回滚完成 ==="
