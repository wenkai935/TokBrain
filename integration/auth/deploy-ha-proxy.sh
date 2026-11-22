#!/bin/bash
# 部署 HA 反向代理（xiaoqi-auth 批次A第1步）。
# 作用：HA_TOKEN 只活在反代一处；业务侧（home_mcp/haclient）改持短期票据。
# 顺序：起反代(8124) → 验证 → 再决定是否切换 home_mcp 的反代开关（默认不切）。
# 前置：xiaoqi-auth 已在 8900 运行；/vol1/1000/agent/.env 里有 HA_TOKEN。
set -euo pipefail
SRC=/vol1/1000/xiaoqi-auth
STAMP=$(date +%Y%m%d-%H%M%S)
HA_ENV=/vol1/1000/agent/.env

echo "=== 0. 前置检查 ==="
curl -s --max-time 4 http://127.0.0.1:8900/health | grep -q '"ok"' || { echo "xiaoqi-auth 未就绪(:8900)"; exit 1; }
grep -q '^HA_TOKEN=' "$HA_ENV" || { echo "$HA_ENV 缺 HA_TOKEN"; exit 1; }
python3 -m py_compile "$SRC/ha_proxy.py"
echo "前置 OK（auth 在线、HA_TOKEN 存在、语法通过）"

echo "=== 1. 部署 systemd 单元 ==="
sudo -n mkdir -p "$SRC/backups"
sudo tee /etc/systemd/system/ha-proxy.service >/dev/null <<EOF
[Unit]
Description=HA reverse proxy with xiaoqi-auth ticket gate
After=network.target xiaoqi-auth.service
Wants=xiaoqi-auth.service

[Service]
Type=simple
User=kai
WorkingDirectory=$SRC
# HA_TOKEN/HA_URL 从 life.env 注入；不写死在单元或代码里
EnvironmentFile=$HA_ENV
Environment=AUTH_URL=http://127.0.0.1:8900
Environment=PORT=8124
Environment=AUDIT_PATH=$SRC/ha-proxy-audit.jsonl
ExecStart=/usr/bin/python3 $SRC/ha_proxy.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now ha-proxy
sleep 2
systemctl is-active ha-proxy

echo "=== 2. 验证反代（不碰真实设备，只做只读+门禁验证）==="
SECRET=$(python3 -c "import json;print(json.load(open('$SRC/config.json'))['clients']['home-history']['secret'])")
TOK=$(curl -s --max-time 6 -X POST http://127.0.0.1:8900/token -H 'Content-Type: application/json' \
  -d "{\"grant_type\":\"client_credentials\",\"client_id\":\"home-history\",\"client_secret\":\"$SECRET\",\"aud\":\"ha-proxy\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
echo -n "反代 GET states（只读票）: "
curl -s --max-time 8 -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:8124/api/states
echo -n "反代 GET 无票: "
curl -s --max-time 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8124/api/states
echo -n "反代 路径越界(/api/config): "
curl -s --max-time 5 -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:8124/api/config

echo "=== DONE（home_mcp 反代开关默认未打开）==="
echo "下一步（确认反代无误后手动执行）："
echo "  sudo tee /etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-ha-proxy.conf <<'X'"
echo "  [Service]"
echo "  Environment=XIAOQI_HA_VIA_PROXY=1"
echo "  X"
echo "  sudo -n systemctl daemon-reload && sudo -n systemctl restart hermes-gateway-7320d9b7"
echo "回滚：sudo -n systemctl disable --now ha-proxy"
