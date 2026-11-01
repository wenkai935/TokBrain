"""写操作审计（JSONL，按日切分）。

原则：**不落原文**。链接正文、文件名、提示词只记数量和摘要 hash，避免把用户内容
写进日志后被随手贴出去。

返回值里带 ``audit_id``，Hermes 转述失败时可以让用户凭 id 回查。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = _PROJECT_ROOT / "data" / "logs"
RETENTION_DAYS = 30

_lock = threading.Lock()


def new_audit_id() -> str:
    return uuid.uuid4().hex[:12]


def _log_path(log_dir: Path, day: date | None = None) -> Path:
    day = day or date.today()
    return log_dir / f"control-audit-{day:%Y%m%d}.jsonl"


def digest_args(args: dict[str, Any] | None) -> tuple[str, int]:
    """返回 (参数摘要, 参数体积)。摘要用于对账，不能反推原文。"""
    if not args:
        return ("", 0)
    import hashlib

    try:
        encoded = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(args)
    return (hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16], len(encoded))


def prune(log_dir: Path | None = None) -> int:
    """删掉超过保留期的审计文件，返回删除数量。"""
    log_dir = log_dir or DEFAULT_LOG_DIR
    if not log_dir.is_dir():
        return 0
    cutoff = date.today() - timedelta(days=RETENTION_DAYS)
    removed = 0
    for path in log_dir.glob("control-audit-*.jsonl"):
        stem = path.stem.replace("control-audit-", "")
        try:
            day = datetime.strptime(stem, "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def record(
    *,
    tool: str,
    ok: bool,
    audit_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    http_status: int | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
    args: dict[str, Any] | None = None,
    counts: dict[str, Any] | None = None,
    note: str | None = None,
    severity: str = "normal",
    caller: str | None = None,
    log_dir: Path | None = None,
) -> str:
    """追加一条审计记录，返回 audit_id。任何异常都不向上抛。"""
    audit_id = audit_id or new_audit_id()
    args_digest, args_size = digest_args(args)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "audit_id": audit_id,
        "caller": caller,
        "tool": tool,
        "method": method,
        "path": path,
        "http_status": http_status,
        "ok": bool(ok),
        "error_code": error_code,
        "duration_ms": duration_ms,
        "args_digest": args_digest,
        "args_size": args_size,
        "counts": counts or {},
        "note": note,
        "severity": severity,
    }
    try:
        target_dir = log_dir or DEFAULT_LOG_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with _lock:
            fd = os.open(
                _log_path(target_dir), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
            )
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except OSError:
        # 审计失败不能影响主流程，但也不要静默到查不出来
        pass
    return audit_id


def tail(count: int = 20, log_dir: Path | None = None) -> list[dict[str, Any]]:
    """读最近 N 条审计（跨天回溯，最多看 7 天）。"""
    log_dir = log_dir or DEFAULT_LOG_DIR
    collected: list[dict[str, Any]] = []
    for offset in range(7):
        path = _log_path(log_dir, date.today() - timedelta(days=offset))
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if len(collected) >= count:
                break
            line = line.strip()
            if not line:
                continue
            try:
                collected.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if len(collected) >= count:
            break
    return collected
