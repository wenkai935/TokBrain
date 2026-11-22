#!/bin/bash
# 开源发布前敏感信息扫描：零命中才允许 push。
# 用法：bash verify-before-push.sh [待发布目录]
# 默认扫描当前目录。发现任何疑似真凭据/隐私即退出码 1。
set -u
ROOT="${1:-.}"
echo "=== 扫描目录: $ROOT ==="
FAILED=0
# 注：元文档（检查清单/结构总结/本脚本）自身会引用敏感值做说明，已在各 grep 中 --exclude 排除。

# 1) 真凭据模式（硬阻断：密钥/证书，排除明显占位符）
echo "--- 1. 真密钥/证书扫描（硬阻断）---"
SCAN=$(grep -rInE \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.venv \
  --exclude='开源前检查清单.md' --exclude='verify-before-push.sh' --exclude='项目结构总结.md' \
  --include='*.py' --include='*.yaml' --include='*.yml' --include='*.json' \
  --include='*.md' --include='*.sh' --include='*.toml' --include='*.ini' \
  --include='*.conf' --include='*.env*' --include='*.txt' \
  'a861d0d5|8k8652pd|sk-[A-Za-z0-9]{20,}|Bearer[[:space:]]+[A-Za-z0-9._-]{24,}|[0-9]{8,}@im\.bot:[a-f0-9]{20,}|BEGIN[[:space:]]+(RSA|OPENSSH|EC)[[:space:]]+PRIVATE|[a-f0-9]{32}' \
  "$ROOT" 2>/dev/null \
  | grep -viE 'xxxxxxxx|your_|你的|example|placeholder|<.*>|\$\{|sha256|digest|md5|hash')
if [ -n "$SCAN" ]; then
  echo "$SCAN" | head -30
  echo "❌ 发现疑似真密钥/证书！"
  FAILED=1
else
  echo "✅ 无真密钥命中"
fi

# 1b) 内网 IP / 设备 ID（需人工确认，不算硬失败）
echo "--- 1b. 内网 IP / 设备 ID（需确认已脱敏）---"
PRIV=$(grep -rInE \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.venv \
  --exclude='开源前检查清单.md' --exclude='verify-before-push.sh' --exclude='项目结构总结.md' \
  --include='*.py' --include='*.md' --include='*.sh' --include='*.yaml' --include='*.yml' \
  '192\.168\.(10|5|0|31)\.[0-9]+|cuco_cn_[0-9]+|iot_cn_[0-9]+|bc:09:b9:djcmy@' \
  "$ROOT" 2>/dev/null \
  | head -20)
if [ -n "$PRIV" ]; then
  echo "$PRIV"
  echo "⚠️  仍有内网 IP / 设备 ID 未脱敏（不阻断，但强烈建议按清单替换）"
else
  echo "✅ 无内网 IP / 设备 ID"
fi

# 2) 禁止的文件类型
echo "--- 2. 禁止文件类型 ---"
BAD=$(find "$ROOT" -type f \( -name '*.pem' -o -name '*.key' -o -name 'id_rsa*' \
  -o -name '*.log' -o -name '*.jsonl' -o -name '*.pt' -o -name '*.onnx' -o -name '*.db' \
  -o -name '*.tar' -o -name '*.tar.gz' -o -name '*.mp4' \) \
  -not -path '*/.git/*' 2>/dev/null | head -30)
if [ -n "$BAD" ]; then
  echo "$BAD"
  echo "❌ 发现不应入库的文件（证书/日志/模型/归档/视频）"
  FAILED=1
else
  echo "✅ 无禁止文件"
fi

# 3) 运行目录
echo "--- 3. 运行目录 ---"
DIRS=$(find "$ROOT" -type d \( -name models -o -name logs -o -name backup -o -name backups -o -name mysql -o -name music \) \
  -not -path '*/.git/*' 2>/dev/null | head -20)
if [ -n "$DIRS" ]; then
  echo "$DIRS"
  echo "⚠️  发现运行数据目录，确认是否已加 .gitignore"
  FAILED=1
else
  echo "✅ 无运行数据目录"
fi

echo "==================="
if [ "$FAILED" -eq 0 ]; then
  echo "✅ 全部通过，可以 git push"
  exit 0
else
  echo "❌ 存在问题，修复后再 push"
  exit 1
fi
