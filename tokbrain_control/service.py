"""Windows 侧服务生命周期：查状态、启动、停止、重启。

两条铁律：

1. **终止只经 ``scripts/stop.ps1``**。它会按 ``data/runtime.json`` 里的双重身份
   （pid + creation_time + executable_path + 命令行哈希）复核，不匹配就不动手。
   控制面代码里不出现 ``taskkill`` / ``Stop-Process``——那会绕过这套校验，
   变成"能杀掉这台机器上任何东西"的远程工具。
2. **不用 ``停止.cmd`` / ``重启.cmd``**。它们的成功分支有 ``timeout /t 2``、
   失败分支有 ``pause``，在 stdin 被重定向的进程里会直接报错或挂死。

另注意 ``启动.cmd`` 会调用 ``start.ps1``，而 ``start.ps1`` 成功后会主动打开浏览器。
远端一次 ``tb_service_start`` 不该在用户屏幕上弹窗口，所以必须注入
``TOKBRAIN_SKIP_LOCAL_UI_OPEN=1``。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from .errors import (
    CODE_ALREADY_RUNNING,
    CODE_NOT_RUNNING,
    CODE_PORT_CONFLICT_UNTRACKED,
    CODE_POWERSHELL_MISSING,
    CODE_RUNTIME_MISSING_BUT_PORT_BUSY,
    CODE_SERVICE_BUSY,
    CODE_START_FAILED,
    CODE_STOP_INCOMPLETE,
    CODE_STOP_REFUSED,
    CODE_BACKEND_CONTRACT_MISMATCH,
    ControlError,
)
from .http import EXPECTED_API_CONTRACT, Transport

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_JSON = PROJECT_ROOT / "data" / "runtime.json"
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOCK_PATH = LOG_DIR / ".control-service.lock"

BACKEND_PORT = 8001
FRONTEND_PORT = 3001
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"

START_SCRIPT = PROJECT_ROOT / "launchers" / "启动.cmd"
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop.ps1"

START_TIMEOUT = 40.0
STOP_TIMEOUT = 30.0
LOCK_STALE_SECONDS = 180.0
CREATE_NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ 探测原语


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_runtime() -> dict[str, Any]:
    """读 ``data/runtime.json``（只读，永不写）。"""
    if not RUNTIME_JSON.is_file():
        return {"exists": False}
    try:
        raw = RUNTIME_JSON.read_bytes()
    except OSError as exc:
        return {"exists": True, "readable": False, "error": repr(exc)}
    info: dict[str, Any] = {
        "exists": True,
        "readable": True,
        "path": str(RUNTIME_JSON),
        "mtime": RUNTIME_JSON.stat().st_mtime,
        "sha": hashlib.sha256(raw).hexdigest()[:16],
        "size": len(raw),
    }
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        info["parse_error"] = repr(exc)
        return info
    if isinstance(data, dict):
        info["state_version"] = data.get("state_version")
        backend = data.get("backend") or {}
        info["backend"] = {
            key: backend.get(key)
            for key in ("pid", "port", "host", "started_at")
            if isinstance(backend, dict) and key in backend
        }
        info["top_level_keys"] = sorted(data.keys())
    return info


def tail_log(name: str, lines: int = 30) -> list[str]:
    path = LOG_DIR / name
    if not path.is_file():
        return []
    try:
        # 日志可能很大，只读尾部 64KB
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > 64 * 1024:
                handle.seek(size - 64 * 1024)
            chunk = handle.read()
    except OSError:
        return []

    # cmd 的 `>` 重定向会写成 UTF-16LE，而 Python 子进程写的是 UTF-8。
    # 同一个 logs 目录里两种编码并存，按特征判断而不是硬编码一种。
    if chunk[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = chunk.decode("utf-16", "replace")
    elif chunk.count(b"\x00") >= 2 and chunk.count(b"\x00") > len(chunk) * 0.01:
        text = chunk.decode("utf-16-le", "replace")
    else:
        text = chunk.decode("utf-8", "replace")

    cleaned: list[str] = []
    for line in text.splitlines():
        line = line.replace("\x00", "").rstrip()
        if not line.strip():
            continue
        cleaned.append(line)
    return cleaned[-lines:]


def _health() -> dict[str, Any]:
    try:
        transport = Transport(BACKEND_URL, read_timeout=2.0)
        payload = transport.health(read_timeout=2.0)
        return {"reachable": True, **payload}
    except ControlError as exc:
        return {"reachable": False, "code": exc.code, "message": exc.message}


def _started_recently(window_seconds: float = 1800.0) -> bool:
    """日志里出现过「已启动」但进程已经没了——典型的 Job Object 回收。

    只看最近 30 分钟的日志：否则一次正常的「停止」之后，历史日志会永远把状态
    误判成 stale（停止脚本不写这个文件）。
    """
    log_path = LOG_DIR / "startup-console.log"
    try:
        if time.time() - log_path.stat().st_mtime > window_seconds:
            return False
    except OSError:
        return False
    for line in tail_log("startup-console.log", 80):
        if "已启动" in line or "started" in line.lower():
            return True
    return False


def in_job_object() -> bool | None:
    """当前进程是否处在 Windows Job Object 里。

    **实测结论**（2026-11-01 验证）：从 IDE/终端里调 ``tb_service_start``，因为父进程
    持有 Job Object，``启动.cmd`` 拉起的服务会在父进程退出时被一起回收——日志里写着
    "已启动"，端口却已经关了，runtime.json 变成孤儿。``start.ps1`` 自己也发现了这点，
    会打印"无法创建作业对象（本进程可能已受其他作业约束）"。

    与其让用户对着 "启动成功但 30 秒后消失" 猜，不如提前说明。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 64 位下 HANDLE 必须按指针传，默认 int 会被截断导致调用失败
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.IsProcessInJob.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        kernel32.IsProcessInJob.restype = ctypes.c_int
        result = ctypes.c_int(0)
        ok = kernel32.IsProcessInJob(
            kernel32.GetCurrentProcess(), None, ctypes.byref(result)
        )
    except Exception:  # noqa: BLE001 - 拿不到就别吓唬人
        return None
    return bool(result.value) if ok else None


def probe_state() -> dict[str, Any]:
    """综合判断服务状态。

    四态含义：

    ``running``  健康检查通过
    ``orphan``   有进程/端口在，但不是受管实例（runtime.json 缺失或不一致）
    ``stale``    端口空、无 runtime.json，但日志显示启动过 → 被 Job Object 回收
    ``stopped``  干净停止
    """
    health = {"reachable": False, "code": "backend_offline", "skipped": True}
    backend_port = port_open(BACKEND_PORT)
    frontend_port = port_open(FRONTEND_PORT)
    runtime = read_runtime()

    # 端口都没开就不必再等 HTTP 超时（Windows 上被拒的连接实测要 2 秒）。
    if backend_port:
        health = _health()

    if health.get("reachable"):
        contract = health.get("api_contract")
        state = "running"
        note = None
        if contract is not None and int(contract) != EXPECTED_API_CONTRACT:
            state = "running"
            note = (
                f"后端契约 {contract} 与控制面期望的 {EXPECTED_API_CONTRACT} 不一致，"
                "写操作可能失败。"
            )
    elif backend_port or runtime.get("exists"):
        state = "orphan"
        note = (
            "端口在监听但没有可用的 /health，或 runtime.json 与实际进程不符；"
            "需要在本机人工确认（控制面不会强杀）。"
        )
    elif _started_recently():
        state = "stale"
        note = (
            "日志显示启动过，但端口和 runtime.json 都不见了——很可能是启动它的父进程"
            "（终端/IDE）退出时把子进程一起回收了。用独立窗口或计划任务重跑控制 MCP。"
        )
    else:
        state = "stopped"
        note = None

    return {
        "state": state,
        "note": note,
        "ports": {
            "backend": {"port": BACKEND_PORT, "open": backend_port},
            "frontend": {"port": FRONTEND_PORT, "open": frontend_port},
        },
        "health": health,
        "runtime_json": runtime,
        "process": {
            "pid": os.getpid(),
            "in_job_object": in_job_object(),
            "note": (
                "在 Job Object 里：由本进程启动的服务可能随它退出而被回收。"
                "建议用独立窗口或计划任务启动控制 MCP。"
                if in_job_object()
                else None
            ),
        },
        "logs": {
            "startup_tail": tail_log("startup-console.log", 30),
            "backend_error_tail": tail_log("backend-error.log", 20),
        },
        "project_root": str(PROJECT_ROOT),
    }


# ------------------------------------------------------------------ 文件锁


class _ServiceLock:
    """防 Hermes 并发触发双重启/双重停。超 180 秒的锁视为陈旧自动接管。"""

    def __init__(self, path: Path | None = None) -> None:
        # 不写成默认参数：默认值在函数定义时就绑定，会绕过 monkeypatch
        self.path = path or LOCK_PATH
        self.acquired = False

    def __enter__(self) -> "_ServiceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0.0
            if age < LOCK_STALE_SECONDS:
                raise ControlError(
                    CODE_SERVICE_BUSY,
                    f"另一个生命周期操作正在进行（锁已存在 {int(age)} 秒）",
                    kind="business",
                    retryable=True,
                    hint=f"等 10 秒重试；若确认没有别的操作，可手动删除 {self.path}",
                ) from None
            try:
                self.path.unlink()
            except OSError:
                pass
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, f"{os.getpid()} {time.time():.0f}\n".encode())
        finally:
            os.close(fd)
        self.acquired = True
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.acquired = False


# ------------------------------------------------------------------ 动作


def _run(cmd: list[str], *, timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - 固定脚本路径，无用户输入拼接
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace")
        return (-1, output)
    return (completed.returncode, (completed.stdout or b"").decode("utf-8", "replace"))


def start(*, timeout: float = START_TIMEOUT) -> dict[str, Any]:
    """后台拉起 TokBrain（不弹浏览器），等就绪。"""
    with _ServiceLock():
        before = probe_state()
        if before["state"] == "running":
            return {
                "state": "running",
                "already_running": True,
                "elapsed": 0.0,
                "api_contract": before["health"].get("api_contract"),
                "url": f"http://127.0.0.1:{FRONTEND_PORT}",
            }
        if not START_SCRIPT.is_file():
            raise ControlError(
                CODE_START_FAILED,
                f"找不到启动脚本：{START_SCRIPT}",
                kind="local",
                hint="launchers 目录应包含 启动.cmd；确认 PROJECT_ROOT 正确。",
            )
        if before["state"] == "orphan" and not before["runtime_json"].get("exists"):
            raise ControlError(
                CODE_PORT_CONFLICT_UNTRACKED,
                f"{BACKEND_PORT} 端口被未记录的进程占用",
                kind="local",
                hint=(
                    "这不是 TokBrain 管理的实例，控制面不会动手。请在本机确认占用进程，"
                    "必要时手动处理后再启动。"
                ),
                detail=before["ports"],
            )

        old_sha = before["runtime_json"].get("sha")
        env = dict(os.environ)
        env["TOKBRAIN_SKIP_LOCAL_UI_OPEN"] = "1"

        started_at = time.monotonic()
        code, output = _run(
            ["cmd", "/c", str(START_SCRIPT)], timeout=20.0, env=env
        )
        # 启动脚本是 `start ""` 分离式的，返回 0 只代表"已发出启动指令"。

        deadline = started_at + timeout
        last_health: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_health = _health()
            runtime = read_runtime()
            fresh_instance = runtime.get("exists") and runtime.get("sha") != old_sha
            if last_health.get("reachable") and fresh_instance:
                elapsed = round(time.monotonic() - started_at, 1)
                warning = None
                if in_job_object():
                    warning = (
                        "本进程处在 Windows Job Object 里。由它启动的服务会在本次会话结束时"
                        "被一起回收（表现为'启动成功但过一会儿就没了'）。要长期运行，"
                        "请用独立 cmd 窗口或登录时计划任务启动控制 MCP。"
                    )
                return {
                    "state": "running",
                    "already_running": False,
                    "elapsed": elapsed,
                    "api_contract": last_health.get("api_contract"),
                    "url": f"http://127.0.0.1:{FRONTEND_PORT}",
                    "note": "浏览器未打开（TOKBRAIN_SKIP_LOCAL_UI_OPEN=1）",
                    "warning": warning,
                }
            time.sleep(0.5)

        raise ControlError(
            CODE_START_FAILED,
            f"启动 {int(timeout)} 秒后仍未就绪",
            kind="local",
            retryable=True,
            hint="看 logs.startup_tail；常见原因：.venv 缺依赖、前端未构建、ffmpeg 不可用。",
            detail={
                "returncode": code,
                "stdout_tail": output[-1500:],
                "health": last_health,
                "logs": {
                    "startup_tail": tail_log("startup-console.log", 30),
                    "backend_error_tail": tail_log("backend-error.log", 20),
                },
            },
        )


def stop(*, timeout: float = STOP_TIMEOUT) -> dict[str, Any]:
    """按 runtime.json 身份校验停止服务。

    完成判定四条件同时成立：/health 不通、8001 端口关闭、3001 端口关闭、
    runtime.json 消失。任一不满足只报错，**绝不动手 taskkill**。
    """
    with _ServiceLock():
        before = probe_state()
        if before["state"] == "stopped":
            return {"state": "stopped", "already_stopped": True, "elapsed": 0.0}

        runtime = before["runtime_json"]
        if not runtime.get("exists") and not port_open(BACKEND_PORT):
            return {"state": "stopped", "already_stopped": True, "elapsed": 0.0}

        if not STOP_SCRIPT.is_file():
            raise ControlError(
                CODE_STOP_INCOMPLETE,
                f"找不到停止脚本：{STOP_SCRIPT}",
                kind="local",
            )

        powershell = "powershell.exe"
        started_at = time.monotonic()
        code, output = _run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(STOP_SCRIPT),
            ],
            timeout=timeout + 10.0,
        )
        if code == -1:
            raise ControlError(
                CODE_STOP_INCOMPLETE,
                f"停止脚本 {int(timeout)} 秒未返回",
                kind="local",
                retryable=True,
                detail={"stdout_tail": output[-1500:]},
            )
        if code != 0:
            # stop.ps1 的失败通常是身份不匹配，原样转述比二次包装有用。
            detail = output.strip()[-1500:]
            if "身份" in detail or "identity" in detail.lower():
                raise ControlError(
                    CODE_STOP_REFUSED,
                    "停止被拒绝：runtime.json 记录的身份与当前进程不符",
                    kind="local",
                    hint="有进程占着 runtime.json 记录的 PID 但不是原实例。请在本机人工确认。",
                    detail={"returncode": code, "stdout_tail": detail},
                )
            if "powershell" in detail.lower() and "not recognized" in detail.lower():
                raise ControlError(
                    CODE_POWERSHELL_MISSING,
                    "找不到 powershell.exe",
                    kind="local",
                    hint="PowerShell 不在 PATH 里，控制面无法执行停止脚本。",
                )
            raise ControlError(
                CODE_STOP_INCOMPLETE,
                f"停止脚本返回 {code}",
                kind="local",
                detail={"returncode": code, "stdout_tail": detail},
            )

        deadline = started_at + timeout
        while time.monotonic() < deadline:
            if (
                not _health().get("reachable")
                and not port_open(BACKEND_PORT)
                and not port_open(FRONTEND_PORT)
                and not read_runtime().get("exists")
            ):
                return {
                    "state": "stopped",
                    "already_stopped": False,
                    "elapsed": round(time.monotonic() - started_at, 1),
                    "ports_released": True,
                }
            time.sleep(0.5)

        remaining = {
            "backend_open": port_open(BACKEND_PORT),
            "frontend_open": port_open(FRONTEND_PORT),
            "runtime_exists": read_runtime().get("exists"),
            "health_reachable": _health().get("reachable"),
        }
        busy = [k for k, v in remaining.items() if v]
        raise ControlError(
            CODE_STOP_INCOMPLETE,
            f"停止未完成，仍在：{', '.join(busy)}",
            kind="local",
            retryable=True,
            hint="控制面不会强杀进程。需要时在本机双击 停止.cmd 或手动确认。",
            detail={
                "returncode": code,
                "stdout_tail": output.strip()[-1500:],
                "remaining": remaining,
                "logs": {"startup_tail": tail_log("startup-console.log", 20)},
            },
        )


def restart(*, timeout: float = START_TIMEOUT) -> dict[str, Any]:
    stopped = stop()
    began = time.monotonic()
    started = start(timeout=timeout)
    downtime = round(time.monotonic() - began, 1)
    return {
        **started,
        "restarted": True,
        "already_running": False,
        "downtime_seconds": downtime,
        "stopped_first": stopped.get("state"),
    }


def ensure_started() -> dict[str, Any]:
    """幂等的"确保在跑"，给写操作做前置。"""
    state = probe_state()
    if state["state"] == "running":
        return {"state": "running", "already_running": True}
    return start()
