"""飞书发现批次：静默补齐完成后再一次性交给用户决策。

用户定的交互约定（2026-11-02）：

- 补齐期间前端一律不显示，数字不许跳。
- 补齐结束才一次性弹出，清单里同时带出本轮「取不到信息」的条目。

因此这里不需要额外的「轮次」表：``dispatch`` 只要还有非终态记录，就说明
bridge 正在跑这一轮，直接返回 ``filling``，前端保持静默。等全部落到终态，
``awaiting_confirm`` 的条数就固定了，此时数字不会再变。

一个飞书行 = 一个 import batch（bridge 的 ``process_link`` 逐条建批），所以
确认时要按 batch 分组逐批 confirm。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportItem
from app.services.dispatch_store import DispatchStore

#: 清单一次最多列出的条目数（超出部分只报数量，避免把页面撑爆）。
MAX_ITEMS = 10

#: 「取不到信息」只统计这个时间窗内的失败，避免把历史积压（如 429 批量失败）
#: 混进本轮发现，让用户误以为刚刚又失败了 N 条。
UNRESOLVED_WINDOW_MINUTES = 30


@lru_cache(maxsize=1)
def _store() -> DispatchStore:
    return DispatchStore()


def reset_store_cache() -> None:
    """测试用：切换 dispatch.db 路径后清掉缓存的 store。"""
    _store.cache_clear()


def _recent_since(minutes: int) -> str:
    """dispatch 的时间列是 'YYYY-MM-DD HH:MM:SS'，字典序即时间序。"""
    return (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _snapshot() -> dict:
    store = _store()
    terminal = store.TERMINAL
    con = store._connect()
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "SELECT record_id, link, tags, status, batch_id, lid, "
                "error_code, error_message, updated_at FROM dispatch"
            )
        ]
    finally:
        con.close()
    cutoff = _recent_since(UNRESOLVED_WINDOW_MINUTES)
    return {
        # 非终态 = bridge 这一轮还没跑完（含 queued / processing 等）
        "pending": [row for row in rows if row["status"] not in terminal],
        # 预检已补齐、只等用户点「解析」的条目
        "awaiting": [
            row for row in rows if row["status"] == "awaiting_confirm" and row["batch_id"]
        ],
        # 本轮取不到信息的条目：预检失败没建 batch，只能按链接 + 原因展示
        "unresolved": [
            row
            for row in rows
            if row["status"] == "failed" and (row["updated_at"] or "") >= cutoff
        ],
    }


def _empty(state: str, *, filling: int = 0) -> dict:
    return {
        "state": state,
        "filling": filling,
        "total": 0,
        "resolvable": 0,
        "unresolved": 0,
        "items": [],
        "truncated": 0,
    }


async def _items_by_batch(session: AsyncSession, batch_ids: list[str]) -> dict:
    if not batch_ids:
        return {}
    rows = (
        await session.execute(
            select(ImportItem).where(ImportItem.batch_id.in_(batch_ids))
        )
    ).scalars()
    # 一个飞书行对应一个批次，批次里通常只有一条；多条目时取序号最小的作为代表。
    picked: dict = {}
    for item in rows:
        current = picked.get(item.batch_id)
        if current is None or (item.ordinal or 0) < (current.ordinal or 0):
            picked[item.batch_id] = item
    return picked


def _ready_item(record: dict, item: ImportItem) -> dict:
    return {
        "record_id": record["record_id"],
        "batch_id": record["batch_id"],
        "item_id": item.id,
        "link": record["link"],
        "lid": record["lid"],
        "state": "ready",
        "title": item.title or "",
        "author_name": item.author_name or "",
        "platform": item.platform or "douyin",
        "kind": item.kind or "video",
        "cover_url": item.cover_url or "",
        "duration_seconds": float(item.duration_seconds or 0),
        "platform_work_id": item.platform_work_id or "",
        "reason": "",
    }


def _unresolved_item(record: dict) -> dict:
    return {
        "record_id": record["record_id"],
        "batch_id": "",
        "item_id": None,
        "link": record["link"],
        "lid": record["lid"],
        "state": "unresolved",
        "title": "",
        "author_name": "",
        "platform": "douyin",
        "kind": "video",
        "cover_url": "",
        "duration_seconds": 0.0,
        "platform_work_id": "",
        "reason": (record["error_message"] or "预检未取到信息")[:160],
    }


async def discovery_view(session: AsyncSession) -> dict:
    """聚合「飞书发现」卡片需要的一切：状态 + 计数 + 清单。"""
    snap = _snapshot()

    # 补齐还没跑完：静默，连数字都不给，避免用户看到中途跳变。
    if snap["pending"]:
        return _empty("filling", filling=len(snap["pending"]))

    awaiting = sorted(snap["awaiting"], key=lambda row: row["updated_at"] or "")
    unresolved = sorted(snap["unresolved"], key=lambda row: row["updated_at"] or "")
    if not awaiting and not unresolved:
        return _empty("idle")

    picked = await _items_by_batch(session, [row["batch_id"] for row in awaiting])
    ready = [
        _ready_item(row, picked[row["batch_id"]])
        for row in awaiting
        if row["batch_id"] in picked  # 批次被删掉的行不展示，避免点了解析报 404
    ]

    # 可解析项排前面，取不到信息的排后面；清单整体封顶 MAX_ITEMS 条。
    ordered = [*ready, *[_unresolved_item(row) for row in unresolved]]
    shown = ordered[:MAX_ITEMS]
    return {
        "state": "ready",
        "filling": 0,
        "total": len(ordered),
        "resolvable": len(ready),
        "unresolved": len(unresolved),
        "items": shown,
        "truncated": len(ordered) - len(shown),
    }


async def approve_discovery(session: AsyncSession, *, item_ids: list[int]) -> dict:
    """用户确认后：逐批 confirm → 建 ingest job → 把 dispatch 推进到 ingested。

    ``ingested`` 是本次新增的终态，含义是「已确认并提交解析，等 bridge 回填
    Obsidian」。之所以不直接标 processed：回填笔记要访问 Vault 文件系统，只
    有 bridge 能做；标成 processed 会让它以为笔记已回填而跳过。
    """
    wanted = {int(value) for value in item_ids}
    if not wanted:
        raise ValueError("请选择至少一个待解析作品")

    store = _store()
    con = store._connect()
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "SELECT record_id, batch_id, status FROM dispatch "
                "WHERE status='awaiting_confirm' AND batch_id != ''"
            )
        ]
    finally:
        con.close()
    by_batch_row = {row["batch_id"]: row for row in rows}

    items = (
        (
            await session.execute(select(ImportItem).where(ImportItem.id.in_(wanted)))
        )
        .scalars()
        .all()
    )
    if not items:
        raise ValueError("所选条目不存在，请刷新后重试")
    blocked = [
        item.id
        for item in items
        if item.batch_id not in by_batch_row or item.status not in {"ready", "queued"}
    ]
    if blocked:
        raise ValueError(f"条目 {blocked} 当前不可解析，请刷新后重试")

    grouped: dict[str, list[int]] = {}
    for item in items:
        grouped.setdefault(item.batch_id, []).append(item.id)

    from app.services.import_queue import confirm_import_items
    from app.services.jobs import enqueue_ingest_job

    work_ids: list[int] = []
    confirmed_batches: list[str] = []
    batch_work: dict[str, int] = {}
    for batch_id, ids in grouped.items():
        await confirm_import_items(session, batch_id, ids)
        confirmed_batches.append(batch_id)
        fresh = (
            (
                await session.execute(
                    select(ImportItem).where(ImportItem.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        for item in fresh:
            if item.existing_work_id:
                wid = int(item.existing_work_id)
                work_ids.append(wid)
                batch_work[batch_id] = wid

    work_ids = list(dict.fromkeys(work_ids))
    job = None
    if work_ids:
        job = await enqueue_ingest_job(session, work_ids=work_ids)
    await session.commit()

    # 批次已确认且有作品的才推进 dispatch；没拿到 work_id 的留在 awaiting_confirm，
    # 让用户刷新后还能重新点一次，而不是被静默吞掉。
    for batch_id in confirmed_batches:
        wid = batch_work.get(batch_id)
        if wid:
            store.update(
                by_batch_row[batch_id]["record_id"],
                status="ingested",
                work_id=wid,
                error_code="",
                error_message="",
            )
    return {
        "confirmed": len(items),
        "work_ids": work_ids,
        "job_id": job.id if job else "",
        "remaining": max(0, len(rows) - len(confirmed_batches)),
    }
