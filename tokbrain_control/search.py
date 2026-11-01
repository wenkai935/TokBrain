"""检索（**唯一**的可选依赖模块）。

``.venv`` / ``.venv-mcp`` 里都有 SQLAlchemy 和 ``app.*``，所以这里有条件地复用
产品自己的检索实现，保证「控制面搜到的」和「产品内搜到的」是同一套结果。

裸 Python（没有 SQLAlchemy）下会抛 :class:`SearchUnavailable`，CLI 明确报
``exit 3`` 并给出 hint，而不是假装搜不到东西。

注意：这里**只读**。``app.services.knowledge.search`` 本身只做 SELECT；向量表不可用
时它会自动降级成词法打分，我们再补一层纯词法兜底（与 8010 的 ``kb_search`` 同构）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from .errors import CODE_SEARCH_UNAVAILABLE, ControlError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNIPPET_LIMIT = 700


class SearchUnavailable(ControlError):
    def __init__(self, message: str, *, hint: str | None = None, detail: Any = None):
        super().__init__(
            CODE_SEARCH_UNAVAILABLE,
            message,
            kind="offline",
            hint=hint
            or "检索需要项目依赖（SQLAlchemy + app.services.knowledge）。"
            "请用 .venv 或 .venv-mcp 下的 python 运行；其余命令不受影响。",
            detail=detail,
        )


def _fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "…"


def _ensure_path() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))


async def _lexical_only(session: Any, query: str, top_k: int) -> list[dict[str, Any]]:
    """纯词法兜底：跳过向量通道，但过滤条件与 ``knowledge.search`` 完全一致。"""
    from sqlalchemy import select

    from app.models import Collection, CollectionMembership, KnowledgeChunk, Work
    from app.services import knowledge

    grounded = (
        select(KnowledgeChunk.work_id)
        .where(KnowledgeChunk.source_kind.in_(knowledge.ORIGINAL_EVIDENCE_KINDS))
        .distinct()
    )
    rows = (
        await session.execute(
            select(KnowledgeChunk, Work)
            .join(Work, Work.id == KnowledgeChunk.work_id)
            .where(
                Work.library_state == "in_library",
                Work.processing_state == "processed",
                Work.evidence_state.in_({"sufficient", "unverified"}),
                KnowledgeChunk.source_kind.in_(knowledge.RETRIEVABLE_SOURCE_KINDS),
                Work.id.in_(grounded),
            )
        )
    ).all()
    if not rows:
        return []

    scored = await asyncio.to_thread(
        knowledge._score_chunks, query, None, list(rows), None
    )
    collection_map: dict[int, str] = {}
    memberships = (
        await session.execute(
            select(CollectionMembership.work_id, Collection.title)
            .join(Collection, Collection.id == CollectionMembership.collection_id)
            .order_by(Collection.sort_order)
        )
    ).all()
    for work_id, title in memberships:
        collection_map.setdefault(work_id, title)

    grouped: dict[int, dict[str, Any]] = {}
    for _score, _semantic, lexical, chunk, work in scored:
        if lexical <= 0:
            continue
        item = grouped.get(work.id)
        if not item:
            item = {
                "score": round(float(lexical), 4),
                "texts": [],
                "source_kind": chunk.source_kind,
                "timestamp_seconds": chunk.start_seconds,
                "work_id": work.id,
                "platform_work_id": work.platform_work_id,
                "title": work.title,
                "collection": collection_map.get(work.id),
                "external_url": work.source_url,
            }
            grouped[work.id] = item
        if len(item["texts"]) < 2 and chunk.text not in item["texts"]:
            item["texts"].append(chunk.text)

    results = sorted(grouped.values(), key=lambda i: i["score"], reverse=True)
    return results[:top_k]


async def _run(
    query: str, top_k: int, collection: str | None
) -> dict[str, Any]:
    try:
        _ensure_path()
        from app.database import database  # noqa: F401
        from app.services import knowledge  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 缺依赖/配置错误都要转成明确提示
        raise SearchUnavailable(f"无法加载检索依赖：{exc}", detail=repr(exc)) from exc

    from app.database import database
    from app.services import knowledge

    note: str | None = None
    mode = "hybrid"
    fetch_k = top_k * 3 if collection else top_k
    needle = " ".join(collection.split()).lower() if collection else None

    async with database.session() as session:
        try:
            raw = await knowledge.search(session, query, max(fetch_k, top_k))
        except Exception as exc:  # noqa: BLE001 - 向量/密钥/供应商问题一律降级
            note = f"混合检索不可用，已降级为词法匹配：{type(exc).__name__}: {exc}"
            mode = "lexical"
            try:
                raw = await _lexical_only(session, query, max(fetch_k, top_k))
            except Exception as inner:  # noqa: BLE001
                raise SearchUnavailable(
                    f"检索失败：{inner}", detail={"vector_error": str(exc)}
                ) from inner

    if needle:
        raw = [r for r in raw if str(r.get("collection") or "").lower() == needle]
        if not raw:
            note = (note + "；" if note else "") + f"合集「{collection}」内没有命中"

    results = [
        {
            "work_id": item.get("work_id"),
            "title": item.get("title"),
            "collection": item.get("collection"),
            "source_kind": item.get("source_kind"),
            "timestamp": _fmt_ts(item.get("timestamp_seconds")),
            "timestamp_seconds": item.get("timestamp_seconds"),
            "url": item.get("external_url"),
            "score": item.get("score"),
            "snippet": _snippet(item.get("text") or ""),
        }
        for item in raw[:top_k]
    ]
    return {
        "mode": mode,
        "query": query,
        "collection": collection,
        "count": len(results),
        "results": results,
        "note": note,
        "evidence_only": True,
        "reminder": "这些是证据片段，不含生成式答案；请依据它们作答并标注标题与时间戳。",
    }


def search(query: str, top_k: int = 5, collection: str | None = None) -> dict[str, Any]:
    """同步检索入口。query 空串视为参数错误。"""
    query = (query or "").strip()
    if not query:
        raise ControlError("invalid_argument", "检索词不能为空", kind="usage")
    top_k = max(1, min(int(top_k or 5), 20))
    try:
        return asyncio.run(_run(query, top_k, collection))
    except RuntimeError as exc:
        # 已在事件循环里被直接调用（不该发生：MCP 侧走 to_thread）
        raise SearchUnavailable(f"检索无法在现有事件循环中执行：{exc}") from exc


async def _transcript(
    work_id: int, limit: int, start: float | None, end: float | None
) -> list[dict[str, Any]]:
    _ensure_path()
    from sqlalchemy import select

    from app.database import database
    from app.models import KnowledgeChunk

    query = select(KnowledgeChunk).where(KnowledgeChunk.work_id == work_id)
    if start is not None:
        query = query.where(KnowledgeChunk.end_seconds >= start)
    if end is not None:
        query = query.where(KnowledgeChunk.start_seconds <= end)
    async with database.session() as session:
        rows = (
            (
                await session.execute(
                    query.order_by(KnowledgeChunk.chunk_index).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "chunk_index": row.chunk_index,
            "source_kind": row.source_kind,
            "timestamp": _fmt_ts(row.start_seconds),
            "start_seconds": row.start_seconds,
            "end_seconds": row.end_seconds,
            "text": _snippet(row.text, 900),
        }
        for row in rows
    ]


def transcript(
    work_id: int,
    *,
    limit: int = 8,
    start: float | None = None,
    end: float | None = None,
) -> list[dict[str, Any]]:
    """读一个作品的知识片段（时间轴）。只读。"""
    limit = max(1, min(int(limit or 8), 200))
    try:
        return asyncio.run(_transcript(int(work_id), limit, start, end))
    except RuntimeError as exc:
        raise SearchUnavailable(f"读取片段失败：{exc}") from exc
    except ControlError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SearchUnavailable(f"读取片段需要项目依赖：{exc}", detail=repr(exc)) from exc
