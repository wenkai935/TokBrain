"""Protocol-level acceptance probe for the TokBrain stdio MCP server."""

from __future__ import annotations

import json
import os
import subprocess
import sys


SERVER = os.environ.get("TOKBRAIN_MCP_SERVER", "/vol1/1000/tokbrain/bin/tokbrain_hermes_stdio.py")


def main() -> None:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tokbrain_stats", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "tokbrain_search", "arguments": {"query": "视频", "limit": 1}}},
    ]
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
    process = subprocess.run(
        [sys.executable, SERVER], input=payload, text=True, capture_output=True,
        env={**os.environ, "TOKBRAIN_DB_PATH": os.environ.get("TOKBRAIN_DB_PATH", "/vol1/1000/tokbrain/data/douyin_rag.db")},
        timeout=20, check=True,
    )
    responses = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    assert len(responses) == 4, process.stdout
    assert all("error" not in response for response in responses), responses
    tools = {item["name"] for item in responses[1]["result"]["tools"]}
    assert "tokbrain_search" in tools and "tokbrain_get_transcript" in tools, tools
    stats = json.loads(responses[2]["result"]["content"][0]["text"])
    search = json.loads(responses[3]["result"]["content"][0]["text"])
    assert stats["works_in_library"] > 0 and stats["chunks_total"] > 0, stats
    assert search["ok"] is True and search["count"] <= 1, search
    print(json.dumps({"ok": True, "tools": sorted(tools), "stats": stats,
                      "search_count": search["count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
