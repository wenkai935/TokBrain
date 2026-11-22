#!/bin/bash
# 批次A一体化部署（HA反代 + HA读走反代 + 播报票据）。
# 用法：bash deploy-batch-a.sh [--with-ha-switch]
#   不带参数：只起 HA 反代 + 播报鉴权，home_mcp 仍直连 HA（最保守）
#   带 --with-ha-switch：额外打开 XIAOQI_HA_VIA_PROXY=1，让 HA 读写全走反代
#
# 顺序严格如下，任一步失败即停（set -e），不会留下半开状态：
#   1. HA 反代服务        （只加服务，不改任何现有调用）
#   2. 播报票据（X-Token 保留，纯增量）
#   3. （可选）切换 home_mcp/haclient 到反代
set -euo pipefail
SRC=/vol1/1000/xiaoqi-auth
WITH_SWITCH=0
[ "${1:-}" = "--with-ha-switch" ] && WITH_SWITCH=1

echo "########## 批次A部署开始（with_ha_switch=$WITH_SWITCH）##########"
[ -f "$SRC/config.json" ] || { echo "缺 config.json"; exit 1; }

echo "########## 1/3 HA 反代 ##########"
bash "$SRC/deploy-ha-proxy.sh"

echo "########## 2/3 播报票据 ##########"
bash "$SRC/deploy-voice-auth.sh"

if [ "$WITH_SWITCH" = "1" ]; then
  echo "########## 3/3 切换 HA 读书写到反代 ##########"
  echo "注意：这一步会让 home_mcp 和 haclient 不再持有 HA_TOKEN，改持票据。"
  # 替换 home_mcp.py 为反代版（保留所有 NAS 真身功能 + 音乐认证 + HA 反代）
  HMB_MD5=$(md5sum /vol1/1000/hermes-state/home_mcp.py | cut -d' ' -f1)
  if [ "$HMB_MD5" != "8a0c15fdc65eacb292038a04af0b1c60" ]; then
    echo "⚠️ home_mcp 当前 md5=$HMB_MD5，与预期的 auth-fix 基线 8a0c15fd 不符，中止"
    exit 1
  fi
  cp -p /vol1/1000/hermes-state/home_mcp.py "$SRC/backups/home_mcp.py.bak-$(date +%Y%m%d-%H%M%S)-haproxy"
  cp "$SRC/home_mcp.py.ha-proxy-fix" /vol1/1000/hermes-state/home_mcp.py
  # haclient 是 tools 的内依赖，随 home_mcp 进程加载
  cp -p /vol1/1000/agent-llm/haclient.py "$SRC/backups/haclient.py.bak-$(date +%Y%m%d-%H%M%S)-haproxy"
  cp "$SRC/haclient.py.ha-proxy-fix" /vol1/1000/agent-llm/haclient.py
  # 打开开关
  printf '[Service]\nEnvironment=XIAOQI_HA_VIA_PROXY=1\n' | \
    sudo -n tee /etc/systemd/system/hermes-gateway-7320d9b7.service.d/xiaoqi-ha-proxy.conf >/dev/null
  sudo -n systemctl daemon-reload
  sudo -n systemctl restart hermes-gateway-7320d9b7
  sleep 5
  echo "已切换。验证（只读，不出声）："
  systemctl is-active hermes-gateway-7320d9b7 ha-proxy
  echo "回滚：bash $SRC/rollback-ha-proxy.sh"
else
  echo "########## 3/3 跳过（未加 --with-ha-switch，home_mcp 仍直连 HA）##########"
fi

echo "########## 批次A部署完成 ##########"
echo "验证清单："
echo "  curl -s http://127.0.0.1:8900/audit | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"count\"])'"
echo "  systemctl is-active xiaoqi-auth ha-proxy life-agent"
