"""Small local vector/lexical retrieval layer for personal collections."""

from __future__ import annotations

import asyncio
import math
import re

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Collection,
    CollectionMembership,
    KnowledgeChunk,
    Work,
)
from app.services.budget import consume, record_usage, release, reserve
from app.services.model_profiles import get_active_profile
from app.services.pricing import PRICE_VERSION
from app.services.providers import DashScopeProvider, OpenAICompatibleProvider
from app.services.secrets import get_secret
from app.services.vector_store import ensure_vec0, vector_candidates


ORIGINAL_EVIDENCE_KINDS = {"subtitle", "transcript", "ocr", "visual"}
RETRIEVABLE_SOURCE_KINDS = ORIGINAL_EVIDENCE_KINDS | {"notes"}
# KNN recall is larger than the final result set so lexical fusion still has
# candidates to choose from within each work.
VECTOR_CANDIDATE_MULTIPLIER = 10


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else -1.0


def lexical_score(question: str, text: str) -> float:
    normalized_question = re.sub(r"\s+", "", question.lower())
    lower = re.sub(r"\s+", "", text.lower())
    if not normalized_question or not lower:
        return 0.0
    terms: set[str] = set()
    for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", normalized_question):
        terms.add(token)
        for sequence in re.findall(r"[\u4e00-\u9fff]+", token):
            for width in (2, 3):
                terms.update(
                    sequence[index : index + width]
                    for index in range(max(0, len(sequence) - width + 1))
                )
    matched = sum(1.0 for term in terms if term in lower) / max(1, len(terms))
    exact_phrase = (
        1.5 if len(normalized_question) >= 2 and normalized_question in lower else 0
    )
    return matched + exact_phrase


def _build_provider(api_key: str, profile: dict):
    """Configured provider for retrieval embedding.

    Resolves ``DashScopeProvider`` through this module's namespace so tests
    monkeypatching ``knowledge.DashScopeProvider`` keep working.  Mirrors
    ``providers.get_provider`` for the non-default provider type.
    """
    if str(profile.get("provider_type") or settings.provider_type) == "openai":
        return OpenAICompatibleProvider(
            api_key,
            base_url=profile.get("base_url") or settings.dashscope_base_url,
            embedding_model=(
                profile.get("embedding_model") or settings.provider_embedding_model
            ),
            chat_model=profile.get("chat_model") or settings.provider_chat_model,
        )
    return DashScopeProvider(
        api_key,
        base_url=profile.get("base_url") or settings.dashscope_base_url,
        embedding_model=profile.get("embedding_model") or None,
        chat_model=profile.get("chat_model") or None,
    )


async def _embed_question(session: AsyncSession, question: str) -> list[float] | None:
    """Embed the query with budget accounting; None on failure (lexical fallback)."""
    profile = await get_active_profile(session)
    api_key = await get_secret(session, str(profile["api_key_ref"]))
    if not api_key and profile.get("builtin"):
        api_key = await get_secret(session, "dashscope_api_key")
    if not api_key:
        return None
    reservation = await reserve(
        session, works=0, llm_tokens=max(512, len(question) // 2 + 256)
    )
    try:
        provider = _build_provider(api_key, profile)
        vectors, usage = await provider.embed([question])
        await record_usage(
            session,
            model=usage.model,
            metric=usage.metric,
            quantity=usage.quantity,
            unit=usage.unit,
            estimated_cost_cny=usage.cost_cny,
            metadata={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
            price_version=PRICE_VERSION,
        )
        await consume(
            session,
            reservation,
            actual_llm_tokens=usage.input_tokens + usage.output_tokens,
        )
        return vectors[0] if vectors else None
    except Exception as exc:
        await release(session, reservation)
        logger.warning("向量检索不可用，已降级为本地词法检索: {}", exc)
        return None


def _score_chunks(
    question: str,
    query_vector: list[float] | None,
    rows: list[tuple[KnowledgeChunk, Work]],
    distances: dict[int, float] | None = None,
) -> list[tuple[float, bool, float, KnowledgeChunk, Work]]:
    """CPU-only ranking kept outside the API event loop for large libraries.

    ``distances`` carries sqlite-vec cosine distances (``{chunk_id: distance}``)
    when the KNN pre-filter was used; otherwise similarity is recomputed from
    the stored embeddings exactly like the original brute-force path.
    """

    scored: list[tuple[float, bool, float, KnowledgeChunk, Work]] = []
    for chunk, work in rows:
        lexical = lexical_score(
            question,
            f"{work.title}\n{work.description}\n{chunk.text}",
        )
        if distances is not None:
            distance = distances.get(chunk.id)
            semantic = distance is not None
            semantic_score = 1.0 - distance if semantic else -1.0
        else:
            semantic = bool(query_vector and chunk.embedding)
            semantic_score = cosine(query_vector, chunk.embedding) if semantic else -1.0
        score = semantic_score + min(2.0, lexical) * 0.6 if semantic else lexical
        scored.append((score, semantic, lexical, chunk, work))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


async def search(session: AsyncSession, question: str, top_k: int = 8) -> list[dict]:
    grounded_work_ids = (
        select(KnowledgeChunk.work_id)
        .where(KnowledgeChunk.source_kind.in_(ORIGINAL_EVIDENCE_KINDS))
        .distinct()
    )
    work_id_rows = await session.execute(
        select(Work.id)
        .where(
            Work.library_state == "in_library",
            Work.processing_state == "processed",
            Work.evidence_state.in_({"sufficient", "unverified"}),
            Work.id.in_(grounded_work_ids),
        )
    )
    work_ids = set(work_id_rows.scalars().all())
    if not work_ids:
        return []
    collection_map: dict[int, str] = {}
    collection_rows = (
        await session.execute(
            select(CollectionMembership.work_id, Collection.title)
            .join(Collection, Collection.id == CollectionMembership.collection_id)
            .where(
                CollectionMembership.work_id.in_(work_ids),
            )
            .order_by(Collection.sort_order)
        )
    ).all()
    for work_id, title in collection_rows:
        collection_map.setdefault(work_id, title)

    query_vector = await _embed_question(session, question)
    distances: dict[int, float] | None = None
    if query_vector is not None and await ensure_vec0(session):
        try:
            candidate_k = max(40, top_k * VECTOR_CANDIDATE_MULTIPLIER)
            distances = (
                await vector_candidates(session, work_ids, query_vector, k=candidate_k)
                or None
            )
        except Exception as exc:
            logger.warning("向量索引查询失败，回退全量检索: {}", exc)
            distances = None
    if distances is not None:
        rows = (
            await session.execute(
                select(KnowledgeChunk, Work)
                .join(Work, Work.id == KnowledgeChunk.work_id)
                .where(
                    KnowledgeChunk.id.in_(distances),
                    KnowledgeChunk.source_kind.in_(RETRIEVABLE_SOURCE_KINDS),
                )
            )
        ).all()
    else:
        rows = (
            await session.execute(
                select(KnowledgeChunk, Work)
                .join(Work, Work.id == KnowledgeChunk.work_id)
                .where(
                    Work.library_state == "in_library",
                    Work.processing_state == "processed",
                    Work.evidence_state.in_({"sufficient", "unverified"}),
                    KnowledgeChunk.source_kind.in_(RETRIEVABLE_SOURCE_KINDS),
                    Work.id.in_(grounded_work_ids),
                )
            )
        ).all()
    if not rows:
        return []
    scored = await asyncio.to_thread(
        _score_chunks, question, query_vector, list(rows), distances
    )
    grouped: dict[int, dict] = {}
    for score, semantic, lexical, chunk, work in scored:
        semantic_score = score - min(2.0, lexical) * 0.6 if semantic else -1.0
        if not (lexical > 0 or (semantic and semantic_score >= 0.35)):
            continue
        item = grouped.get(work.id)
        if not item:
            item = {
                "score": round(score, 4),
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
    results = sorted(grouped.values(), key=lambda item: item["score"], reverse=True)[
        :top_k
    ]
    for item in results:
        item["text"] = "\n\n".join(item.pop("texts"))
    return results
