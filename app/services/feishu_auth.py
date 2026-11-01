"""飞书 lark-cli 用户授权的一键续期（device flow 驱动）。

背景：lark-cli 只有 device flow（`auth login`），**没有 refresh 子命令**。
access token 有效期约 3 小时、refresh token 约 7 天，CLI 调 API 时会自行用
refresh token 续期。真正需要用户重新授权的是「refresh token 也失效」或
「本机凭证被清理」两种情况。

因此续期分两步走（不在本模块做，由路由层先调 sync_status.cli_state() 判定）：
1. 先重测一次授权——多数红点是后端进程缓存、网络抖动或 token 刚被 CLI 自动
   刷新，重测即恢复，用户无需任何操作；
2. 仍失效才发起 device flow：后台跑 `auth login --domain base`，从 stdout
   解析 verification_url 并生成二维码返回前端。CLI 自己轮询授权结果，
   用户扫码确认后自动落 token。

为什么不让前端回传 device_code 再调 `--device-code`：CLI 输出里该值会被脱敏
（出现过连续大写 O 的假值），回传必被判无效；而且每次重启登录都会作废上一轮
的 code，容易把用户手上的链接搞成死链。让 CLI 自己守着轮询最稳。

状态放在进程内存（单用户本地应用，无需持久化），进程重启即回 idle。
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]  # 项目根目录

# 只授多维表格域：与 bridge 实际调用的 +record-list / +field-* / +record-batch-update 一致
OAUTH_DOMAIN = "base"
# 等 CLI 吐出 verification_url 的上限（正常 2~6s）
_WAIT_URL_SEC = 25
# device flow 有效期，超过则视为失败
_FLOW_TTL_SEC = 600

_URL_RE = re.compile(r"https://accounts\.feishu\.cn/oauth/v1/device/verify\?[^\s\"']+")
_CODE_RE = re.compile(r"[?&]user_code=([A-Za-z0-9\-]+)")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_LOCK = threading.Lock()
_PROC: subprocess.Popen | None = None


def _blank() -> dict:
    return {
        "state": "idle",  # idle | starting | waiting | success | failed
        "url": "",
        "user_code": "",
        "qrcode_b64": "",
        "message": "",
        "started_at": "",
        "finished_at": "",
    }


_STATE: dict = _blank()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _patch(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _snapshot() -> dict:
    with _LOCK:
        return dict(_STATE)


def _bridge():
    """延迟 import bridge，复用其 LARK_CLI / LARK_CLI_DIR（不 import sync_status，避免循环）。"""
    try:
        import bridge_feishu_obsidian as bridge

        return bridge
    except Exception:
        return None


def _decode(raw: bytes) -> str:
    bridge = _bridge()
    fn = getattr(bridge, "_decode_cli_output", None) if bridge else None
    if fn:
        try:
            return fn(raw)
        except Exception:
            pass
    return (raw or b"").decode("utf-8", "replace")


def _cli_env() -> dict:
    env = os.environ.copy()
    bridge = _bridge()
    cli_dir = getattr(bridge, "LARK_CLI_DIR", "") if bridge else ""
    if cli_dir:
        env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    return env


def _lark_cli() -> str:
    bridge = _bridge()
    return getattr(bridge, "LARK_CLI", "") if bridge else ""


def _make_qrcode(url: str) -> str:
    """用 lark-cli 自带的 qrcode 子命令把授权链接转成 PNG，返回 base64（失败返回空串）。"""
    exe = _lark_cli()
    if not exe:
        return ""
    png = Path(tempfile.gettempdir()) / f"tokbrain-lark-{os.getpid()}.png"
    try:
        proc = subprocess.run(
            [exe, "auth", "qrcode", url, "--output", str(png), "--size", "320"],
            capture_output=True,
            env=_cli_env(),
            timeout=25,
            creationflags=_NO_WINDOW,
        )
        if proc.returncode == 0 and png.exists() and png.stat().st_size > 0:
            return base64.b64encode(png.read_bytes()).decode("ascii")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    finally:
        try:
            png.unlink()
        except OSError:
            pass
    return ""


def _run_login() -> None:
    """后台线程：跑 device flow，边读输出边推进状态机。"""
    global _PROC
    exe = _lark_cli()
    if not exe:
        _patch(
            state="failed",
            message="未找到 lark-cli，请确认项目 cli/ 目录完整",
            finished_at=_now(),
        )
        return

    try:
        proc = subprocess.Popen(
            [exe, "auth", "login", "--domain", OAUTH_DOMAIN],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_cli_env(),
            cwd=str(_HERE),
            creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        _patch(
            state="failed",
            message=f"启动 lark-cli 失败: {type(exc).__name__}",
            finished_at=_now(),
        )
        return

    _PROC = proc
    got_url = False
    tail: list[str] = []
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = _decode(raw).strip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-15:]
            if not got_url:
                match = _URL_RE.search(line)
                if match:
                    url = match.group(0)
                    code_match = _CODE_RE.search(url)
                    _patch(
                        state="waiting",
                        url=url,
                        user_code=code_match.group(1) if code_match else "",
                        message="等待在飞书/浏览器中确认授权…",
                    )
                    got_url = True
                    # 二维码生成约 1s；CLI 输出 URL 后即进入轮询，短暂阻塞不影响流程
                    b64 = _make_qrcode(url)
                    if b64:
                        _patch(qrcode_b64=b64)
    finally:
        try:
            code = proc.wait(timeout=_FLOW_TTL_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -1
        _PROC = None
        if code == 0 and got_url:
            _patch(state="success", message="授权成功，飞书已重新连接", finished_at=_now())
        elif code == 0 and not got_url:
            # CLI 直接成功退出（未开 device flow）：本机已有可用 token，视为已恢复
            _patch(state="success", message="本机已有有效授权，无需重新登录", finished_at=_now())
        else:
            reason = ""
            for line in reversed(tail):
                if any(k in line for k in ("过期", "失败", "超时", "error", "Error", "cancel")):
                    reason = line
                    break
            _patch(
                state="failed",
                message=(reason or "授权未完成（链接已过期或被取消）")[:160],
                finished_at=_now(),
            )


def start_login(wait_sec: float = 8.0) -> dict:
    """发起（或复用）一次 device flow，最多阻塞 wait_sec 秒等 verification_url。

    已有进行中的流程时直接返回原 URL——重复发起会作废用户手上正在用的链接。
    拿不到 URL 时返回 state=starting，由前端轮询 login_state() 补齐
    （避免把 HTTP 请求拖到前端 30s 超时边缘）。
    """
    snap = _snapshot()
    if snap["state"] == "waiting" and _PROC is not None and _PROC.poll() is None:
        return snap

    with _LOCK:
        _STATE.clear()
        _STATE.update(_blank())
        _STATE.update(state="starting", started_at=_now())

    threading.Thread(target=_run_login, daemon=True, name="lark-relogin").start()

    deadline = time.time() + max(0.5, min(wait_sec, _WAIT_URL_SEC))
    while time.time() < deadline:
        snap = _snapshot()
        if snap["url"] or snap["state"] in ("failed", "success"):
            return snap
        time.sleep(0.15)

    return _snapshot()


def login_state() -> dict:
    """查询当前续期流程状态（供前端轮询）。"""
    return _snapshot()
