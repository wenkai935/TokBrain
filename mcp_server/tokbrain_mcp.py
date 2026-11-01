"""TokBrain 只读 MCP 服务。

把本地视频知识库暴露给 Hermes Agent / Claude Desktop / 任何 MCP 客户端。

设计约束
--------
1. 只读：所有工具都标注 ``readOnlyHint``，没有任何写操作。
2. 隔离：独立进程、独立端口（默认 8010）、独立 venv（.venv-mcp），
   不改动 TokBrain 主服务的 127.0.0.1:8001 local-only 设计。
3. 一致：检索复用 ``app.services.knowledge.search``，与产品内问答行为完全一致，
   包括证据约束（只返回已入库、已处理、且有原始证据的作品）。

启动
----
    .venv-mcp/Scripts/python.exe mcp_server/tokbrain_mcp.py

环境变量
--------
TOKBRAIN_MCP_HOST            绑定地址，默认 127.0.0.1；非回环监听必须设置令牌
TOKBRAIN_MCP_PORT            端口，默认 8010
TOKBRAIN_MCP_TOKEN           访问令牌；设置后所有请求需带 Authorization: Bearer <token>
TOKBRAIN_MCP_ALLOWED_HOSTS   允许的 Host 头（逗号分隔）；非回环监听默认开启防护
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.database import database  # noqa: E402
from app.models import (  # noqa: E402
    Collection,
    CollectionMembership,
    KnowledgeChunk,
    Work,
    WorkSummary,
)
from app.services import knowledge  # noqa: E402


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _fmt_ts(seconds: float | None) -> str:
    """秒 -> HH:MM:SS，方便模型直接引用视频位置。"""
    if seconds is None:
        return "--:--:--"
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _dump(payload: dict[str, Any]) -> str:
    """统一以 JSON 文本返回，避免不同客户端的 structured_content 兼容差异。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _snippet(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "…"


async def _lexical_only_search(
    session: Any, query: str, top_k: int
) -> list[dict[str, Any]]:
    """纯词法兜底检索。

    当 embedding 不可用（例如 DPAPI 密钥解不开、预算超限、百炼不通）时使用。
    过滤条件与 ``knowledge.search`` 完全对齐，只是跳过向量通道，
    并复用它的 ``_score_chunks`` 打分逻辑，保证行为一致。
    """
    import asyncio

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
                "score": round(lexical, 4),
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

    results = sorted(grouped.values(), key=lambda i: i["score"], reverse=True)[:top_k]
    for item in results:
        item["text"] = "\n\n".join(item.pop("texts"))
    return results


mcp = MCPServer(
    name="tokbrain",
    title="TokBrain 视频知识库",
    version="0.1.0",
    instructions=(
        "TokBrain 是一个本地视频知识库：视频经过转写/OCR/关键帧分析后被切成带时间戳的"
        "知识片段。用 kb_search 做语义检索，用 kb_get_transcript 取某个视频的完整时间轴，"
        "用 kb_get_work 看单个视频的摘要与元数据。回答时请引用 title 与 timestamp，"
        "并给出 url，让用户能跳回原始视频。"
    ),
)


@mcp.tool(
    name="kb_search",
    description=(
        "在 TokBrain 视频知识库中做混合检索（向量语义 + 词法匹配）。"
        "返回按相关度排序的知识片段，每条带所属视频标题、时间戳、合集和原始链接。"
        "适合回答「我收藏的视频里讲过什么 / 某个观点出自哪个视频」这类问题。"
    ),
    annotations=READ_ONLY,
)
async def kb_search(
    query: str,
    top_k: int = 5,
    collection: str | None = None,
) -> str:
    """检索知识库。

    Args:
        query: 自然语言问题或关键词。
        top_k: 返回的作品数量，1-20。
        collection: 只在这些合集标题内检索（可选）。
    """
    if not query or not query.strip():
        return _dump({"ok": False, "error": "query 不能为空"})
    limit = max(1, min(int(top_k), 20))
    # 先多取一些，给合集过滤留余量
    fetch_k = min(limit * 3, 60) if collection else limit
    mode = "hybrid"
    note: str | None = None
    try:
        async with database.session() as session:
            hits = await knowledge.search(session, query.strip(), top_k=fetch_k)
    except Exception as exc:
        # embedding 密钥不可用 / 预算超限 / 供应商不可达 —— 降级为纯词法，不让检索整体失败
        logger.warning("混合检索不可用，降级为词法检索: {}", exc)
        mode = "lexical"
        note = f"语义检索不可用（{exc}），本次结果仅基于关键词匹配"
        try:
            async with database.session() as session:
                hits = await _lexical_only_search(session, query.strip(), fetch_k)
        except Exception as fallback_exc:
            logger.warning("kb_search 兜底也失败: {}", fallback_exc)
            return _dump({"ok": False, "error": f"检索失败: {fallback_exc}"})

    if collection:
        hits = [h for h in hits if (h.get("collection") or "") == collection]
    hits = hits[:limit]

    results = []
    for hit in hits:
        results.append(
            {
                "work_id": hit.get("work_id"),
                "title": hit.get("title"),
                "collection": hit.get("collection"),
                "source_kind": hit.get("source_kind"),
                "timestamp": _fmt_ts(hit.get("timestamp_seconds")),
                "timestamp_seconds": hit.get("timestamp_seconds"),
                "url": hit.get("external_url"),
                "score": hit.get("score"),
                "snippet": _snippet(hit.get("text") or "", 700),
            }
        )
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "count": len(results),
        "mode": mode,
        "results": results,
    }
    if note:
        payload["note"] = note
    return _dump(payload)


@mcp.tool(
    name="kb_get_work",
    description=(
        "按 work_id 取单个视频的完整档案：标题、作者、时长、平台链接、所属合集、"
        "一句话总结、结构化摘要与标签。适合在 kb_search 命中后深入了解某个视频。"
    ),
    annotations=READ_ONLY,
)
async def kb_get_work(work_id: int) -> str:
    """取视频档案。

    Args:
        work_id: kb_search 返回的作品 id。
    """
    try:
        async with database.session() as session:
            work = await session.get(Work, int(work_id))
            if work is None:
                return _dump({"ok": False, "error": f"找不到 work_id={work_id}"})
            summary = (
                await session.execute(
                    select(WorkSummary)
                    .where(WorkSummary.work_id == work.id)
                    .order_by(WorkSummary.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            collection_title = (
                await session.execute(
                    select(Collection.title)
                    .join(CollectionMembership, CollectionMembership.collection_id == Collection.id)
                    .where(CollectionMembership.work_id == work.id)
                    .order_by(Collection.sort_order)
                    .limit(1)
                )
            ).scalar_one_or_none()
            chunk_count = (
                await session.execute(
                    select(func.count(KnowledgeChunk.id)).where(
                        KnowledgeChunk.work_id == work.id
                    )
                )
            ).scalar_one()
    except Exception as exc:
        logger.warning("kb_get_work 失败: {}", exc)
        return _dump({"ok": False, "error": f"查询失败: {exc}"})

    payload: dict[str, Any] = {
        "ok": True,
        "work_id": work.id,
        "title": work.title,
        "platform": work.platform,
        "author": work.author_name,
        "duration": _fmt_ts(work.duration_seconds),
        "duration_seconds": work.duration_seconds,
        "url": work.source_url,
        "collection": collection_title,
        "chunk_count": chunk_count,
        "published_at": work.published_at.isoformat() if work.published_at else None,
        "library_state": work.library_state,
        "processing_state": work.processing_state,
        "evidence_state": work.evidence_state,
        "description": _snippet(work.description or "", 600),
    }
    if summary is not None:
        payload["summary"] = {
            "one_sentence": summary.one_sentence,
            "detail": summary.content_json,
            "tags": summary.tags,
            "generated_at": summary.generated_at.isoformat() if summary.generated_at else None,
        }
    return _dump(payload)


@mcp.tool(
    name="kb_get_transcript",
    description=(
        "取某个视频的知识片段时间轴（默认按时间从前往后）。"
        "可传 start_seconds / end_seconds 只看某一段，适合复盘某个具体环节。"
        "每条都带时间戳，可直接定位到视频画面。"
    ),
    annotations=READ_ONLY,
)
async def kb_get_transcript(
    work_id: int,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    limit: int = 40,
) -> str:
    """取视频时间轴。

    Args:
        work_id: 作品 id。
        start_seconds: 起始秒（含），可选。
        end_seconds: 结束秒（含），可选。
        limit: 最多返回片段数，1-200。
    """
    max_rows = max(1, min(int(limit), 200))
    try:
        async with database.session() as session:
            stmt = (
                select(KnowledgeChunk)
                .where(KnowledgeChunk.work_id == int(work_id))
                .order_by(KnowledgeChunk.chunk_index)
            )
            if start_seconds is not None:
                stmt = stmt.where(KnowledgeChunk.end_seconds >= float(start_seconds))
            if end_seconds is not None:
                stmt = stmt.where(KnowledgeChunk.start_seconds <= float(end_seconds))
            rows = (await session.execute(stmt.limit(max_rows))).scalars().all()
            total = (
                await session.execute(
                    select(func.count(KnowledgeChunk.id)).where(
                        KnowledgeChunk.work_id == int(work_id)
                    )
                )
            ).scalar_one()
    except Exception as exc:
        logger.warning("kb_get_transcript 失败: {}", exc)
        return _dump({"ok": False, "error": f"查询失败: {exc}"})

    if not rows:
        return _dump(
            {"ok": False, "error": f"work_id={work_id} 没有匹配的知识片段", "total_chunks": total}
        )
    segments = [
        {
            "timestamp": _fmt_ts(row.start_seconds),
            "start_seconds": row.start_seconds,
            "end_seconds": row.end_seconds,
            "source_kind": row.source_kind,
            "text": _snippet(row.text, 900),
        }
        for row in rows
    ]
    return _dump(
        {
            "ok": True,
            "work_id": int(work_id),
            "returned": len(segments),
            "total_chunks": total,
            "segments": segments,
        }
    )


@mcp.tool(
    name="kb_list_collections",
    description=(
        "列出知识库里的所有合集（专题）及各自的作品数。"
        "可以先看有哪些主题，再用 kb_search 的 collection 参数缩小范围。"
    ),
    annotations=READ_ONLY,
)
async def kb_list_collections() -> str:
    """列出合集。"""
    try:
        async with database.session() as session:
            rows = (
                await session.execute(
                    select(
                        Collection.id,
                        Collection.key,
                        Collection.title,
                        func.count(CollectionMembership.work_id).label("works"),
                    )
                    .outerjoin(
                        CollectionMembership,
                        CollectionMembership.collection_id == Collection.id,
                    )
                    .group_by(Collection.id)
                    .order_by(Collection.sort_order, Collection.id)
                )
            ).all()
    except Exception as exc:
        logger.warning("kb_list_collections 失败: {}", exc)
        return _dump({"ok": False, "error": f"查询失败: {exc}"})

    return _dump(
        {
            "ok": True,
            "count": len(rows),
            "collections": [
                {"id": r.id, "key": r.key, "title": r.title, "works": r.works} for r in rows
            ],
        }
    )


@mcp.tool(
    name="kb_stats",
    description="知识库总览：作品数、知识片段数、可检索状态分布。用来判断库里到底有没有东西。",
    annotations=READ_ONLY,
)
async def kb_stats() -> str:
    """库概览。"""
    try:
        async with database.session() as session:
            total_works = (await session.execute(select(func.count(Work.id)))).scalar_one()
            in_library = (
                await session.execute(
                    select(func.count(Work.id)).where(Work.library_state == "in_library")
                )
            ).scalar_one()
            processed = (
                await session.execute(
                    select(func.count(Work.id)).where(Work.processing_state == "processed")
                )
            ).scalar_one()
            total_chunks = (
                await session.execute(select(func.count(KnowledgeChunk.id)))
            ).scalar_one()
            by_kind = (
                await session.execute(
                    select(KnowledgeChunk.source_kind, func.count(KnowledgeChunk.id))
                    .group_by(KnowledgeChunk.source_kind)
                )
            ).all()
            platforms = (
                await session.execute(
                    select(Work.platform, func.count(Work.id)).group_by(Work.platform)
                )
            ).all()
    except Exception as exc:
        logger.warning("kb_stats 失败: {}", exc)
        return _dump({"ok": False, "error": f"查询失败: {exc}"})

    return _dump(
        {
            "ok": True,
            "works_total": total_works,
            "works_in_library": in_library,
            "works_processed": processed,
            "chunks_total": total_chunks,
            "chunks_by_source": {kind: count for kind, count in by_kind},
            "works_by_platform": {platform: count for platform, count in platforms},
        }
    )


# --------------------------------------------------------------------------- #
# HTTP 传输层：Bearer 鉴权 + 监听配置
# --------------------------------------------------------------------------- #


try:  # 与 8011 控制面共用同一份鉴权实现（纯 stdlib，可在 .venv 里直接测）
    from mcp_server._http_guard import BearerGuard as _BearerGuard
except ImportError:  # 直接以脚本方式运行时 sys.path[0] 是 mcp_server/
    from _http_guard import BearerGuard as _BearerGuard  # type: ignore[no-redef]


def build_app() -> Any:
    """构造 ASGI 应用（供 uvicorn 使用）。"""
    from mcp.server.transport_security import TransportSecuritySettings

    host = os.getenv("TOKBRAIN_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    token = os.getenv("TOKBRAIN_MCP_TOKEN", "").strip()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback and not token:
        raise RuntimeError("MCP 非回环监听必须配置 TOKBRAIN_MCP_TOKEN")

    allowed = [
        h.strip()
        for h in os.getenv("TOKBRAIN_MCP_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    if not loopback and not allowed:
        allowed = [host]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed),
        allowed_hosts=list(allowed),
        allowed_origins=list(allowed),
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=host,
        transport_security=security,
    )
    if token:
        app = _BearerGuard(app, token)
        logger.info("已启用 Bearer 令牌校验")
    else:
        logger.info("MCP 仅监听回环地址，未启用 Bearer 令牌")
    return app


def main() -> None:
    import uvicorn

    host = os.getenv("TOKBRAIN_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("TOKBRAIN_MCP_PORT", "8010"))
    logger.info(f"TokBrain MCP 服务启动: http://{host}:{port}/mcp")
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
