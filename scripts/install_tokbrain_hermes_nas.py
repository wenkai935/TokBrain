"""Idempotently install the TokBrain stdio MCP entry into Hermes config.

Run on the NAS after copying tokbrain_hermes_stdio.py and douyin_rag.db.
The script creates a timestamped config backup and preserves unrelated keys.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

import yaml


CONFIG = Path(os.environ.get("HERMES_CONFIG", "/vol1/1000/hermes-state/config.yaml"))
PYTHON = os.environ.get("TOKBRAIN_PYTHON", "/usr/bin/python3")
SERVER = os.environ.get("TOKBRAIN_MCP_SERVER", "/vol1/1000/tokbrain/bin/tokbrain_hermes_stdio.py")
DATABASE = os.environ.get("TOKBRAIN_DB_PATH", "/vol1/1000/tokbrain/data/douyin_rag.db")


def main() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    backup_dir = CONFIG.parent / "backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"config-before-tokbrain-{stamp}.yaml"
    shutil.copy2(CONFIG, backup)
    servers = config.setdefault("mcp_servers", {})
    servers["tokbrain"] = {
        "command": PYTHON,
        "args": [SERVER],
        "env": {"TOKBRAIN_DB_PATH": DATABASE},
    }
    for channel in ("cli", "web", "dashboard", "api", "cron"):
        tools = config.setdefault("platform_toolsets", {}).setdefault(channel, [])
        if "tokbrain" not in tools:
            tools.append("tokbrain")
    temp = CONFIG.with_suffix(".yaml.tmp")
    temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(CONFIG)
    print(f"backup={backup}")
    print(f"config={CONFIG}")


if __name__ == "__main__":
    main()
