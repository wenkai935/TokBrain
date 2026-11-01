"""飞书同步 / Obsidian 连接状态检测（供 /api/sync 路由使用）。

职责：
- 真实查询一次飞书多维表格（复用 bridge_feishu_obsidian 的 Feishu 类），把查询
  时间与行数落盘到 data/feishu_last_query.json（跨重启保留，前端展示「最近查询」）。
- 检测 lark-cli 用户授权是否可用（cli 已连接 / 授权过期）。
- 检测 Obsidian Vault 目录是否存在且可写（本地知识库已连接 / 未连接）。
- 读取前端轮询间隔配置 data/feishu_poll.json（缺省 600s = 10min）。

注意：这些都是会 shell out / 访问外部的调用，不应并入 app/services/health.py 的
「本地无网络」探针体系，因此独立成模块。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]  # 项目根目录
DATA_DIR = _HERE / "data"
FEISHU_LAST_QUERY_PATH = DATA_DIR / "feishu_last_query.json"
POLL_CONFIG_PATH = DATA_DIR / "feishu_poll.json"
DEFAULT_FRONTEND_REFRESH_SEC = 600  # 前端默认 10 分钟

_CLI_TIMEOUT_SEC = 30

# 后端进程（start.ps1 启动的 uvicorn）不加载飞书环境变量，这里兜底到与
# 「启动飞书同步.cmd」同源的项目常量（obsidian知识库收件箱 -> 视频收藏表）。
_FEISHU_APP_TOKEN_DEFAULT = "WmDJbX7jaaiDvksBX22c7IuLnAf"
_FEISHU_TABLE_ID_DEFAULT = "tblgX5UNFSZu31aj"


def _load_bridge():
    """延迟 import bridge_feishu_obsidian，复用 Feishu 类 / LARK_CLI / VAULT_PATH。

    桥脚本顶层无副作用（仅常量与类定义），可安全 import；失败（如依赖缺失、
    路径变化）时返回 None，由调用方降级为「未配置/未知」。
    """
    try:
        import bridge_feishu_obsidian as bridge

        return bridge
    except Exception:
        return None


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def query_feishu_rows() -> dict:
    """真实查询一次飞书表，返回 {ok, rows, last_modified, detail}。

    每次调用都走一次 lark-cli record-list（读取类接口，10min 级频率足够）。
    成功时把查询时间落盘，供 read_last_query() 读取。
    """
    bridge = _load_bridge()
    app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "") or _FEISHU_APP_TOKEN_DEFAULT
    table_id = os.environ.get("FEISHU_BITABLE_TABLE_ID", "") or _FEISHU_TABLE_ID_DEFAULT
    if bridge is None or not (app_token and table_id):
        return {"ok": False, "rows": 0, "last_modified": "", "detail": "飞书未配置或 bridge 不可用"}
    if not getattr(bridge, "LARK_CLI", None):
        return {"ok": False, "rows": 0, "last_modified": "", "detail": "lark-cli 不存在"}
    try:
        fs = bridge.Feishu(app_token=app_token, table_id=table_id)
        rows = fs._list_records_raw()
    except Exception as exc:
        return {"ok": False, "rows": 0, "last_modified": "", "detail": str(exc)[:300]}
    # 「最后修改时间」列值即游标：取最大作为表内最近变更（与 bridge 增量逻辑同口径）
    col = getattr(bridge, "FS_COL_MODIFIED", "最后修改时间")
    last_modified = ""
    for row in rows:
        value = bridge.Feishu._text(row.get("fields", {}).get(col))
        if value and value > last_modified:
            last_modified = value
    _ensure_data_dir()
    payload = {
        "time_iso": _now_local().isoformat(timespec="seconds"),
        "rows": len(rows),
        "last_modified": last_modified,
    }
    FEISHU_LAST_QUERY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return {"ok": True, "rows": len(rows), "last_modified": last_modified, "detail": ""}


def read_last_query() -> dict:
    """读取最近一次飞书查询时间戳（落盘值），返回 {time:"HH:MM"|"", rows}。"""
    if not FEISHU_LAST_QUERY_PATH.exists():
        return {"time": "", "rows": 0}
    try:
        payload = json.loads(FEISHU_LAST_QUERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"time": "", "rows": 0}
    time_iso = payload.get("time_iso") or ""
    time_label = ""
    if time_iso:
        try:
            time_label = datetime.fromisoformat(time_iso).strftime("%H:%M")
        except ValueError:
            time_label = ""
    return {"time": time_label, "rows": int(payload.get("rows") or 0)}


def cli_state() -> dict:
    """检测 lark-cli 用户授权状态。

    返回 {connected, detail, expires_at, refresh_expires_at, user_name}。

    注意：`auth status` 输出是 JSON，其中 **bot 身份恒为 ready**（应用级凭证不过期），
    而同步多维表格依赖的是 **user 身份**。旧实现用全文匹配 "ready"，会被 bot 的
    ready 命中，导致 user 已过期时仍判为「已连接」——前端「一键续期」按钮因此
    永远不会出现。这里改为解析 JSON，只看 identities.user。
    """
    empty = {"expires_at": "", "refresh_expires_at": "", "user_name": ""}
    bridge = _load_bridge()
    lark_cli = getattr(bridge, "LARK_CLI", None) if bridge else None
    if not lark_cli:
        return {"connected": False, "detail": "lark-cli 不可用", **empty}
    env = os.environ.copy()
    cli_dir = getattr(bridge, "LARK_CLI_DIR", "")
    if cli_dir:
        env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [lark_cli, "auth", "status"],
            capture_output=True,
            env=env,
            timeout=_CLI_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"connected": False, "detail": f"lark-cli 调用失败: {type(exc).__name__}", **empty}
    decode = getattr(bridge, "_decode_cli_output", None)
    out = decode(proc.stdout or proc.stderr) if decode else (proc.stdout or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        return {"connected": False, "detail": (out or "auth status 失败").strip()[:200], **empty}

    try:
        payload = json.loads(out)
    except (TypeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        user = (payload.get("identities") or {}).get("user") or {}
        status = str(user.get("status") or "").lower()
        token_status = str(user.get("tokenStatus") or "").lower()
        connected = status == "ready" and token_status in ("", "valid")
        if connected:
            detail = ""
        elif status in ("", "missing") or token_status == "expired":
            detail = "用户授权已失效，需重新扫码登录"
        else:
            detail = f"用户身份状态: {status or 'unknown'}"
        return {
            "connected": connected,
            "detail": detail,
            "expires_at": str(user.get("expiresAt") or ""),
            "refresh_expires_at": str(user.get("refreshExpiresAt") or ""),
            "user_name": str(user.get("userName") or ""),
        }

    # 兼容旧版 CLI 的非 JSON 输出（此时无法区分 bot/user，只能按关键字粗判）
    lowered = out.lower()
    expired_markers = ("expired", "过期", "未登录", "not logged", "invalid token", "未授权")
    ready = "logged in" in lowered or "identity: ready" in lowered
    if any(marker in lowered for marker in expired_markers) and not ready:
        return {"connected": False, "detail": "授权过期，请重新扫码登录 lark-cli", **empty}
    return {"connected": ready, "detail": ("" if ready else out.strip()[:200]), **empty}


def obsidian_state() -> dict:
    """检测 Obsidian Vault 是否可写，返回 {connected, path, detail}。"""
    bridge = _load_bridge()
    vault = getattr(bridge, "VAULT_PATH", None) if bridge else None
    if vault is None:
        vault = Path(os.environ.get("VAULT_PATH", r"D:\ObsidianVault"))
    if not vault.exists() or not vault.is_dir():
        return {"connected": False, "path": str(vault), "detail": "目录不存在"}
    # Windows 下 os.access 对 ACL 的判断较宽松；存在即视为可访问，写入失败由
    # 实际调用（bridge）暴露。这里做「存在 + 可写」轻量探测即可。
    writable = os.access(vault, os.W_OK)
    return {
        "connected": writable,
        "path": str(vault),
        "detail": "" if writable else "目录存在但不可写（请检查权限）",
    }


def read_poll_config() -> dict:
    """读取前端轮询间隔配置，返回 {frontend_refresh_sec}（缺省 600）。"""
    try:
        payload = json.loads(POLL_CONFIG_PATH.read_text(encoding="utf-8"))
        value = int(payload.get("frontend_refresh_sec") or DEFAULT_FRONTEND_REFRESH_SEC)
    except Exception:
        value = DEFAULT_FRONTEND_REFRESH_SEC
    return {"frontend_refresh_sec": max(10, value)}


def overview() -> dict:
    """聚合 /api/sync/overview 的完整响应（每次调用都真实查一次飞书表）。"""
    query = query_feishu_rows()
    last = read_last_query()
    cli = cli_state()
    obsidian = obsidian_state()
    poll = read_poll_config()
    feishu_connected = bool(query.get("ok")) and bool(cli.get("connected"))
    return {
        "feishu": {
            "connected": feishu_connected,
            "query_ok": bool(query.get("ok")),
            "last_query": last.get("time") or "",
            "rows": int(last.get("rows") or 0),
            "query_detail": query.get("detail") or "",
            "cli_detail": cli.get("detail") or "",
            "user_name": cli.get("user_name") or "",
            "expires_at": cli.get("expires_at") or "",
            "refresh_expires_at": cli.get("refresh_expires_at") or "",
        },
        "obsidian": obsidian,
        "poll": poll,
    }
