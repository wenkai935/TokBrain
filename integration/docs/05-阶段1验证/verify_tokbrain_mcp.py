#!/usr/bin/env python3
"""阶段1前置验证：直接跟 NAS 上的 tokbrain stdio MCP 对话。
用法（在 NAS 上）：TOKBRAIN_DB_PATH=<db> python3 verify_tokbrain_mcp.py <stdio脚本路径>
只读：initialize + tools/list + 两次 tools/call（真实检索）。不改任何文件、不碰数据库写。
"""
import json
import subprocess
import sys

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else "/vol1/1000/tokbrain/bin/tokbrain_hermes_stdio.py"

reqs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "stage1-verify", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "tokbrain_search",
                "arguments": {"query": "硬件产品经理面试", "limit": 3}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "tokbrain_stats", "arguments": {}}},
]

payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in reqs) + "\n"
proc = subprocess.run(
    [sys.executable, SCRIPT], input=payload.encode("utf-8"),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
)
if proc.returncode != 0:
    print("MCP 进程非零退出:", proc.returncode)
    print(proc.stderr.decode("utf-8", "replace")[:1000])
    sys.exit(1)

for line in proc.stdout.decode("utf-8", "replace").splitlines():
    try:
        d = json.loads(line)
    except Exception:
        continue
    rid = d.get("id")
    r = d.get("result", {})
    if rid == 1:
        print("[1/4] initialize OK, protocol =", r.get("protocolVersion"))
        ins = r.get("instructions", "")
        print("      initialize 携带 instructions:", "是" if ins else "否", "(长度", len(ins), ")")
        print("      规则含『忠于evidence』:", "是" if "evidence" in ins.lower() else "否",
              "『时间戳』:", "是" if "时间戳" in ins else "否",
              "『最多三次』:", "是" if "三次" in ins else "否")
    elif rid == 2:
        tools = r.get("tools", [])
        print("[2/4] tools/list =", len(tools), "个工具")
        for t in tools:
            print("      -", t["name"])
        names = {t["name"] for t in tools}
        expect = {"tokbrain_search", "tokbrain_get_work", "tokbrain_get_transcript",
                  "tokbrain_list_collections", "tokbrain_stats"}
        print("      5个只读工具齐全:", "是" if expect <= names else "否")
        desc = tools[0].get("description", "") if tools else ""
        print("      工具描述含检索规则:", "是" if "search" in desc.lower() else "否")
    elif rid == 3:
        txt = json.dumps(r, ensure_ascii=False)
        print("[3/4] 检索『硬件产品经理面试』返回字节:", len(txt))
        # 打印命中标题与时间标记的片段，看是否带时间戳
        print("      含时间戳标记:", "是" if ("时" in txt and ":" in txt) else "未见明确时间戳")
        print("      结果预览:", txt[:600])
    elif rid == 4:
        print("[4/4] stats:", json.dumps(r, ensure_ascii=False)[:400])
