#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 config.example.json 生成真 config.json（随机 secret），并打印需同步给下游的 env。

只在部署机上跑一次；config.json 含明文 secret，务必 chmod 600 且不入 git。
"""
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(HERE, "config.example.json")
dst = os.path.join(HERE, "config.json")

if os.path.exists(dst):
    print(f"{dst} 已存在，不覆盖。要重建请先手动删除。")
    sys.exit(1)

cfg = json.load(open(src, encoding="utf-8"))
for cid, c in cfg["clients"].items():
    if str(c.get("secret", "")).startswith("REPLACE"):
        c["secret"] = secrets.token_hex(24)

with open(dst, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.chmod(dst, 0o600)

print(f"已生成 {dst} (chmod 600)")
print("\n=== 下游需要的环境变量（填到对应服务）===")
for cid, c in cfg["clients"].items():
    print(f"# {cid} -> aud={c.get('auds')} scopes={c.get('scopes')}")
    print(f"XIAOQI_AUTH_CLIENT={cid}")
    print(f"XIAOQI_AUTH_SECRET={c['secret']}")
    print()
print("XIAOQI_AUTH_URL=http://127.0.0.1:8900")
