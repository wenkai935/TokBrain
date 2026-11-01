"""KNN retrieval path: sqlite-vec pre-filter + lexical fusion, plus fallbacks.

These tests exercise ``search()`` with the vec0 index populated through the
real ``ensure_vec0`` / ``replace_chunk_vectors`` helpers, then verify ranking,
fused lexical behaviour and the graceful fallback when vec0 is unavailable.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.knowledge as knowledge
import app.services.vector_store as vector_store
from app.models import Base, KnowledgeChunk, Work
from app.services.budget import Reservation
from app.services.knowledge import search
from app.services.providers import ProviderUsage


def vec(index: int) -> list[float]:
    vector = [0.0] * 1024
    vector[index] = 1.0
    return vector


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


def install_fake_provider(monkeypatch, query_vector):
    class FakeProvider:
        def __init__(self, _api_key, _profile=None):
            pass

        async def embed(self, _texts):
            return [query_vector], ProviderUsage("text-embedding-v4", 1, 0, 0)

    async def fake_secret(*_args):
        return "test-key"

    async def fake_reserve(*_args, **_kwargs):
        # release() reads .day/.works/.media_minutes/.llm_tokens off the
        # reservation, so the fallback path needs a real Reservation.
        return Reservation(works=0, media_minutes=0.0, llm_tokens=0)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge, "get_secret", fake_secret)
    monkeypatch.setattr(knowledge, "reserve", fake_reserve)
    monkeypatch.setattr(knowledge, "record_usage", no_op)
    monkeypatch.setattr(knowledge, "consume", no_op)
    monkeypatch.setattr(
        knowledge, "_build_provider", lambda key, profile=None: FakeProvider(key, profile)
    )
    return FakeProvider


async def _seed(works_and_vectors, session, *, index: bool = True):
    """Insert works/chunks with embeddings and optionally index them into chunk_vec."""
    works = []
    for title, text, embedding in works_and_vectors:
        work = Work(
            platform_work_id=f"work-{title}",
            title=title,
            source_url=f"https://www.douyin.com/video/{title}",
            library_state="in_library",
            processing_state="processed",
            evidence_state="sufficient",
        )
        session.add(work)
        await session.flush()
        chunk = KnowledgeChunk(
            work_id=work.id,
            chunk_index=0,
            source_kind="transcript",
            text=text,
            embedding=embedding,
        )
        session.add(chunk)
        await session.flush()
        works.append((work, chunk))
    await session.commit()
    if index:
        assert await vector_store.ensure_vec0(session)
        await vector_store.replace_chunk_vectors(
            session,
            [(chunk.id, work.id, chunk.embedding) for work, chunk in works],
        )
        await session.commit()
    return works


async def test_knn_retrieval_ranks_semantic_nearest_first(monkeypatch, session):
    install_fake_provider(monkeypatch, query_vector=vec(0))
    # B/C embeddings have cosine 0.9 / 0.8 with the query (above the 0.35
    # threshold) so all three works pass the semantic filter and ordering is
    # observable.
    await _seed(
        [
            ("咖啡教程", "手冲咖啡建议使用九十二度热水", vec(0)),
            ("心理学沟通", "沟通中需要关注情绪和关系", [0.9, 0.43589] + [0.0] * 1022),
            ("定积分", "区间减半公式可以缩短计算区间", [0.8, 0.6] + [0.0] * 1022),
        ],
        session,
    )
    results = await search(session, "咖啡", top_k=3)
    assert results[0]["title"] == "咖啡教程"
    assert results[0]["external_url"].endswith("/video/咖啡教程")
    assert "九十二度" in results[0]["text"]
    assert len(results) == 3
    assert all({"score", "text", "source_kind", "work_id", "title"} <= set(item) for item in results)


async def test_knn_lexical_fusion_keeps_phrase_match_above_far_vector(monkeypatch, session):
    # The query embedding is far from every chunk, but the exact Chinese phrase
    # in work B must still surface through lexical fusion.
    install_fake_provider(monkeypatch, query_vector=vec(500))
    await _seed(
        [
            ("无关视频", "完全无关的科技内容", vec(0)),
            ("咖啡教程", "手冲咖啡建议使用九十二度热水", vec(10)),
        ],
        session,
    )
    results = await search(session, "咖啡热水", top_k=2)
    assert [item["title"] for item in results] == ["咖啡教程"]


async def test_search_falls_back_when_vec0_unavailable(monkeypatch, session):
    install_fake_provider(monkeypatch, query_vector=vec(0))
    monkeypatch.setattr(vector_store, "vec0_path", lambda: None)
    await _seed(
        [
            ("咖啡教程", "手冲咖啡建议使用九十二度热水", vec(0)),
            ("无关视频", "完全无关的科技内容", vec(10)),
        ],
        session,
        index=False,
    )
    # vec0 is disabled, so retrieval must fall back to the full-scan lexical path.
    results = await search(session, "咖啡热水", top_k=2)
    assert [item["title"] for item in results] == ["咖啡教程"]
