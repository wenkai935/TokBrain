"""控制 MCP 协议级探针。

不依赖 mcp SDK，只用标准库发 JSON-RPC，用来回答一个具体问题：
**"从外面（比如 NAS）连 8011，握手、列工具、调一个只读工具，能不能通？"**

用法
----
    # 默认读 mcp_server/control.env，连 127.0.0.1:8011
    .venv-mcp\\Scripts\\python.exe scripts\\probe_control_mcp.py

    # 指定地址与令牌（测局域网可达性时用这个）
    python scripts\\probe_control_mcp.py --url http://192.168.10.100:8011/mcp --token xxx

期望结果
--------
握手成功、工具数 27、`tb_search` 是只读、`tb_work_delete` 是破坏性、
`tb_status` 返回 ok=true。

在 NAS 上直接 curl 时，期望 401 而不是 timeout：
    401          防火墙通了，只是没带令牌
    refused      服务没起
    timeout      防火墙没放行
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "mcp_server" / "control.env"
PROTOCOL_VERSION = "2024-11-05"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def rpc(url: str, token: str | None, method: str, params: dict | None = None, rid: int = 1):
    payload = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        payload["params"] = params
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            ctype = response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return {"_http_error": exc.code, "_detail": detail}
    except urllib.error.URLError as exc:
        return {"_network_error": str(exc.reason)}

    if "text/event-stream" in ctype:
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw[:400]}


def main() -> int:
    env = load_env(ENV_FILE)
    parser = argparse.ArgumentParser(description="TokBrain 控制 MCP 探针")
    parser.add_argument(
        "--url",
        default=None,
        help="MCP 地址，默认按 control.env 推导",
    )
    parser.add_argument("--token", default=None, help="令牌，默认读 control.env")
    args = parser.parse_args()

    host = env.get("TOKBRAIN_CONTROL_HOST", "127.0.0.1")
    port = env.get("TOKBRAIN_CONTROL_PORT", "8011")
    url = args.url or f"http://{host}:{port}/mcp"
    token = args.token or env.get("TOKBRAIN_CONTROL_TOKEN") or None

    print(f"目标: {url}")
    print(f"令牌: {'已提供' if token else '未提供'}")

    init = rpc(
        url,
        token,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tokbrain-probe", "version": "1.0.0"},
        },
    )
    if "_http_error" in init:
        code = init["_http_error"]
        print(f"\n[失败] initialize 返回 HTTP {code}")
        if code == 401:
            print("  401 说明网络与端口都通了，只是令牌不对/没带。")
        print(f"  响应: {init.get('_detail')}")
        return 1
    if "_network_error" in init:
        print(f"\n[失败] 连不上：{init['_network_error']}")
        print("  refused → 服务没起（先跑 mcp_server/启动控制MCP.cmd）")
        print("  timeout → 防火墙没放行（双击 mcp_server/放行控制端口.cmd）")
        return 1
    if "error" in init:
        print(f"\n[失败] initialize 报错: {init['error']}")
        return 1

    server = (init.get("result") or {}).get("serverInfo") or {}
    print(f"[通过] 握手成功: {server.get('name')} {server.get('version')}")

    listing = rpc(url, token, "tools/list", {}, rid=2)
    tools = ((listing.get("result") or {}).get("tools")) or []
    if not tools:
        print(f"\n[失败] tools/list 没返回工具: {listing}")
        return 1

    names = [t.get("name") for t in tools]
    print(f"[通过] 工具数: {len(tools)}")

    problems: list[str] = []
    non_tb = [n for n in names if not str(n).startswith("tb_")]
    if non_tb:
        problems.append(f"存在非 tb_ 前缀工具: {non_tb}")

    by_name = {t.get("name"): t for t in tools}

    def ann(tool: str, key: str) -> bool:
        entry = by_name.get(tool)
        if not entry:
            problems.append(f"缺少工具 {tool}")
            return False
        return bool((entry.get("annotations") or {}).get(key))

    if not ann("tb_search", "readOnlyHint"):
        problems.append("tb_search 应当是只读")
    if not ann("tb_work_delete", "destructiveHint"):
        problems.append("tb_work_delete 应当标破坏性")
    if not ann("tb_service_stop", "destructiveHint"):
        problems.append("tb_service_stop 应当标破坏性")

    if problems:
        print("\n[失败] 注解/命名检查未通过:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("[通过] 注解检查（只读/破坏性标注正确）")

    called = rpc(
        url,
        token,
        "tools/call",
        {"name": "tb_status", "arguments": {"include_jobs": 1}},
        rid=3,
    )
    content = ((called.get("result") or {}).get("content")) or []
    text = content[0].get("text") if content else None
    if not text:
        print(f"\n[失败] tb_status 没有返回内容: {called}")
        return 1
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        print(f"\n[失败] tb_status 返回的不是 JSON: {text[:300]}")
        return 1

    if envelope.get("ok"):
        backend = (envelope.get("data") or {}).get("backend") or {}
        print(f"[通过] tb_status ok，后端契约 {backend.get('api_contract')}")
        return 0

    error = envelope.get("error") or {}
    print(f"\n[部分失败] tb_status 返回 {error.get('code')}: {error.get('message')}")
    if error.get("code") == "backend_offline":
        print("  MCP 本身是通的，只是 TokBrain 后端没起：tbctl service start")
    return 1


if __name__ == "__main__":
    sys.exit(main())
