"""飞书同步桥（``bridge_feishu_obsidian.py``）的控制面。

为什么要单独管这个进程：飞书表的读取能力在 bridge 里，而 bridge 是**独立常驻进程**，
不在 TokBrain 的服务生命周期内（``启动飞书同步.cmd`` 单独双击启动，``start.ps1``
不碰它）。外部 agent 想完成「把飞书表里新加的视频导入并解析」，正确姿势不是自己读表
——那会和 bridge 抢同一批行、破坏「处理状态」回写——而是**驱动 bridge**。

本模块只做四件事：看状态、启动、停止、分批重投积压。

设计边界：

* 进程识别靠 WMI/CIM 的 CommandLine 精确匹配 ``bridge_feishu_obsidian.py``，
  不做「杀掉所有 python」这种危险动作；
* 队列读写直连 ``data/pipeline/dispatch.db``（bridge 自己也是这么干的，本机短事务），
  不经过任何 HTTP——因为根本没有这个接口；
* 重投**必须分批**。历史事故：120 条积压全是 429（Too Many Requests），
  一次性放开会立刻再撞一次同样的墙。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = PROJECT_ROOT / "bridge_feishu_obsidian.py"
DISPATCH_DB = PROJECT_ROOT / "data" / "pipeline" / "dispatch.db"
BRIDGE_LOG = PROJECT_ROOT / "data" / "logs" / "bridge-watch.log"

#: 终态。bridge 的 ``pending_items()`` 用 ``NOT IN TERMINAL`` 取件，
#: 所以把 failed 改回 queued 等于「重新投进慢轨」。
TERMINAL_STATES = ("processed", "needs_supplement", "failed", "awaiting_confirm")
ACTIVE_STATES = ("pending", "queued", "processing")
#: 预检已完成、等确认的条目。它们不在慢轨里，也不能算 in_flight——
#: 球在用户/agent 那边，不是在后台。
AWAITING_CONFIRM = "awaiting_confirm"

#: 单次重投上限。宁可让调用方多调几次，也不要一次把限流打爆。
MAX_REPLAY_PER_CALL = 20
DEFAULT_REPLAY_LIMIT = 5

#: 飞书多维表格定位。**必须显式注入**：``bridge_feishu_obsidian.py`` 里这两个变量
#: 默认空串，为空时 ``use_feishu`` 为假，会静默退回读本地 ``inbox.json``——
#: 表现为"bridge 在跑但飞书新行纹丝不动"，极难排查。
#: 与 ``启动飞书同步.cmd`` 和 ``app/services/sync_status.py`` 保持一致，换表要三处同改。
DEFAULT_APP_TOKEN = "WmDJbX7jaaiDvksBX22c7IuLnAf"
DEFAULT_TABLE_ID = "tblgX5UNFSZu31aj"
DEFAULT_VAULT = r"D:\ObsidianVault"

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _python() -> str:
    """优先用可用的下载环境，回退到项目 .venv。"""
    workbuddy = Path(r"C:\Users\xxsyl\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
    if workbuddy.is_file():
        return str(workbuddy)
    candidate = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if candidate.is_file():
        return str(candidate)
    return sys.executable


# ------------------------------------------------------------------ 进程


def _run(cmd: list[str], *, timeout: float = 25.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - 固定命令，无用户输入拼接
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return (-1, (exc.stdout or b"").decode("utf-8", "replace"))
    except OSError as exc:
        return (-2, f"无法启动子进程：{exc}")
    return (completed.returncode, (completed.stdout or b"").decode("utf-8", "replace"))


def _query_processes() -> tuple[list[dict[str, Any]], str | None]:
    """列出所有跑 bridge 的 python 进程。

    用 CIM 而不是 tasklist，因为 tasklist 看不到命令行——没有命令行就无法区分
    「这是 bridge」还是「这是别的 python 脚本」。
    """
    script = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Select-Object ProcessId,CommandLine,CreationDate "
        "| ConvertTo-Json -Compress"
    )
    code, output = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
    if code != 0:
        return [], (output or "powershell 返回非零").strip()[:300]
    text = (output or "").strip()
    if not text:
        return [], None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], f"进程列表解析失败：{exc}"
    if isinstance(payload, dict):
        payload = [payload]

    found: list[dict[str, Any]] = []
    for row in payload:
        command = str(row.get("CommandLine") or "")
        if "bridge_feishu_obsidian.py" not in command:
            continue
        found.append(
            {
                "pid": row.get("ProcessId"),
                "command_line": command,
                "created": row.get("CreationDate"),
                "mode": "watch" if "--watch" in command else "once",
            }
        )
    return found, None


def processes() -> dict[str, Any]:
    items, error = _query_processes()
    data: dict[str, Any] = {"count": len(items), "items": items}
    if error:
        data["error"] = error
    return data


def _log_activity() -> dict[str, Any]:
    """日志尾巴是判断「活着但卡住」的唯一线索。"""
    if not BRIDGE_LOG.is_file():
        return {"exists": False}
    try:
        stat = BRIDGE_LOG.stat()
        size = stat.st_size
        with BRIDGE_LOG.open("rb") as handle:
            if size > 32 * 1024:
                handle.seek(size - 32 * 1024)
            chunk = handle.read()
    except OSError as exc:
        return {"exists": True, "error": repr(exc)}
    if chunk[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = chunk.decode("utf-16", "replace")
    elif chunk.count(b"\x00") > max(8, len(chunk) // 8):
        text = chunk.decode("utf-16-le", "replace")
    else:
        text = chunk.decode("utf-8", "replace")
    lines = [line for line in text.splitlines() if line.strip()]
    age = round(time.time() - stat.st_mtime, 1)
    return {
        "exists": True,
        "path": str(BRIDGE_LOG),
        "size": size,
        "idle_seconds": age,
        "idle_hint": _idle_hint(age),
        "tail": lines[-8:],
    }


def _idle_hint(age: float) -> str:
    if age < 180:
        return "刚有输出，正常"
    if age < 900:
        return "3 分钟以上没输出；一轮处理慢任务时属正常"
    return "超过 15 分钟没输出；可能卡住或进程已死，用 tb_sync_status 看进程是否还在"


# ------------------------------------------------------------------ 队列


def _connect(readonly: bool = True, timeout: float = 20.0):
    if not DISPATCH_DB.is_file():
        return None
    if readonly:
        uri = f"file:{DISPATCH_DB.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        con = sqlite3.connect(str(DISPATCH_DB), timeout=timeout)
    con.row_factory = sqlite3.Row
    return con


def queue_counts() -> dict[str, Any]:
    con = _connect()
    if con is None:
        return {"available": False, "reason": "dispatch.db 不存在（bridge 从未跑过）"}
    try:
        rows = con.execute("SELECT status, COUNT(*) c FROM dispatch GROUP BY status").fetchall()
        counts = {row["status"]: int(row["c"]) for row in rows}
        total = sum(counts.values())
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"读取失败：{exc}"}
    finally:
        con.close()

    pending = sum(counts.get(state, 0) for state in ACTIVE_STATES)
    failed = counts.get("failed", 0)
    awaiting = counts.get(AWAITING_CONFIRM, 0)
    return {
        "available": True,
        "by_state": counts,
        "total": total,
        "in_flight": pending,
        "failed": failed,
        "done": counts.get("processed", 0),
        "needs_supplement": counts.get("needs_supplement", 0),
        "awaiting_confirm": awaiting,
        "note": (
            "in_flight 是还没走完的条目；awaiting_confirm 是预检完、等确认解析的条目"
            "（用 tb_sync_pending 看，tb_sync_confirm 放行）；"
            "failed 是终态，必须显式重投才会再试一次。"
        ),
    }


def recent_failures(limit: int = 5) -> list[dict[str, Any]]:
    con = _connect()
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT record_id, link, error_code, error_message, updated_at "
            "FROM dispatch WHERE status='failed' ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit or 5), 20)),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [
        {
            "record_id": row["record_id"],
            "link": (row["link"] or "")[:120],
            "error_code": row["error_code"],
            "message": (row["error_message"] or "")[:200],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def recent_done(limit: int = 5) -> list[dict[str, Any]]:
    """最近成功入库的条目。带 work_id，调用方可以接着查作品或检索。"""
    con = _connect()
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT record_id, link, work_id, updated_at FROM dispatch "
            "WHERE status='processed' ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit or 5), 20)),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [
        {
            "record_id": row["record_id"],
            "link": (row["link"] or "")[:120],
            "work_id": row["work_id"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def _detect_mode() -> dict[str, Any]:
    """推断常驻桥当前用哪种导入模式。

    ``FS_NO_DOUYIN`` 是启动时的环境变量，进程列表里看不到，所以从日志里找最近
    一次导入的标记。这个信息很关键：同样是 replay，metadata_only 只会建占位，
    download 才会真的下载并解析。
    """
    activity = _log_activity()
    tail = activity.get("tail") or []
    for line in reversed(tail):
        if "[import-link:metadata-only]" in line:
            return {
                "mode": "metadata_only",
                "detail": "最近一次导入只抓元数据，**没有下载视频**",
                "evidence": line.strip()[:120],
            }
        if "[import-link]" in line:
            return {
                "mode": "download",
                "detail": "最近一次导入走了完整下载 + 解析入库",
                "evidence": line.strip()[:120],
            }
    return {"mode": "unknown", "detail": "日志里还没有导入记录，无法判断"}


# ------------------------------------------------------------------ 状态


def table_config() -> dict[str, str]:
    """当前会连哪张飞书表。排查「在跑但没反应」时第一个要看的东西。"""
    return {
        "app_token": os.environ.get("FEISHU_BITABLE_APP_TOKEN") or DEFAULT_APP_TOKEN,
        "table_id": os.environ.get("FEISHU_BITABLE_TABLE_ID") or DEFAULT_TABLE_ID,
        "source": (
            "环境变量"
            if os.environ.get("FEISHU_BITABLE_APP_TOKEN")
            else "内置默认（与 启动飞书同步.cmd 一致）"
        ),
    }


def status(*, include_tail: bool = True) -> dict[str, Any]:
    procs = processes()
    queue = queue_counts()
    running = procs["count"] > 0

    data: dict[str, Any] = {
        "running": running,
        "processes": procs["items"],
        "table": table_config(),
        "mode": _detect_mode(),
        "queue": queue,
        "recent_done": recent_done(3) if queue.get("available") else [],
        "recent_failures": recent_failures(5) if queue.get("available") else [],
        "log": _log_activity() if include_tail else {"exists": BRIDGE_LOG.is_file()},
    }
    if procs.get("error"):
        data["process_error"] = procs["error"]

    hints: list[str] = []
    if not running:
        hints.append(
            "bridge 没在跑：飞书表新增的视频**不会**被自动导入。"
            "需要时调 tb_sync_start，或本机双击 启动飞书同步.cmd。"
        )
    if queue.get("failed"):
        hints.append(
            f"有 {queue['failed']} 条 failed（终态，不会自动重试）。"
            "确认要重试就调 tb_sync_replay，一次别超过 20 条。"
        )
    if running and data["log"].get("idle_seconds", 0) > 900:
        hints.append(data["log"]["idle_hint"])
    if not DISPATCH_DB.is_file():
        hints.append("dispatch.db 不存在，说明 bridge 从来没有成功跑过一轮。")
    data["hints"] = hints or ["没有发现明显问题"]
    return data


# ------------------------------------------------------------------ 动作


def wait_idle(*, timeout: float = 80.0, poll: float = 3.0) -> dict[str, Any]:
    """等队列跑到空闲（``in_flight`` 归零），返回这段时间的变化。

    为什么不直接等某个 work_id：bridge 是整轮处理，调用方给不出 job_id。
    所以用「队列是否还压着东西」当进度信号，再配 ``recent_done`` 看结果。

    超时不是失败：单个视频的 ASR + OCR 可能要几分钟，MCP 传输层等不了那么久。
    返回 ``idle=false`` 时调用方应该再调一次，**不要**重新导入。
    """
    started = time.monotonic()
    before = queue_counts()
    if not before.get("available"):
        return {"idle": False, "reason": before.get("reason") or "队列不可读"}

    while True:
        current = queue_counts()
        in_flight = int(current.get("in_flight") or 0)
        elapsed = round(time.monotonic() - started, 1)

        if in_flight == 0:
            b = before.get("by_state") or {}
            a = current.get("by_state") or {}
            delta = {
                key: int(a.get(key, 0)) - int(b.get(key, 0))
                for key in set(a) | set(b)
                if int(a.get(key, 0)) != int(b.get(key, 0))
            }
            data: dict[str, Any] = {
                "idle": True,
                "elapsed": elapsed,
                "before": b,
                "after": a,
                "delta": delta,
                "recent_done": recent_done(3),
                "failed_now": current.get("failed"),
            }
            if delta.get("processed", 0) > 0:
                data["note"] = (
                    f"本轮新增 {delta['processed']} 条入库。"
                    "用 tb_work_get 看内容，或 tb_search 验证检索是否命中。"
                )
            elif delta.get("failed", 0) > 0:
                data["note"] = (
                    f"本轮新增 {delta['failed']} 条失败。看 recent_failures 里的原因，"
                    "确认后可用 tb_sync_replay 重投。"
                )
            elif not delta:
                data["note"] = (
                    "队列没有变化。可能是桥还没捡起（每轮约 60 秒），"
                    "也可能桥没在跑——先看 tb_sync_status 的 running。"
                )
            else:
                data["note"] = "队列已空闲，变化见 delta。"
            return data

        if time.monotonic() - started >= max(0.5, timeout):
            return {
                "idle": False,
                "elapsed": elapsed,
                "in_flight": in_flight,
                "before": before.get("by_state"),
                "after": current.get("by_state"),
                "next_step": (
                    "还在处理中（单 worker 串行，一个视频要几分钟）。"
                    "直接再调一次 tb_sync_wait，不要重新导入。"
                ),
            }
        time.sleep(max(1.0, poll))


def _with_localhost_bypass(current: str) -> str:
    """把回环地址并进 NO_PROXY，保留调用方原有的条目。"""
    parts = [item.strip() for item in (current or "").split(",") if item.strip()]
    for host in ("127.0.0.1", "localhost", "::1"):
        if host not in parts:
            parts.append(host)
    return ",".join(parts)


def start(
    *, wait_seconds: float = 6.0, download: bool = False, auto_confirm: bool = False
) -> dict[str, Any]:
    """拉起 ``--watch`` 常驻进程。

    ``download=False``（默认，与 bridge 自身设计一致）走 ``metadata_only``：只抓标题
    作者来源，**不下载视频**，视频要人工补件后才解析。
    ``download=True`` 设 ``FS_NO_DOUYIN=0``，让 bridge 真的去下载——会撞平台风控，
    且历史上正是这条路径把 120 条打成 429，慎用。

    ``auto_confirm=False``（默认）让桥在预检完成后停住：链接先由 f2 补齐标题/作者，
    然后回写飞书「待确认」，等网页或 ``tb_sync_confirm`` 放行才入库。
    设 True 回到旧行为（预检完直接确认并送入库队列）。
    """
    current = processes()
    if current["count"] > 0:
        return {
            "started": False,
            "already_running": True,
            "processes": current["items"],
            "note": "已经在跑，没有重复启动。",
        }
    if not BRIDGE_SCRIPT.is_file():
        return {"started": False, "error": f"找不到 {BRIDGE_SCRIPT}"}

    BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 否则中文输出按 cp936 写进日志会乱码
    # 不注入这两个变量，bridge 会退回读本地 inbox.json，飞书表根本不连
    env.setdefault("FEISHU_BITABLE_APP_TOKEN", DEFAULT_APP_TOKEN)
    env.setdefault("FEISHU_BITABLE_TABLE_ID", DEFAULT_TABLE_ID)
    env.setdefault("VAULT_PATH", DEFAULT_VAULT)
    env["FS_NO_DOUYIN"] = "0" if download else "1"
    env["FS_AUTO_CONFIRM"] = "1" if auto_confirm else "0"
    lark_exe = Path(r"C:\Users\xxsyl\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe")
    if lark_exe.is_file():
        env["LARK_CLI_EXE"] = str(lark_exe)
        env["LARK_CLI_DIR"] = str(lark_exe.parent.parent)
    # WorkBuddy 环境提供 yt-dlp/Playwright；项目环境提供 requests 等桥依赖。
    # 两者均在本机，按路径合并，避免把下载能力锁死在 .venv。
    project_site = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
    if project_site.is_dir():
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in [str(project_site), str(PROJECT_ROOT), existing_pythonpath] if part
        )
    # bridge 用 requests，而 requests 默认 trust_env：系统里配了 HTTP_PROXY
    # （Clash/VPN/CI 都会配）时，连 http://127.0.0.1:8001 也会被推给代理，
    # 代理按 absolute-form 转发，后端收到 path 是 "http://127.0.0.1:8001/api/..."
    # 直接 404。实测踩过：POST 建批成功、GET 批次 404。
    env["NO_PROXY"] = env["no_proxy"] = _with_localhost_bypass(
        env.get("NO_PROXY") or env.get("no_proxy") or ""
    )

    try:
        handle = BRIDGE_LOG.open("ab")
    except OSError as exc:
        return {"started": False, "error": f"打不开日志文件：{exc}"}

    try:
        args = [_python(), "-u", str(BRIDGE_SCRIPT), "--watch"]
        if download:
            args.append("--download")
        if auto_confirm:
            args.append("--auto-confirm")
        proc = subprocess.Popen(  # noqa: S603 - 固定脚本路径，无用户输入拼接
            args,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as exc:
        handle.close()
        return {"started": False, "error": f"启动失败：{exc}"}
    finally:
        try:
            handle.close()
        except OSError:
            pass

    deadline = time.monotonic() + max(1.0, wait_seconds)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = _log_activity()
            return {
                "started": False,
                "error": f"进程启动后立刻退出，退出码 {proc.returncode}",
                "log_tail": tail.get("tail", []),
            }
        time.sleep(0.4)

    if auto_confirm:
        note = (
            "常驻轮询已启动，auto_confirm=true：预检完直接确认并送入库队列，不再问。"
        )
    elif download:
        note = (
            "常驻轮询已启动：新行先由 f2 预检补齐标题/作者，然后停在待确认；"
            "tb_sync_confirm 放行后会真下载视频并解析。"
        )
    else:
        note = (
            "常驻轮询已启动：新行只做元数据预检（不碰视频），然后停在待确认；"
            "tb_sync_confirm 放行后入库。要真下载请用 download=true 重启。"
        )

    data: dict[str, Any] = {
        "started": True,
        "pid": proc.pid,
        "mode": "watch",
        "download": download,
        "auto_confirm": auto_confirm,
        "log": str(BRIDGE_LOG),
        "table": {
            "app_token": env["FEISHU_BITABLE_APP_TOKEN"],
            "table_id": env["FEISHU_BITABLE_TABLE_ID"],
        },
        "note": note,
    }
    if _in_job_object():
        data["warning"] = (
            "当前进程在 Windows Job Object 里，bridge 可能随父会话结束被回收。"
            "如果 tb_sync_status 很快变回没在跑，请改用独立 cmd 窗口或计划任务启动。"
        )
    return data


def _in_job_object() -> bool:
    """与控制面共用同一套判断：从 IDE/终端起，子进程会被父会话连带回收。"""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        result = ctypes.c_int(0)
        ok = kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(result))
    except Exception:  # noqa: BLE001 - 拿不到就不吓唬人
        return False
    return bool(result.value) if ok else False


def stop(*, timeout: float = 15.0) -> dict[str, Any]:
    """停止 bridge。只停命令行里明确带 ``bridge_feishu_obsidian.py`` 的进程。"""
    procs = processes()
    if procs["count"] == 0:
        return {"stopped": False, "already_stopped": True, "note": "没有在跑的 bridge 进程。"}

    pids = [int(item["pid"]) for item in procs["items"] if str(item.get("pid", "")).isdigit()]
    killed: list[int] = []
    failed: list[dict[str, Any]] = []
    for pid in pids:
        code, output = _run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=timeout)
        if code == 0:
            killed.append(pid)
        else:
            failed.append({"pid": pid, "code": code, "output": output.strip()[:200]})

    time.sleep(0.8)
    remaining = processes()
    return {
        "stopped": remaining["count"] == 0,
        "killed": killed,
        "failed": failed,
        "remaining": remaining["items"],
        "note": (
            "已停止。停止期间飞书表的新行不会被导入，恢复后从游标继续。"
            if remaining["count"] == 0
            else "仍有进程存活，可能是权限不足（试试本机人工结束）。"
        ),
    }


# ------------------------------------------------- 待确认（预检完成、等放行）


def _awaiting_rows(limit: int) -> list[dict[str, Any]] | None:
    """取待确认条目。老库没有 batch_id 列时退化为空串，而不是整个工具报错。"""
    con = _connect()
    if con is None:
        return None
    take = max(1, min(int(limit or 20), 100))
    try:
        try:
            rows = con.execute(
                "SELECT record_id, link, tags, batch_id, note_path, updated_at "
                "FROM dispatch WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (AWAITING_CONFIRM, take),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            # dispatch.db 还是旧版（bridge 尚未用新版 dispatch_store 跑过一轮）
            rows = con.execute(
                "SELECT record_id, link, tags, note_path, updated_at "
                "FROM dispatch WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (AWAITING_CONFIRM, take),
            ).fetchall()
            return [{**dict(row), "batch_id": ""} for row in rows]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def pending_confirm(limit: int = 20) -> dict[str, Any]:
    """列出「预检完成、等确认解析」的飞书行。

    这些行的标题/作者/封面已经由 f2 预检补齐（在批次条目里），所以这里**不联网**，
    只把批次里已有的信息取出来给调用方看——问用户「要不要解析」时得说清是哪条。
    """
    from .client import Client
    from .errors import ControlError

    rows = _awaiting_rows(limit)
    if rows is None:
        return {"available": False, "count": 0, "items": [], "reason": "dispatch.db 不可读"}

    items: list[dict[str, Any]] = []
    client: Client | None = None
    for row in rows:
        entry: dict[str, Any] = {
            "record_id": row["record_id"],
            "link": (row["link"] or "")[:200],
            "tags": row["tags"] or "",
            "batch_id": row["batch_id"] or "",
            "updated_at": row["updated_at"],
        }
        if entry["batch_id"]:
            try:
                client = client or Client()
                view = client.get_batch(entry["batch_id"])
            except Exception as exc:  # noqa: BLE001 - 预览失败不该炸掉整个列表
                entry["preview_error"] = str(exc)[:200]
            else:
                preview = []
                for item in view.get("items") or []:
                    preview.append(
                        {
                            "item_id": item.get("id"),
                            "status": item.get("status"),
                            "title": (item.get("title") or "")[:120],
                            "author": item.get("author_name"),
                            "duration_seconds": item.get("duration_seconds"),
                            "platform_work_id": item.get("platform_work_id"),
                        }
                    )
                entry["preview"] = preview
        items.append(entry)

    return {
        "available": True,
        "count": len(items),
        "items": items,
        "note": (
            "这些是预检已完成、还没解析的链接。确认要解析就调 tb_sync_confirm；"
            "不想要就让它在飞书表里留着，或把「处理状态」改成别的值。"
        ),
    }


def confirm_pending(record_ids: list[str], *, collection: str | int | None = None) -> dict[str, Any]:
    """对指定飞书行执行「确认 → 入库」，把球踢回后台。

    只处理 ``awaiting_confirm`` 的行：已 processed / failed 的一律跳过，避免误操作
    把两个月前的失败条目重新拉起来跑。
    """
    from .client import Client
    from .errors import ControlError

    wanted = [str(r).strip() for r in (record_ids or []) if str(r).strip()]
    if not wanted:
        raise ControlError("invalid_argument", "record_ids 不能为空", kind="usage")

    con = _connect(readonly=False)
    if con is None:
        raise ControlError(
            "queue_missing", "dispatch.db 不存在，没有可确认的条目", kind="business"
        )

    client = Client()
    result: dict[str, Any] = {"confirmed": [], "skipped": [], "failed": [], "work_ids": []}
    try:
        for record_id in wanted:
            try:
                row = con.execute(
                    "SELECT record_id, status, batch_id, link FROM dispatch WHERE record_id=?",
                    (record_id,),
                ).fetchone()
            except sqlite3.Error:
                # 旧库没有 batch_id 列：按「缺 batch_id」处理，提示重新投一次。
                row = con.execute(
                    "SELECT record_id, status, '' AS batch_id, link FROM dispatch"
                    " WHERE record_id=?",
                    (record_id,),
                ).fetchone()
            if row is None:
                result["failed"].append({"record_id": record_id, "reason": "队列里没有这条"})
                continue
            if row["status"] != AWAITING_CONFIRM:
                result["skipped"].append(
                    {"record_id": record_id, "reason": f"状态是 {row['status']}，不是待确认"}
                )
                continue
            batch_id = (row["batch_id"] or "").strip()
            if not batch_id:
                result["failed"].append(
                    {"record_id": record_id, "reason": "没有 batch_id，重新投一次让桥补上"}
                )
                continue

            try:
                view = client.get_batch(batch_id)
                ready = [
                    i["id"]
                    for i in view.get("items") or []
                    if str(i.get("status") or "") in ("ready", "needs_local_file")
                ]
                if not ready:
                    result["failed"].append(
                        {"record_id": record_id, "reason": "批次里没有可确认的条目（预检没过？）"}
                    )
                    continue
                item_collection = None
                if collection is not None:
                    item_collection = client.find_collection(collection)
                    if not item_collection:
                        created = client.create_collection(str(collection))
                        item_collection = created.get("id") or (
                            created.get("collection") or {}
                        ).get("id")
                if item_collection:
                    client.confirm_batch(
                        batch_id,
                        items=[
                            {"item_id": item_id, "collection_id": item_collection}
                            for item_id in ready
                        ],
                    )
                else:
                    client.confirm_batch(batch_id, item_ids=ready)

                after = client.get_batch(batch_id)
                work_ids = [
                    int(i["existing_work_id"])
                    for i in after.get("items") or []
                    if i.get("existing_work_id")
                ]
                if not work_ids:
                    result["failed"].append(
                        {"record_id": record_id, "reason": "确认后没拿到 work_id"}
                    )
                    continue
                job = client.ingest(work_ids)
            except ControlError as exc:
                result["failed"].append(
                    {"record_id": record_id, "reason": f"{exc.code}: {exc.message}"}
                )
                continue

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            with con:
                con.execute(
                    "UPDATE dispatch SET status='processed', work_id=?, error_code='', "
                    "error_message='', updated_at=? WHERE record_id=?",
                    (work_ids[0], now, record_id),
                )
            result["confirmed"].append(record_id)
            result["work_ids"].extend(work_ids)
            result.setdefault("jobs", []).append(
                {"record_id": record_id, "batch_id": batch_id, "job_id": job.get("id"),
                 "work_ids": work_ids}
            )
    finally:
        con.close()

    result["count"] = len(result["confirmed"])
    result["note"] = (
        "已确认并送入库队列，解析在后台排队（单 worker 串行）。"
        "用 tb_job_wait / tb_work_list 跟进；飞书「处理状态」下一轮会回写为已归档。"
        if result["confirmed"]
        else "没有任何条目被确认，看 failed / skipped 里的原因。"
    )
    return result


def replay(
    limit: int = DEFAULT_REPLAY_LIMIT,
    *,
    newest_first: bool = False,
    record_ids: list[str] | None = None,
    include_processed: bool = False,
    caller: str | None = None,
) -> dict[str, Any]:
    """把 ``limit`` 条 failed 重投回慢轨（状态改为 queued）。

    为什么要分批：这批积压的失败原因几乎全是 429 限流，一次性放开就会立刻再撞一次。

    ``newest_first=True`` 时优先重投**最近失败**的条目。用户说「我刚加的那条」时用这个
    ——按时间升序会先翻出两个月前的旧链接。

    ``record_ids`` 用于精确重投：用户指着飞书表里某几行说「把这几个补齐」时，
    按数量挑会挑错行（积压里绝大多数是两个月前的旧链接）。给了它就忽略 limit
    的挑选语义，只处理点名且当前仍是 failed 的条目。
    """
    from .errors import CODE_INVALID_ARGUMENT, ControlError

    wanted: list[str] = []
    if include_processed and not record_ids:
        raise ControlError(
            CODE_INVALID_ARGUMENT,
            "include_processed 只能与明确的 record_ids 一起使用",
            kind="usage",
        )
    if record_ids:
        wanted = list(
            dict.fromkeys(str(rid).strip() for rid in record_ids if str(rid).strip())
        )
        if not wanted:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                "record_ids 里没有有效的 record_id",
                kind="usage",
            )
        if len(wanted) > MAX_REPLAY_PER_CALL:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                f"单次最多重投 {MAX_REPLAY_PER_CALL} 条，收到 {len(wanted)} 条",
                kind="usage",
                hint="分批调用，每批之间用 tb_sync_wait 看队列消化情况再继续。",
            )

    try:
        count = int(limit)
    except (TypeError, ValueError):
        raise ControlError(CODE_INVALID_ARGUMENT, "limit 必须是整数", kind="usage") from None
    if count < 1:
        raise ControlError(CODE_INVALID_ARGUMENT, "limit 至少为 1", kind="usage")
    if count > MAX_REPLAY_PER_CALL:
        raise ControlError(
            CODE_INVALID_ARGUMENT,
            f"单次最多重投 {MAX_REPLAY_PER_CALL} 条，收到 {count} 条",
            kind="usage",
            hint="分批调用，每批之间用 tb_sync_wait 看队列消化情况再继续。",
        )

    con = _connect(readonly=False)
    if con is None:
        raise ControlError(
            "queue_missing",
            "dispatch.db 不存在，没有可重投的队列",
            kind="business",
            hint="先让 bridge 跑一轮（tb_sync_start），队列会自己建出来。",
        )

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    order = "DESC" if newest_first else "ASC"
    skipped: list[str] = []
    try:
        if wanted:
            placeholders = ",".join("?" * len(wanted))
            rows = con.execute(
                f"SELECT record_id, link FROM dispatch WHERE status IN ('failed'" + (",'processed'" if include_processed else "") + ") "
                f"AND record_id IN ({placeholders})",
                wanted,
            ).fetchall()
            found = {row["record_id"] for row in rows}
            skipped = [rid for rid in wanted if rid not in found]
            # 保持调用方给的顺序，便于对照飞书表逐行核对
            order_map = {rid: index for index, rid in enumerate(wanted)}
            rows = sorted(rows, key=lambda row: order_map.get(row["record_id"], 0))
        else:
            rows = con.execute(
                f"SELECT record_id, link FROM dispatch WHERE status IN ('failed'" + (",'processed'" if include_processed else "") + ") "
                f"ORDER BY updated_at {order} LIMIT ?",
                (count,),
            ).fetchall()
        ids = [row["record_id"] for row in rows]
        links = [str(row["link"] or "")[:120] for row in rows]
        if ids:
            con.executemany(
                "UPDATE dispatch SET status='queued', error_code='', error_message='', "
                "updated_at=? WHERE record_id=?",
                [(now, rid) for rid in ids],
            )
            con.commit()
        remaining = con.execute(
            "SELECT COUNT(*) FROM dispatch WHERE status='failed'"
        ).fetchone()[0]
    except sqlite3.Error as exc:
        con.close()
        raise ControlError(
            "queue_write_failed",
            f"写队列失败：{exc}",
            kind="business",
            hint="可能是 bridge 正在批量写队列；等一会儿再试。",
        ) from exc
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass

    running = processes()["count"] > 0
    mode = _detect_mode()
    data: dict[str, Any] = {
        "requeued": len(ids),
        "record_ids": ids,
        "links": links,
        "order": "requested" if wanted else ("newest_first" if newest_first else "oldest_first"),
        "failed_remaining": int(remaining),
        "bridge_running": running,
        "mode": mode,
    }
    if wanted:
        data["requested"] = wanted
    if skipped:
        data["skipped"] = skipped
        data["skipped_hint"] = (
            "这些 record_id 当前不是可重投状态（可能已处理、或库里没有）；"
            "确认拼写后再试，或先用 tb_sync_pending 看当前队列。"
        )
    if include_processed:
        data["note"] = "已显式允许 processed 重跑；如桥为 download 模式，将重新触发下载/解析。"
    if mode.get("mode") == "metadata_only":
        data["warning"] = (
            "当前桥是 metadata_only 模式：重投后只会重建占位，**不会下载视频**。"
            "要真的下载并解析，先 tb_sync_stop，再 tb_sync_start(download=true)，然后重投。"
        )
    if not ids:
        data["note"] = "队列里没有 failed 条目，无需重投。"
    elif not running:
        data["note"] = (
            "已改回 queued，但 bridge 没在跑，**不会被处理**。"
            "调 tb_sync_start 后才会进入慢轨。"
        )
    else:
        data["note"] = (
            "已改回 queued，bridge 下一轮（约 60 秒内）会捡起。"
            "用 tb_sync_status 看 in_flight 变化，跑完再放下一批。"
        )
    if int(remaining) > 0:
        data["next_step"] = f"还剩 {remaining} 条 failed，确认上一批消化完再调 tb_sync_replay。"
    return data
