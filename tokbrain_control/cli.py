"""命令行入口：``python -m tokbrain_control`` / ``tbctl.cmd``。

退出码与 :mod:`tokbrain_control.errors` 对齐：

====  ====================================================
0     成功
1     业务失败（后端拒绝、任务终态失败、启动/停止未完成）
2     参数错
3     后端不在线
====  ====================================================
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__, commands, service
from . import paths as media_paths
from .client import LIBRARY_STATES
from .errors import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_OFFLINE,
    EXIT_USAGE,
)

HEADER = "TokBrain 控制面"


# ------------------------------------------------------------------ 渲染


def _short(value: Any, limit: int = 88) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _row(item: dict[str, Any], keys: Sequence[str]) -> str:
    parts = []
    for key in keys:
        if key in item and item[key] not in (None, "", [], {}):
            parts.append(f"{key}={_short(item[key], 60)}")
    return "  ".join(parts) if parts else _short(item, 120)


def _pretty(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, val in value.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{pad}{key}:")
                lines.append(_pretty(val, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_short(val)}")
        return "\n".join(lines) if lines else f"{pad}(空)"
    if isinstance(value, list):
        if not value:
            return f"{pad}(空)"
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{pad}- {_row(item, list(item.keys()))}")
            else:
                lines.append(f"{pad}- {_short(item)}")
        return "\n".join(lines)
    return f"{pad}{_short(value, 400)}"


def _render_search(data: dict[str, Any]) -> str:
    lines = [
        f"模式: {data.get('mode')}  命中: {data.get('count')}  查询: {data.get('query')}"
    ]
    if data.get("collection"):
        lines.append(f"合集过滤: {data['collection']}")
    if data.get("note"):
        lines.append(f"说明: {data['note']}")
    lines.append("")
    for index, item in enumerate(data.get("results") or [], 1):
        lines.append(
            f"{index}. [{_short(item.get('score'))}] {item.get('title')}"
            f"  ({item.get('collection') or '-'})  {item.get('timestamp')}"
        )
        snippet = str(item.get("snippet") or "")
        if snippet:
            lines.append(f"   {snippet}")
        if item.get("url"):
            lines.append(f"   {item['url']}")
    if not data.get("results"):
        lines.append("（没有命中。可以先 tb_status 确认库里有内容，或换个说法。）")
    lines.append("")
    lines.append("以上为证据片段，不含生成答案。")
    return "\n".join(lines)


def _render_status(data: dict[str, Any]) -> str:
    backend = data.get("backend") or {}
    library = data.get("library") or {}
    lines = [
        f"后端: {'在线' if backend.get('alive') else '离线'}"
        f"  契约: {backend.get('api_contract')}",
        f"知识库: 在库 {library.get('in_library')}  "
        f"待入库 {library.get('pending')}  异常 {library.get('issues')}  "
        f"合集 {library.get('collections')}",
    ]
    coord = (data.get("coordinators") or {}).get("status")
    if coord:
        lines.append(f"协调器: {coord}")
    jobs = data.get("jobs") or []
    if jobs:
        lines.append("最近任务:")
        for job in jobs:
            lines.append(
                f"  - {job.get('id')}  {job.get('job_type')}  {job.get('state')}"
                f"  {job.get('processed_items')}/{job.get('total_items')}"
                f"  {job.get('message') or ''}"
            )
    extra = {k: v for k, v in data.items() if k not in {"backend", "library", "jobs", "coordinators"}}
    if extra:
        lines.append("")
        lines.append(_pretty(extra))
    return "\n".join(lines)


def _special(tool: str, data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if tool == "tb_status":
        return _render_status(data)
    if tool == "tb_search":
        return _render_search(data)
    return None


def _emit(envelope: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return

    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        print(f"失败: {error.get('message')}")
        print(f"  错误码: {error.get('code')}  类型: {error.get('kind')}"
              f"  可重试: {'是' if error.get('retryable') else '否'}")
        if error.get("hint"):
            print(f"  提示: {error['hint']}")
        if error.get("detail"):
            print("  详情:")
            print(_pretty(error["detail"], 2))
        if envelope.get("audit_id"):
            print(f"  审计: {envelope['audit_id']}")
        return

    data = envelope.get("data")
    rendered = _special(str(envelope.get("tool")), data)
    print(rendered if rendered is not None else _pretty(data))
    if envelope.get("audit_id"):
        print(f"\n[审计] {envelope['audit_id']}")
    print(f"[耗时] {envelope.get('elapsed_ms')} ms")


# ------------------------------------------------------------------ doctor


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _firewall_rule() -> dict[str, Any]:
    if not shutil.which("netsh"):
        return {"checked": False, "reason": "netsh 不在 PATH"}
    try:
        proc = subprocess.run(  # noqa: S603
            ["netsh", "advfirewall", "firewall", "show", "rule",
             "name=TokBrain Control MCP 8011"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"checked": False, "reason": repr(exc)}
    present = proc.returncode == 0 and "8011" in (proc.stdout or "")
    return {"checked": True, "present": present}


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.10.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


def doctor(*, caller: str | None = None) -> dict[str, Any]:
    def impl() -> dict[str, Any]:
        state = service.probe_state()
        lan = _lan_ip()
        ports = {
            "backend_8001": _port_open(8001),
            "frontend_3001": _port_open(3001),
            "control_8011": _port_open(8011),
        }
        problems: list[str] = []
        if state["state"] != "running":
            problems.append(f"后端状态是 {state['state']}，写操作会失败")
        if not ports["control_8011"]:
            problems.append("控制 MCP（8011）没在监听——Hermes 连不上")
        in_job = service.in_job_object()
        if in_job:
            problems.append(
                "当前进程在 Windows Job Object 里：由它启动的服务会随会话结束被回收"
            )
        firewall = _firewall_rule()
        if firewall.get("checked") and not firewall.get("present"):
            problems.append("防火墙没有放行 8011（双击 mcp_server/放行控制端口.cmd）")

        media = media_paths.describe(include_samples=False)
        if not media["configured"]:
            problems.append(
                "没配媒体路径约定（control.env 的 TOKBRAIN_CONTROL_MEDIA_ROOTS）："
                "大肥鱼只能传本机绝对路径，NAS 路径会被拒"
            )
        elif media["usable_roots"] == 0:
            problems.append(
                "媒体目录约定已配置，但没有一个能访问——NAS 映射盘可能掉线"
                "（双击 mcp_server/挂载NAS媒体盘.cmd）"
            )

        return {
            "version": __version__,
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "project_root": str(service.PROJECT_ROOT),
            "lan_ip": lan,
            "process": {"pid": os.getpid(), "in_job_object": in_job},
            "service": {
                "state": state["state"],
                "note": state.get("note"),
                "ports": state["ports"],
            },
            "mcp": {
                "endpoint": f"http://{lan or '127.0.0.1'}:8011/mcp" if lan else "http://127.0.0.1:8011/mcp",
                "listening": ports["control_8011"],
            },
            "firewall": firewall,
            "media_roots": media,
            "problems": problems or ["没有发现明显问题"],
        }

    return commands.run_tool("tb_doctor", impl, caller=caller)


# ------------------------------------------------------------------ 参数树


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 信封")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbctl",
        description=f"{HEADER} {__version__} —— 驱动本机 TokBrain 全链路",
    )
    parser.add_argument("--base", default=None, help="后端地址，默认 http://127.0.0.1:8001")
    parser.add_argument("--json", action="store_true", help="全局 JSON 输出")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="group", required=True)

    # status / doctor / audit
    p = sub.add_parser("status", help="后端与知识库概览")
    p.add_argument("--include-jobs", type=int, default=5)
    p.add_argument("--include-sync", action="store_true")
    _add_common(p)

    p = sub.add_parser("doctor", help="环境自检")
    _add_common(p)

    p = sub.add_parser("audit", help="审计日志")
    p.add_argument("action", choices=["tail"])
    p.add_argument("--n", type=int, default=20)
    _add_common(p)

    # service
    p = sub.add_parser("service", help="服务生命周期")
    p.add_argument("action", choices=["status", "start", "stop", "restart"])
    p.add_argument("--wait", type=int, default=40)
    _add_common(p)

    # sync
    p = sub.add_parser("sync", help="飞书同步桥")
    p.add_argument(
        "action", choices=["status", "start", "stop", "replay", "wait", "pending", "confirm"]
    )
    p.add_argument("--download", action="store_true", help="start 时真的下载视频（默认只抓元数据）")
    p.add_argument(
        "--auto-confirm",
        action="store_true",
        help="start 时预检完直接入库，不停在待确认（默认停住等人放行）",
    )
    p.add_argument("--limit", type=int, default=5, help="replay 单次重投条数 / pending 列出条数")
    p.add_argument("--newest", action="store_true", help="replay 时优先重投最近失败的")
    p.add_argument("--timeout", type=int, default=80, help="wait 的最长秒数")
    p.add_argument("--records", default="", help="confirm 的 record_id，逗号分隔")
    p.add_argument("--collection", default=None, help="confirm 时挂到哪个合集（标题或 id）")
    _add_common(p)

    # import
    p = sub.add_parser("import", help="导入")
    imp = p.add_subparsers(dest="action", required=True)

    links = imp.add_parser("links", help="链接导入")
    links.add_argument("--text", default=None, help="链接文本（多行/空格分隔）")
    links.add_argument("--file", default=None, help="从文件读链接")
    links.add_argument("--metadata-only", action="store_true")
    links.add_argument("--title", default=None)
    links.add_argument("--author", default=None)
    _add_common(links)

    files = imp.add_parser("files", help="本地文件导入（≤10）")
    files.add_argument("--path", action="append", required=True, help="可重复")
    files.add_argument("--collection", type=int, default=None)
    files.add_argument("--no-confirm", action="store_true")
    files.add_argument("--no-ingest", action="store_true")
    _add_common(files)

    package = imp.add_parser("package", help="目录数据包导入")
    package.add_argument("--folder", required=True)
    package.add_argument("--collection", type=int, default=None)
    package.add_argument("--no-analyze", action="store_true")
    _add_common(package)

    # batch
    p = sub.add_parser("batch", help="导入批次")
    bat = p.add_subparsers(dest="action", required=True)

    get = bat.add_parser("get")
    get.add_argument("batch_id")
    get.add_argument("--wait", type=int, default=0, help="等待预检完成的秒数")
    get.add_argument("--no-items", action="store_true")
    _add_common(get)

    confirm = bat.add_parser("confirm")
    confirm.add_argument("batch_id")
    confirm.add_argument("--items", default=None, help="逗号分隔的 item_id")
    confirm.add_argument("--all-ready", action="store_true")
    confirm.add_argument("--collection", type=int, default=None)
    _add_common(confirm)

    cancel = bat.add_parser("cancel")
    cancel.add_argument("batch_id")
    _add_common(cancel)

    # ingest
    p = sub.add_parser("ingest", help="触发入库（confirm 之后必须调）")
    p.add_argument("--works", required=True, help="逗号分隔的 work_id")
    p.add_argument("--wait", type=int, default=0)
    _add_common(p)

    # job
    p = sub.add_parser("job", help="任务")
    job = p.add_subparsers(dest="action", required=True)
    jl = job.add_parser("list")
    jl.add_argument("--limit", type=int, default=10)
    jl.add_argument("--state", default=None)
    _add_common(jl)
    jg = job.add_parser("get")
    jg.add_argument("job_id")
    _add_common(jg)
    jw = job.add_parser("wait")
    jw.add_argument("job_id", nargs="?", default=None)
    jw.add_argument("--batch", default=None)
    jw.add_argument("--timeout", type=int, default=600)
    jw.add_argument("--interval", type=float, default=2.0)
    _add_common(jw)
    jc = job.add_parser("cancel")
    jc.add_argument("job_id")
    _add_common(jc)

    # work
    p = sub.add_parser("work", help="知识库作品")
    work = p.add_subparsers(dest="action", required=True)
    wl = work.add_parser("list")
    wl.add_argument("--state", default="in_library", choices=list(LIBRARY_STATES))
    wl.add_argument("--collection", type=int, default=None)
    wl.add_argument("--platform", default=None)
    wl.add_argument("--kind", default=None)
    wl.add_argument("--author", default=None)
    wl.add_argument("--offset", type=int, default=0)
    wl.add_argument("--limit", type=int, default=20)
    _add_common(wl)
    wg = work.add_parser("get")
    wg.add_argument("work_id", type=int)
    wg.add_argument("--transcript", action="store_true")
    wg.add_argument("--limit-segments", type=int, default=8)
    _add_common(wg)
    wr = work.add_parser("retry")
    wr.add_argument("--works", default=None)
    wr.add_argument("--error-code", default=None)
    wr.add_argument("--collection", type=int, default=None)
    _add_common(wr)
    wd = work.add_parser("delete")
    wd.add_argument("work_id", type=int)
    wd.add_argument("--confirm-title", required=True, help="必须与标题完全一致，防手滑")
    _add_common(wd)
    ws = work.add_parser("restore")
    ws.add_argument("work_id", type=int)
    _add_common(ws)
    wsup = work.add_parser("supplement")
    wsup.add_argument("work_id", type=int)
    wsup.add_argument("--path", action="append", required=True)
    _add_common(wsup)

    # collection
    p = sub.add_parser("collection", help="合集")
    col = p.add_subparsers(dest="action", required=True)
    cl = col.add_parser("list")
    _add_common(cl)
    ca = col.add_parser("assign")
    ca.add_argument("--title", required=True)
    ca.add_argument("--works", required=True)
    _add_common(ca)

    # search
    p = sub.add_parser("search", help="检索证据（不生成答案）")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--collection", default=None)
    _add_common(p)

    # platform
    p = sub.add_parser("platform", help="平台采集")
    plat = p.add_subparsers(dest="action", required=True)
    pp = plat.add_parser("pull")
    pp.add_argument("platform", choices=["douyin", "bilibili", "xiaohongshu"])
    pp.add_argument("--mode", default="like", choices=["like", "collect", "post", "favorite", "history"])
    pp.add_argument("--limit", type=int, default=50)
    pp.add_argument("--no-enqueue", action="store_true")
    pp.add_argument("--profile-url", default=None)
    _add_common(pp)

    # fs
    p = sub.add_parser("fs", help="文件系统探针与媒体路径约定")
    fsp = p.add_subparsers(dest="action", required=True)
    roots = fsp.add_parser("roots", help="查看 NAS ⇄ Windows 路径约定表")
    _add_common(roots)
    probe = fsp.add_parser("probe")
    probe.add_argument("path", nargs="?", default="", help="留空等价于 fs roots")
    probe.add_argument("--list", action="store_true")
    _add_common(probe)

    return parser


# ------------------------------------------------------------------ 分发


def _ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()]


def _exit_for(envelope: dict[str, Any]) -> int:
    if envelope.get("ok"):
        return EXIT_OK
    error = envelope.get("error") or {}
    kind = error.get("kind")
    if kind == "usage":
        return EXIT_USAGE
    if kind == "offline":
        return EXIT_OFFLINE
    return EXIT_FAIL


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    group = args.group
    action = getattr(args, "action", None)

    if group == "status":
        return commands.status(
            include_jobs=args.include_jobs, include_sync=args.include_sync
        )
    if group == "doctor":
        return doctor()
    if group == "audit":
        return commands.audit_tail(args.n)
    if group == "service":
        if action == "status":
            return commands.service_status()
        if action == "start":
            return commands.service_start(args.wait)
        if action == "stop":
            return commands.service_stop(args.wait)
        return commands.service_restart(args.wait)

    if group == "sync":
        if action == "status":
            return commands.sync_status()
        if action == "start":
            return commands.sync_start(
                download=args.download, auto_confirm=args.auto_confirm
            )
        if action == "stop":
            return commands.sync_stop()
        if action == "wait":
            return commands.sync_wait(timeout=args.timeout)
        if action == "pending":
            return commands.sync_pending(limit=max(args.limit, 20))
        if action == "confirm":
            return commands.sync_confirm(args.records, collection=args.collection)
        return commands.sync_replay(limit=args.limit, newest_first=args.newest)

    if group == "import":
        if action == "links":
            text = args.text or ""
            if args.file:
                text = Path(args.file).expanduser().read_text(encoding="utf-8")
            if not text.strip():
                raise SystemExit("import links 需要 --text 或 --file")
            return commands.import_links(
                text,
                metadata_only=args.metadata_only,
                title=args.title,
                author=args.author,
            )
        if action == "files":
            return commands.import_files(
                args.path,
                collection_id=args.collection,
                auto_confirm=not args.no_confirm,
                auto_ingest=not args.no_ingest,
            )
        return commands.import_package(
            args.folder,
            collection_id=args.collection,
            auto_analyze=not args.no_analyze,
        )

    if group == "batch":
        if action == "get":
            envelope = commands.batch_get(args.batch_id, include_items=not args.no_items)
            if args.wait and envelope.get("ok"):
                job_id = (envelope.get("data") or {}).get("job_id")
                if job_id:
                    waiting = commands.job_wait(job_id=job_id, wait_seconds=min(args.wait, 90))
                    envelope["data"]["wait"] = waiting.get("data") or waiting.get("error")
            return envelope
        if action == "confirm":
            return commands.batch_confirm(
                args.batch_id,
                item_ids=_ids(args.items),
                all_ready=args.all_ready,
                collection_id=args.collection,
            )
        return commands.batch_cancel(args.batch_id)

    if group == "ingest":
        envelope = commands.library_ingest(_ids(args.works))
        if args.wait and envelope.get("ok"):
            job_id = (envelope.get("data") or {}).get("id")
            if job_id:
                envelope["data"]["wait"] = commands.job_wait(
                    job_id=job_id, wait_seconds=min(args.wait, 90)
                ).get("data")
        return envelope

    if group == "job":
        if action == "list":
            return commands.job_list(args.limit, args.state)
        if action == "get":
            return commands.job_get(args.job_id)
        if action == "wait":
            return commands.job_wait(
                job_id=args.job_id,
                batch_id=args.batch,
                wait_seconds=min(args.timeout, 90),
                interval=args.interval,
            )
        return commands.job_cancel(args.job_id)

    if group == "work":
        if action == "list":
            return commands.work_list(
                library_state=args.state,
                collection_id=args.collection,
                platform=args.platform,
                kind=args.kind,
                author=args.author,
                offset=args.offset,
                limit=args.limit,
            )
        if action == "get":
            return commands.work_get(
                args.work_id,
                with_transcript=args.transcript,
                transcript_limit=args.limit_segments,
            )
        if action == "retry":
            return commands.work_retry(
                work_ids=_ids(args.works),
                error_code=args.error_code,
                collection_id=args.collection,
            )
        if action == "delete":
            # CLI 额外要求标题比对：手滑 delete 5 的代价太高了
            current = commands.work_get(args.work_id)
            title = ((current.get("data") or {}).get("title") or "") if current.get("ok") else ""
            if title.strip() != args.confirm_title.strip():
                from .errors import ControlError as _CE

                raise _CE(
                    "title_mismatch",
                    f"标题不匹配：作品实际标题是「{title}」",
                    kind="usage",
                    hint="把 --confirm-title 写成完全一致的标题再试。",
                )
            return commands.work_delete(args.work_id, confirm=True)
        if action == "restore":
            return commands.work_restore(args.work_id)
        return commands.work_supplement(args.work_id, args.path)

    if group == "collection":
        if action == "list":
            return commands.collection_list()
        return commands.collection_assign(args.title, _ids(args.works))

    if group == "search":
        return commands.search(args.query, args.top_k, args.collection)

    if group == "platform":
        return commands.platform_pull(
            args.platform,
            mode=args.mode,
            limit=args.limit,
            enqueue=not args.no_enqueue,
            profile_url=args.profile_url,
        )

    if group == "fs":
        if action == "roots":
            return commands.fs_roots()
        return commands.fs_probe(args.path, list_dir=args.list)

    raise SystemExit(f"未知命令：{group} {action}")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.base:
        os.environ["TOKBRAIN_CONTROL_API_BASE"] = args.base
    as_json = bool(getattr(args, "json", False))

    try:
        envelope = _dispatch(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001
        from .errors import ControlError

        if isinstance(exc, ControlError):
            _emit(
                {
                    "ok": False,
                    "tool": f"{args.group}.{getattr(args, 'action', '')}".strip("."),
                    "audit_id": None,
                    "elapsed_ms": 0,
                    "data": None,
                    "error": exc.to_dict(),
                },
                as_json=as_json,
            )
            return exc.exit_code
        print(f"未预期错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL

    _emit(envelope, as_json=as_json)
    return _exit_for(envelope)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
