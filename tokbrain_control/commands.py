"""工具实现层：CLI 与 MCP 共用。

约定：

* 每个公开函数对应一个 ``tb_*`` 工具，返回统一信封
  ``{ok, tool, audit_id, elapsed_ms, data, error}``；
* 信封形状是 CLI ``--json`` 与 MCP 返回体的**同一份**，不存在两套；
* 只读工具不进审计（只 1% 采样），写工具全记，destructive 另标 ``severity=high``。
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from . import audit, bridge, service
from . import paths as media_paths
from .client import Client
from .errors import (
    CODE_CONFIRM_REQUIRED,
    CODE_DESTRUCTIVE_DISABLED,
    CODE_INVALID_ARGUMENT,
    CODE_TOO_MANY_FILES,
    ControlError,
)

MAX_JOB_WAIT_SECONDS = 90
DEFAULT_JOB_WAIT_SECONDS = 45
MAX_PATHS_PER_BATCH = 10
MAX_LINKS_PER_BATCH = 10
MAX_PACKAGE_FILES = 200
MAX_WORK_LIST_LIMIT = 60
MAX_ITEMS_IN_VIEW = 40


# ------------------------------------------------------------------ 基础设施


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def destructive_allowed() -> bool:
    return _flag("TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE", True)


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _error_envelope(
    tool: str, exc: ControlError, elapsed_ms: int, audit_id: str | None
) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "audit_id": audit_id,
        "elapsed_ms": elapsed_ms,
        "data": None,
        "error": exc.to_dict(),
    }


def run_tool(
    tool: str,
    fn: Callable[[], Any],
    *,
    args: dict[str, Any] | None = None,
    write: bool = False,
    destructive: bool = False,
    note: str | None = None,
    caller: str | None = None,
) -> dict[str, Any]:
    """执行一个工具并包成统一信封。"""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if destructive and not destructive_allowed():
        exc = ControlError(
            CODE_DESTRUCTIVE_DISABLED,
            "破坏性操作已被 TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE=0 关闭",
            kind="business",
            hint="需要时改成 1 并重启控制 MCP。",
        )
        audit_id = audit.record(
            tool=tool, ok=False, error_code=exc.code, args=args,
            severity="high", caller=caller, note=note,
        )
        return _error_envelope(tool, exc, elapsed(), audit_id)

    try:
        data = fn()
    except ControlError as exc:
        audit_id = None
        if write or destructive:
            audit_id = audit.record(
                tool=tool,
                ok=False,
                error_code=exc.code,
                duration_ms=elapsed(),
                args=args,
                note=note,
                severity="high" if destructive else "normal",
                caller=caller,
            )
        return _error_envelope(tool, exc, elapsed(), audit_id)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免把栈打给模型
        wrapped = ControlError(
            "internal_error",
            f"{type(exc).__name__}: {exc}",
            kind="local",
            hint="这是控制面自身的异常，不是 TokBrain 的业务错误；可重试一次，仍失败请查看日志。",
        )
        audit_id = None
        if write or destructive:
            audit_id = audit.record(
                tool=tool, ok=False, error_code=wrapped.code, duration_ms=elapsed(),
                args=args, severity="high" if destructive else "normal", caller=caller,
            )
        return _error_envelope(tool, wrapped, elapsed(), audit_id)

    audit_id: str | None = None
    if write or destructive:
        audit_id = audit.record(
            tool=tool,
            ok=True,
            duration_ms=elapsed(),
            args=args,
            note=note,
            severity="high" if destructive else "normal",
            caller=caller,
        )
    elif audit_id is None and hash(tool) % 100 == 0:
        # 只读工具 1% 采样，避免把日志淹没
        audit.record(tool=tool, ok=True, duration_ms=elapsed(), args=args, caller=caller)

    return {
        "ok": True,
        "tool": tool,
        "audit_id": audit_id,
        "elapsed_ms": elapsed(),
        "data": data,
        "error": None,
    }


def _client() -> Client:
    return Client()


# ------------------------------------------------------------------ 只读工具


def status(include_jobs: int = 5, include_sync: bool = False, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        client = _client()
        health = client.health()
        coordinators = client.coordinator_probe()
        collections = client.list_collections()

        jobs: list[dict[str, Any]] = []
        limit = _clamp(include_jobs, 0, 50, 5)
        if limit:
            jobs = client.list_jobs()[:limit]

        summary = collections.get("summary") or {}
        data: dict[str, Any] = {
            "backend": {
                "alive": bool(health),
                "api_contract": health.get("api_contract"),
                "status": health.get("status"),
                "scope": health.get("scope"),
            },
            "coordinators": {
                "status": coordinators.get("status"),
                "message": coordinators.get("message"),
                "details": coordinators.get("details"),
            },
            "library": {
                "works_total": summary.get("known_distinct_count"),
                "in_library": summary.get("local_item_count"),
                "pending": summary.get("candidate_count"),
                "issues": summary.get("issue_count"),
                "collections": len(collections.get("items") or []),
            },
            "jobs": jobs,
            "runtime": service.read_runtime(),
        }
        if include_sync:
            try:
                data["sync"] = client.sync_overview()
            except ControlError as exc:
                data["sync"] = {"error": exc.to_dict()}
        return data

    return run_tool(
        "tb_status",
        impl,
        args={"include_jobs": include_jobs, "include_sync": include_sync},
        caller=caller,
    )


def service_status(*, caller: str | None = None):
    return run_tool(
        "tb_service_status", service.probe_state, caller=caller
    )


# ------------------------------------------------------------------ 飞书同步桥


def sync_status(*, caller: str | None = None):
    """bridge 进程 + 队列 + 飞书连接，三者一次性给全。

    单独看任何一个都不够：进程活着但队列全是 failed 等于没干活；飞书连着但
    bridge 没跑等于新行没人管。
    """

    def impl() -> dict[str, Any]:
        data = bridge.status()
        client = _client()
        try:
            overview = client.sync_overview()
        except ControlError as exc:
            data["feishu"] = {"available": False, "error": exc.to_dict()}
        else:
            data["feishu"] = {
                "available": True,
                "connected": (overview.get("feishu") or {}).get("connected"),
                "rows": (overview.get("feishu") or {}).get("rows"),
                "last_query": (overview.get("feishu") or {}).get("last_query"),
                "expires_at": (overview.get("feishu") or {}).get("expires_at"),
                "obsidian": overview.get("obsidian"),
            }
            if not data["feishu"]["connected"]:
                data["hints"].append(
                    "飞书连接不可用：可能是 lark-cli 授权过期，需要在网页上点「一键续期」扫码。"
                )

        # 两个凭证互相独立，别混：
        #   f2_cookie      → 链接解析（把抖音分享链接变成作品+媒体地址），本项目里是「可选」
        #   douyin_cookie  → 平台采集（拉自己的收藏/点赞）
        try:
            settings = client.get_settings()
        except ControlError as exc:
            data["credentials"] = {"available": False, "error": exc.to_dict()}
        else:
            f2_ok = bool(settings.get("has_f2_cookie"))
            data["credentials"] = {
                "available": True,
                "f2_cookie": f2_ok,
                "douyin_cookie": bool(settings.get("has_douyin_cookie")),
                "note": (
                    "f2_cookie 只影响「飞书表里的抖音链接」能不能解析；"
                    "导入本地视频文件不需要它。"
                ),
            }
            if not f2_ok:
                data["hints"].append(
                    "f2_cookie 不可用：飞书表里的抖音**链接**会预检失败"
                    "（f2_cookie_unreadable / f2_cookie_required）。"
                    "去网页设置重新保存 F2 Cookie 即可。**本地视频文件导入不受影响。**"
                )

        try:
            douyin = client.platform_status("douyin")
        except ControlError as exc:
            data["platforms"] = {"available": False, "error": exc.to_dict()}
        else:
            connected = bool(douyin.get("connected"))
            data["platforms"] = {"available": True, "douyin_connected": connected}
            if not connected:
                data["hints"].append(
                    "抖音平台采集未登录：只影响「拉我的收藏/点赞」，"
                    "不影响飞书链接解析。"
                )
        return data

    return run_tool("tb_sync_status", impl, args={}, caller=caller)


def sync_start(
    download: bool = False, auto_confirm: bool = False, *, caller: str | None = None
):
    def impl() -> dict[str, Any]:
        return bridge.start(download=download, auto_confirm=auto_confirm)

    return run_tool(
        "tb_sync_start",
        impl,
        args={"download": download, "auto_confirm": auto_confirm},
        write=True,
        caller=caller,
    )


def sync_stop(*, caller: str | None = None):
    def impl() -> dict[str, Any]:
        return bridge.stop()

    return run_tool("tb_sync_stop", impl, args={}, write=True, caller=caller)


def sync_replay(
    limit: int = 5,
    newest_first: bool = False,
    record_ids: list[str] | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        return bridge.replay(
            limit,
            newest_first=newest_first,
            record_ids=record_ids,
            caller=caller,
        )

    return run_tool(
        "tb_sync_replay",
        impl,
        args={
            "limit": limit,
            "newest_first": newest_first,
            "record_ids": record_ids,
        },
        write=True,
        caller=caller,
    )


def sync_wait(timeout: int = 80, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        seconds = _clamp(timeout, 5, MAX_JOB_WAIT_SECONDS, 80)
        return bridge.wait_idle(timeout=float(seconds))

    return run_tool(
        "tb_sync_wait",
        impl,
        args={"timeout": timeout},
        caller=caller,
    )


def sync_pending(limit: int = 20, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        return bridge.pending_confirm(_clamp(limit, 1, 100, 20))

    return run_tool(
        "tb_sync_pending",
        impl,
        args={"limit": limit},
        caller=caller,
    )


def sync_confirm(
    record_ids: list[str] | str, collection: str | int | None = None, *, caller: str | None = None
):
    def impl() -> dict[str, Any]:
        ids = (
            [part.strip() for part in record_ids.split(",")]
            if isinstance(record_ids, str)
            else list(record_ids or [])
        )
        ids = [i for i in ids if i]
        if not ids:
            raise ControlError(CODE_INVALID_ARGUMENT, "record_ids 不能为空", kind="usage")
        return bridge.confirm_pending(ids, collection=collection)

    return run_tool(
        "tb_sync_confirm",
        impl,
        args={"record_ids": record_ids, "collection": collection},
        write=True,
        caller=caller,
    )


def job_list(limit: int = 10, state: str | None = None, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        jobs = _client().list_jobs()
        if state:
            jobs = [j for j in jobs if str(j.get("state")) == state]
        limit_ = _clamp(limit, 1, 50, 10)
        return {"count": len(jobs), "jobs": jobs[:limit_]}

    return run_tool("tb_job_list", impl, args={"limit": limit, "state": state}, caller=caller)


def job_get(job_id: str, *, caller: str | None = None):
    return run_tool(
        "tb_job_get", lambda: _client().get_job(job_id), args={"job_id": job_id}, caller=caller
    )


def _wait_payload(job: dict[str, Any], done: bool, elapsed: float, job_id: str) -> dict[str, Any]:
    state = str(job.get("state") or "")
    progress = job.get("progress") or {}
    payload: dict[str, Any] = {
        "job_id": job_id,
        "done": done,
        "state": state,
        "message": job.get("message"),
        "elapsed": round(elapsed, 1),
        "progress": {
            "items_total": job.get("total_items"),
            "items_completed": job.get("processed_items"),
            "items_failed": job.get("failed_items"),
            "items_deferred": job.get("deferred_items"),
            "phase": progress.get("phase"),
            "queue_position": progress.get("queue_position"),
            "current_work": progress.get("current_work"),
        },
    }
    queue_position = progress.get("queue_position")
    if done:
        payload["summary"] = {
            "processed": job.get("processed_items"),
            "failed": job.get("failed_items"),
            "cancelled": job.get("cancelled_items"),
            "deferred": job.get("deferred_items"),
        }
        if state == "succeeded":
            payload["next_step"] = "完成。可以 tb_search 检索，或 tb_work_get 看摘要。"
        elif state == "partial":
            payload["next_step"] = "部分成功。tb_work_list(library_state=issues) 看失败项，再 tb_work_retry。"
        elif state == "failed":
            payload["next_step"] = "失败。先看 message 与 tb_work_list(library_state=issues) 的 error_code，再决定重试还是补件。"
        else:
            payload["next_step"] = "已取消。"
        return payload

    remaining = (job.get("total_items") or 0) - (job.get("processed_items") or 0)
    if state == "cancelling":
        payload["note"] = "正在安全停止：当前作品处理完后才会停。"
    elif state == "queued" and queue_position:
        payload["note"] = (
            f"在队列第 {queue_position} 位。TokBrain 只有 1 个 worker 串行处理，"
            "排队是正常的，不是卡死。"
        )
    payload["continue_with"] = {
        "tool": "tb_job_wait",
        "args": {"job_id": job_id, "wait_seconds": DEFAULT_JOB_WAIT_SECONDS},
    }
    payload["still_working_hint"] = (
        f"还有约 {max(0, remaining)} 个作品未完成。**继续调用 tb_job_wait**，"
        "不要重新提交入库，也不要换方案。"
    )
    return payload


def job_wait(
    job_id: str | None = None,
    batch_id: str | None = None,
    wait_seconds: int = DEFAULT_JOB_WAIT_SECONDS,
    interval: float = 2.0,
    *,
    caller: str | None = None,
):
    seconds = _clamp(wait_seconds, 1, MAX_JOB_WAIT_SECONDS, DEFAULT_JOB_WAIT_SECONDS)

    def impl() -> dict[str, Any]:
        client = _client()
        resolved = client.resolve_job_id(job_id=job_id, batch_id=batch_id)
        started = time.monotonic()
        job, done = client.wait_job(resolved, timeout=seconds, interval=interval)
        return _wait_payload(job, done, time.monotonic() - started, resolved)

    return run_tool(
        "tb_job_wait",
        impl,
        args={"job_id": job_id, "batch_id": batch_id, "wait_seconds": seconds},
        caller=caller,
    )


def batch_get(batch_id: str, include_items: bool = True, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        batch = _client().get_batch(batch_id)
        items = batch.get("items") or []
        if include_items:
            batch = {**batch, "items": items[:MAX_ITEMS_IN_VIEW]}
            batch["items_truncated"] = max(0, len(items) - MAX_ITEMS_IN_VIEW)
        else:
            batch = {k: v for k, v in batch.items() if k != "items"}
            batch["items_count"] = len(items)
        return batch

    return run_tool(
        "tb_batch_get", impl, args={"batch_id": batch_id}, caller=caller
    )


def work_list(
    library_state: str = "in_library",
    collection_id: int | None = None,
    platform: str | None = None,
    kind: str | None = None,
    author: str | None = None,
    source: str | None = None,
    offset: int = 0,
    limit: int = 20,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        payload = _client().list_works(
            library_state=library_state,
            collection_id=collection_id,
            platform=platform,
            kind=kind,
            author=author,
            source=source,
            offset=max(0, int(offset or 0)),
            limit=_clamp(limit, 1, MAX_WORK_LIST_LIMIT, 20),
        )
        items = payload.get("items") or []
        return {
            "total": payload.get("total"),
            "state": library_state,
            "count": len(items),
            "next_offset": payload.get("next_offset"),
            "items": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "platform": item.get("platform"),
                    "library_state": item.get("library_state"),
                    "processing_state": item.get("processing_state"),
                    "supplement_state": item.get("supplement_state"),
                    "evidence_state": item.get("evidence_state"),
                    "error_code": item.get("error_code"),
                    "summary_state": item.get("summary_state"),
                    "duration_seconds": item.get("duration_seconds"),
                    "source_url": item.get("source_url"),
                    "collections": item.get("collections"),
                }
                for item in items
            ],
        }

    return run_tool(
        "tb_work_list",
        impl,
        args={"library_state": library_state, "collection_id": collection_id, "limit": limit},
        caller=caller,
    )


def work_get(
    work_id: int,
    with_transcript: bool = False,
    transcript_limit: int = 8,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        payload = _client().get_work_summary(int(work_id))
        if with_transcript:
            from . import search as search_mod

            payload["transcript"] = search_mod.transcript(
                int(work_id), limit=_clamp(transcript_limit, 1, 40, 8)
            )
        return payload

    return run_tool(
        "tb_work_get",
        impl,
        args={"work_id": work_id, "with_transcript": with_transcript},
        caller=caller,
    )


def collection_list(*, caller: str | None = None):
    def impl() -> dict[str, Any]:
        payload = _client().list_collections()
        return {
            "summary": payload.get("summary"),
            "collections": payload.get("items") or [],
        }

    return run_tool("tb_collection_list", impl, caller=caller)


def search(
    query: str,
    top_k: int = 5,
    collection: str | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        from . import search as search_mod

        return search_mod.search(query, _clamp(top_k, 1, 20, 5), collection)

    return run_tool(
        "tb_search",
        impl,
        args={"query": query, "top_k": top_k, "collection": collection},
        caller=caller,
    )


def fs_roots(*, caller: str | None = None):
    """返回「NAS 侧路径 → 本机 Windows 路径」的约定表。

    与 fs_probe 分开是因为它是**稳定信息**：调用方第一次接触本地文件时先看一眼，
    之后就知道该把文件放到哪、该用什么路径传参。
    """

    def impl() -> dict[str, Any]:
        return media_paths.describe()

    return run_tool("tb_fs_roots", impl, args={}, caller=caller)


def fs_probe(path: str, list_dir: bool = False, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        raw = str(path or "").strip()
        if not raw:
            return media_paths.describe()
        info = Client.probe_fs(raw)
        if list_dir and info.get("is_dir"):
            target = Path(info["path"])
            try:
                children = sorted(target.iterdir())[:20]
            except OSError as exc:
                info["list_error"] = repr(exc)
            else:
                info["children"] = [
                    {
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": None if child.is_dir() else child.stat().st_size,
                    }
                    for child in children
                ]
        if not info.get("exists"):
            info["media_roots"] = [
                {
                    "windows_path": entry["windows_path"],
                    "nas_prefix": entry.get("nas_prefix"),
                    "is_dir": entry.get("is_dir"),
                }
                for entry in media_paths.describe(include_samples=False)["roots"]
            ]
        return info

    return run_tool("tb_fs_probe", impl, args={"path": path}, caller=caller)


# ------------------------------------------------------------------ 导入写链路


def _client_item_id(index: int) -> str:
    return f"tb{index}-{uuid.uuid4().hex[:10]}"


def import_links(
    text: str,
    metadata_only: bool = False,
    title: str | None = None,
    author: str | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        links = [line.strip() for line in (text or "").split() if line.strip()]
        if not links:
            raise ControlError(CODE_INVALID_ARGUMENT, "没有解析到任何链接", kind="usage")
        # 节流：一次塞太多链接，后端预检会连续打平台接口，历史上正是这样被打成 429
        if len(links) > MAX_LINKS_PER_BATCH:
            raise ControlError(
                "too_many_links",
                f"单次最多 {MAX_LINKS_PER_BATCH} 条链接，收到 {len(links)} 条",
                kind="usage",
                hint="分批提交，每批之间用 tb_batch_get 看预检结果再继续。",
            )
        result = _client().create_link_batch(
            text, metadata_only=metadata_only, title=title, author=author
        )
        return {
            **result,
            "next_step": "用 tb_batch_get 等预检完成（items.status=ready），"
            "再 tb_batch_confirm，然后 tb_library_ingest。",
        }

    return run_tool(
        "tb_import_links",
        impl,
        args={"text": text, "metadata_only": metadata_only, "title": title},
        write=True,
        caller=caller,
    )


def import_files(
    paths: Sequence[str],
    collection_id: int | None = None,
    auto_confirm: bool = True,
    auto_ingest: bool = True,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        cleaned = [str(p).strip() for p in (paths or []) if str(p).strip()]
        if not cleaned:
            raise ControlError(CODE_INVALID_ARGUMENT, "至少要给一个文件路径", kind="usage")
        if len(cleaned) > MAX_PATHS_PER_BATCH:
            raise ControlError(
                CODE_TOO_MANY_FILES,
                f"单批最多 {MAX_PATHS_PER_BATCH} 个文件，收到 {len(cleaned)} 个",
                kind="usage",
                hint="分成多批调用，或改用 tb_import_package。",
            )

        client = _client()
        prepared: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for index, raw in enumerate(cleaned):
            info = client.probe_fs(raw)
            if not info.get("exists") or info.get("is_dir"):
                skipped.append(
                    {"path": raw, "reason": info.get("reason") or "不是普通文件", "info": info}
                )
                continue
            prepared.append(
                {
                    "client_item_id": _client_item_id(index),
                    "filename": Path(info["path"]).name,
                    "size_bytes": info["size"],
                    "source_path": info["path"],
                    "target_collection_id": collection_id,
                }
            )

        if not prepared:
            raise ControlError(
                "path_not_visible",
                "所有路径在本机都不可读",
                kind="local",
                hint=(
                    "先 tb_fs_roots 看约定目录是否在线，再用 tb_fs_probe 逐个确认。"
                    if media_paths.specs()
                    else "先映射 NAS 盘符并在 control.env 里配好媒体路径约定，"
                    "或改用 tb_import_links 走链接导入。"
                ),
                detail={"skipped": skipped},
            )

        batch = client.create_local_batch(
            [
                {
                    "client_item_id": item["client_item_id"],
                    "filename": item["filename"],
                    "size_bytes": item["size_bytes"],
                    "target_collection_id": collection_id,
                }
                for item in prepared
            ]
        )
        batch_id = batch.get("id") or batch.get("batch_id")
        item_id_by_cid = {
            str(entry.get("client_item_id")): entry.get("id")
            for entry in (batch.get("items") or [])
        }

        uploaded: list[dict[str, Any]] = []
        for item in prepared:
            item_id = item_id_by_cid.get(item["client_item_id"])
            if not item_id:
                skipped.append(
                    {"path": item["source_path"], "reason": "批次里没有对应的 item_id"}
                )
                continue
            result = client.upload_local_video(batch_id, int(item_id), item["source_path"])
            uploaded.append(
                {
                    "item_id": item_id,
                    "filename": item["filename"],
                    "status": result.get("status"),
                    "kind": result.get("kind"),
                    "duration_seconds": result.get("duration_seconds"),
                    "sha256": result.get("sha256"),
                    "existing_work_id": result.get("existing_work_id"),
                }
            )

        ready_ids = [int(entry["item_id"]) for entry in uploaded if entry.get("item_id")]
        data: dict[str, Any] = {
            "batch_id": batch_id,
            "job_id": batch.get("job_id"),
            "uploaded": uploaded,
            "skipped": skipped,
        }

        if auto_confirm and ready_ids:
            confirmed = client.confirm_batch(batch_id, item_ids=ready_ids)
            data["confirm"] = confirmed
            work_ids = [
                int(value)
                for value in (confirmed.get("work_ids") or [])
                if str(value).isdigit()
            ]
            data["work_ids"] = work_ids
            if auto_ingest and work_ids:
                data["ingest"] = client.ingest(work_ids)
            data["next_step"] = (
                "已确认并提交入库。用 tb_job_wait 跟踪进度。"
                if data.get("ingest")
                else "已确认但未入库；需要时调用 tb_library_ingest。"
            )
        else:
            data["next_step"] = "文件已上传但未确认。用 tb_batch_get 看状态后再 tb_batch_confirm。"
        return data

    return run_tool(
        "tb_import_files",
        impl,
        args={"paths": list(paths or []), "collection_id": collection_id},
        write=True,
        caller=caller,
    )


def import_package(
    folder_path: str,
    collection_id: int | None = None,
    auto_analyze: bool = True,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        info = Client.probe_fs(folder_path)
        if not info.get("exists"):
            raise ControlError(
                "path_not_visible",
                f"本机看不到这个目录：{folder_path}",
                kind="local",
                hint=(
                    "先 tb_fs_roots 看约定目录是否在线；映射盘掉线时重新双击"
                    "mcp_server/挂载NAS媒体盘.cmd。"
                    if media_paths.specs()
                    else "NAS 目录需要先映射盘符并配置媒体路径约定"
                    "（mcp_server/挂载NAS媒体盘.cmd）。"
                ),
                detail=info,
            )
        if not info.get("is_dir"):
            raise ControlError(
                "invalid_argument",
                f"不是目录：{folder_path}",
                kind="usage",
                hint="单文件导入用 tb_import_files。",
            )

        root = Path(info["path"])
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        if not files:
            raise ControlError(CODE_INVALID_ARGUMENT, "目录里没有文件", kind="usage")
        if len(files) > MAX_PACKAGE_FILES:
            raise ControlError(
                CODE_TOO_MANY_FILES,
                f"目录里有 {len(files)} 个文件，超过控制面上限 {MAX_PACKAGE_FILES}",
                kind="usage",
                hint="先挑出需要的文件放到子目录再导入，避免整盘上传。",
            )

        entries = []
        for index, path in enumerate(files):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append(
                {
                    "client_file_id": _client_item_id(index),
                    "relative_path": str(path.relative_to(root)).replace("\\", "/"),
                    "size_bytes": size,
                    "_path": path,
                }
            )
        if not entries:
            raise ControlError(CODE_INVALID_ARGUMENT, "没有可读文件", kind="usage")

        client = _client()
        batch = client.create_package_batch(
            [
                {
                    "client_file_id": entry["client_file_id"],
                    "relative_path": entry["relative_path"],
                    "size_bytes": entry["size_bytes"],
                }
                for entry in entries
            ],
            upload_mode="folder",
            target_collection_id=collection_id,
        )
        batch_id = batch.get("id") or batch.get("batch_id")
        file_id_by_cid = {
            str(entry.get("client_file_id")): entry.get("id")
            for entry in (batch.get("files") or [])
        }

        uploaded = 0
        for entry in entries:
            file_id = file_id_by_cid.get(entry["client_file_id"])
            if not file_id:
                continue
            client.upload_package_file(batch_id, str(file_id), entry["_path"])
            uploaded += 1

        data: dict[str, Any] = {
            "batch_id": batch_id,
            "files_total": len(entries),
            "files_uploaded": uploaded,
        }
        if auto_analyze:
            data["analyze"] = client.analyze_package(batch_id)
        data["note"] = "数据包导入会走 package_analyze 任务；该接口强制同源 Origin，属预期行为。"
        data["next_step"] = "用 tb_batch_get 看识别结果，再确认入库。"
        return data

    return run_tool(
        "tb_import_package",
        impl,
        args={"folder_path": folder_path, "collection_id": collection_id},
        write=True,
        note="origin_spoof_for_package",
        caller=caller,
    )


def batch_confirm(
    batch_id: str,
    item_ids: Sequence[int] | None = None,
    items: Sequence[dict[str, Any]] | None = None,
    all_ready: bool = False,
    collection_id: int | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        ids = [int(v) for v in (item_ids or []) if str(v).isdigit()]
        selections = [
            {
                "item_id": int(entry["item_id"]),
                "collection_id": int(entry.get("collection_id") or collection_id or 0),
            }
            for entry in (items or [])
            if str(entry.get("item_id", "")).isdigit()
            and (entry.get("collection_id") or collection_id)
        ]
        if all_ready or not ids and not selections:
            batch = _client().get_batch(batch_id)
            ready = [
                int(entry["id"])
                for entry in (batch.get("items") or [])
                if entry.get("status") == "ready"
            ]
            ids = sorted(set(ids) | set(ready))
        if not ids and not selections:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                "没有可确认的条目（没有 ready 状态的 item）",
                kind="business",
                hint="先 tb_batch_get 看 items 状态；需要补件的条目不在这里确认。",
            )
        result = _client().confirm_batch(batch_id, item_ids=ids, items=selections)
        result["next_step"] = "确认只建出待入库作品，**不会**触发解析；要继续就调 tb_library_ingest。"
        return result

    return run_tool(
        "tb_batch_confirm",
        impl,
        args={"batch_id": batch_id, "item_ids": list(item_ids or []), "all_ready": all_ready},
        write=True,
        caller=caller,
    )


def batch_cancel(batch_id: str, *, caller: str | None = None):
    return run_tool(
        "tb_batch_cancel",
        lambda: _client().cancel_batch(batch_id),
        args={"batch_id": batch_id},
        write=True,
        caller=caller,
    )


def library_ingest(work_ids: Sequence[int], *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        ids = sorted({int(v) for v in (work_ids or []) if str(v).isdigit()})
        if not ids:
            raise ControlError(CODE_INVALID_ARGUMENT, "work_ids 不能为空", kind="usage")
        job = _client().ingest(ids)
        job["next_step"] = "用 tb_job_wait(job_id=...) 跟踪；单 worker 串行，可能需要多次等待。"
        return job

    return run_tool(
        "tb_library_ingest",
        impl,
        args={"work_ids": list(work_ids or [])},
        write=True,
        caller=caller,
    )


def job_cancel(job_id: str, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        job = _client().cancel_job(job_id)
        job["note"] = "取消是安全边界式的：当前作品处理完才会停，state 会先变成 cancelling。"
        return job

    return run_tool(
        "tb_job_cancel", impl, args={"job_id": job_id}, write=True, caller=caller
    )


# ------------------------------------------------------------------ 库与合集


def work_retry(
    work_ids: Sequence[int] | None = None,
    error_code: str | None = None,
    collection_id: int | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        ids = [int(v) for v in (work_ids or []) if str(v).isdigit()]
        if not ids and not error_code and collection_id is None:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                "至少要指定 work_ids、error_code 或 collection_id 之一",
                kind="usage",
                hint="只重试异常状态下（library_state=issues）的作品。",
            )
        return _client().retry_works(
            work_ids=ids, error_code=error_code, collection_id=collection_id
        )

    return run_tool(
        "tb_work_retry",
        impl,
        args={"work_ids": list(work_ids or []), "error_code": error_code},
        write=True,
        caller=caller,
    )


def work_delete(work_id: int, confirm: bool = False, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        if confirm is not True:
            raise ControlError(
                CODE_CONFIRM_REQUIRED,
                "永久删除需要显式传 confirm=true",
                kind="usage",
                hint="这一步不可恢复：会删掉作品、知识片段与本地媒体文件。",
            )
        return _client().delete_work(int(work_id))

    return run_tool(
        "tb_work_delete",
        impl,
        args={"work_id": work_id, "confirm": confirm},
        write=True,
        destructive=True,
        caller=caller,
    )


def work_restore(work_id: int, *, caller: str | None = None):
    return run_tool(
        "tb_work_restore",
        lambda: _client().restore_work(int(work_id)),
        args={"work_id": work_id},
        write=True,
        caller=caller,
    )


def work_supplement(
    work_id: int, paths: Sequence[str], *, caller: str | None = None
):
    def impl() -> dict[str, Any]:
        cleaned = [str(p) for p in (paths or []) if str(p).strip()]
        if not cleaned:
            raise ControlError(CODE_INVALID_ARGUMENT, "至少要给一个文件路径", kind="usage")
        if len(cleaned) > 1:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                "补件一次只接受一个文件",
                kind="usage",
                hint="需要多个文件请分次调用；批量补件请用 tb_import_files。",
            )
        return _client().supplement_work(int(work_id), cleaned)

    return run_tool(
        "tb_work_supplement",
        impl,
        args={"work_id": work_id, "paths": list(paths or [])},
        write=True,
        caller=caller,
    )


def collection_assign(
    title_or_id: str | int, work_ids: Sequence[int], *, caller: str | None = None
):
    def impl() -> dict[str, Any]:
        ids = [int(v) for v in (work_ids or []) if str(v).isdigit()]
        if not ids:
            raise ControlError(CODE_INVALID_ARGUMENT, "work_ids 不能为空", kind="usage")
        client = _client()
        collection_id = client.find_collection(title_or_id)
        created = False
        if collection_id is None:
            if isinstance(title_or_id, int) or str(title_or_id).isdigit():
                raise ControlError(
                    "collection_not_found",
                    f"合集 id {title_or_id} 不存在",
                    kind="business",
                    hint="用 tb_collection_list 看现有合集；传标题会自动创建。",
                )
            created_collection = client.create_collection(str(title_or_id))
            collection_id = int(created_collection["id"])
            created = True
        result = client.add_collection_works(collection_id, ids)
        return {"collection_id": collection_id, "created": created, **result}

    return run_tool(
        "tb_collection_assign",
        impl,
        args={"title_or_id": title_or_id, "work_ids": list(work_ids or [])},
        write=True,
        caller=caller,
    )


def platform_pull(
    platform: str,
    mode: str = "like",
    limit: int = 50,
    enqueue: bool = True,
    profile_url: str | None = None,
    *,
    caller: str | None = None,
):
    def impl() -> dict[str, Any]:
        if platform not in {"douyin", "bilibili", "xiaohongshu"}:
            raise ControlError(
                CODE_INVALID_ARGUMENT,
                f"不支持的平台：{platform}",
                kind="usage",
                hint="可选 douyin / bilibili / xiaohongshu。",
            )
        result = _client().platform_pull(
            platform,
            mode=mode,
            limit=_clamp(limit, 1, 200, 50),
            enqueue=enqueue,
            profile_url=profile_url,
        )
        items = result.get("items") or []
        result["items"] = items[:20]
        result["items_truncated"] = max(0, len(items) - 20)
        return result

    return run_tool(
        "tb_platform_pull",
        impl,
        args={"platform": platform, "mode": mode, "limit": limit, "enqueue": enqueue},
        write=True,
        caller=caller,
    )


# ------------------------------------------------------------------ 服务生命周期


def service_start(wait_seconds: int = 40, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        return service.start(timeout=_clamp(wait_seconds, 10, 120, 40))

    return run_tool(
        "tb_service_start", impl, args={"wait_seconds": wait_seconds}, write=True, caller=caller
    )


def service_stop(wait_seconds: int = 30, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        return service.stop(timeout=_clamp(wait_seconds, 5, 120, 30))

    return run_tool(
        "tb_service_stop",
        impl,
        args={"wait_seconds": wait_seconds},
        write=True,
        destructive=True,
        caller=caller,
    )


def service_restart(wait_seconds: int = 40, *, caller: str | None = None):
    def impl() -> dict[str, Any]:
        return service.restart(timeout=_clamp(wait_seconds, 10, 120, 40))

    return run_tool(
        "tb_service_restart",
        impl,
        args={"wait_seconds": wait_seconds},
        write=True,
        destructive=True,
        caller=caller,
    )


def audit_tail(count: int = 20, *, caller: str | None = None):
    return run_tool(
        "tb_audit_tail",
        lambda: {"entries": audit.tail(_clamp(count, 1, 200, 20))},
        args={"count": count},
        caller=caller,
    )
