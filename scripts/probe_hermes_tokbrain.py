"""End-to-end Hermes acceptance probe for the TokBrain MCP toolset."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml


STATE = Path("/vol1/1000/hermes-state")
CLI = "/home/kai/hermes-agent/.hermes/bin/hermes"


def main() -> None:
    config = yaml.safe_load((STATE / "config.yaml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["tokbrain"]["env"]["TOKBRAIN_DB_PATH"].endswith("douyin_rag.db")
    assert all("tokbrain" in config["platform_toolsets"][name]
               for name in ("cli", "web", "dashboard", "api", "cron"))
    env = {**os.environ, "HERMES_HOME": str(STATE), "TZ": "Asia/Shanghai"}
    network = STATE / "network.env"
    if network.exists():
        for line in network.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key] = value.strip().strip('"').strip("'")
    query = "请查我的 TokBrain 知识库：Prompt 版本管理应该怎么做？只根据知识库证据简短回答，并注明视频标题和时间。"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as handle:
        handle.write(query)
        query_file = handle.name
    try:
        process = subprocess.run(
            [CLI, "chat", "--oneshot", "-Q", "--format", "stream-json",
             "--query-file", query_file, "--toolsets", "tokbrain", "--max-turns", "6", "--run-budget", "90"],
            env=env, capture_output=True, text=True, timeout=120,
        )
    finally:
        Path(query_file).unlink(missing_ok=True)
    rows = []
    for line in process.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    tool_events = [row for row in rows if "tool" in str(row.get("type", "")).lower()
                   or "tokbrain" in json.dumps(row, ensure_ascii=False).lower()]
    result = next((row for row in reversed(rows) if row.get("type") == "result"), {})
    text = str(result.get("text") or "")
    if process.returncode != 0:
        print(json.dumps({"ok": False, "returncode": process.returncode,
                          "stdout_tail": process.stdout[-4000:],
                          "stderr_tail": process.stderr[-2000:]}, ensure_ascii=False))
        raise SystemExit(process.returncode)
    assert tool_events, rows[-10:]
    assert text.strip(), result
    print(json.dumps({"ok": True, "tool_events": len(tool_events), "answer": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
