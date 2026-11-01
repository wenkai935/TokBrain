"""平台采集批次的条目状态必须与预检 worker 的取件条件一致。

历史缺陷：``create_platform_import_batch`` 把条目写成 ``pending``，而预检 worker
只处理 ``status == "queued"``，于是批次显示"已完成"、条目永远停在"解析中"，
confirm 也选不中（只收 ready / needs_local_file）。这里把三条约束钉住：

1. 有解析器的平台（抖音）→ 条目 queued、批次 queued；
2. 没有解析器的平台（B 站/小红书）→ 条目 needs_local_file、批次 succeeded；
3. 无资产的 platform_pull 条目在确认时能建作品（媒体缺失留给 ingest 阶段补件）。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, ImportBatch, ImportItem, Job, Work
from app.services import import_integrations
from app.services.import_integrations import (
    create_platform_import_batch,
    enqueue_platform_batch,
)
from app.services.import_queue import confirm_import_items


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory()


def _items(platform: str, count: int = 2) -> list[dict]:
    return [
        {
            "platform_work_id": f"7{index:018d}",
            "source_url": f"https://www.{platform}.com/video/7{index:018d}",
            "title": f"示例视频 {index}",
            "author_name": "某作者",
            "duration_seconds": 42,
        }
        for index in range(1, count + 1)
    ]


async def test_douyin_items_enter_preview_queue():
    engine, session = await _session()

    view = await create_platform_import_batch(
        session, platform="douyin", items=_items("douyin")
    )

    batch = await session.get(ImportBatch, view["id"])
    job = await session.get(Job, batch.job_id)
    statuses = (
        (await session.execute(select(ImportItem.status).where(ImportItem.batch_id == batch.id)))
        .scalars()
        .all()
    )

    assert set(statuses) == {"queued"}, "抖音条目必须进预检队列"
    assert batch.state == "queued", "批次不能一建好就标成已完成"
    assert job.state == "queued"
    assert job.job_type == "link_preview"
    assert job.progress["remaining"] == 2
    assert job.progress["needs_local_file"] == 0

    await session.close()
    await engine.dispose()


async def test_platform_without_resolver_marks_needs_local_file():
    engine, session = await _session()

    view = await create_platform_import_batch(
        session, platform="bilibili", items=_items("bilibili")
    )

    batch = await session.get(ImportBatch, view["id"])
    job = await session.get(Job, batch.job_id)
    statuses = (
        (await session.execute(select(ImportItem.status).where(ImportItem.batch_id == batch.id)))
        .scalars()
        .all()
    )

    assert set(statuses) == {"needs_local_file"}, "没有解析器的平台不该排队等解析"
    assert batch.state == "succeeded"
    assert job.progress["needs_local_file"] == 2
    assert job.progress.get("remaining", 0) == 0

    await session.close()
    await engine.dispose()


async def test_enqueue_only_takes_queued_items(monkeypatch):
    engine, session = await _session()
    pushed: list[list[int]] = []

    async def fake_enqueue(item_ids):
        pushed.append(list(item_ids))

    monkeypatch.setattr(import_integrations, "RESOLVABLE_PLATFORMS", frozenset())
    # 混入一条 queued 看是否被挑出来：直接把平台先按"无解析器"建，再手工改一条
    view = await create_platform_import_batch(
        session, platform="bilibili", items=_items("bilibili")
    )
    batch_id = view["id"]
    first_item = (
        await session.execute(
            select(ImportItem).where(ImportItem.batch_id == batch_id).limit(1)
        )
    ).scalar_one()
    first_item.status = "queued"
    await session.commit()

    from app.services import import_queue

    monkeypatch.setattr(import_queue.coordinator, "enqueue", fake_enqueue)
    count = await enqueue_platform_batch(session, batch_id)

    assert count == 1
    assert pushed == [[first_item.id]], "只有 queued 的条目能进队列"

    await session.close()
    await engine.dispose()


async def test_confirm_accepts_assetless_platform_item():
    engine, session = await _session()

    view = await create_platform_import_batch(
        session, platform="bilibili", items=_items("bilibili", count=1)
    )
    batch_id = view["id"]
    item = (
        await session.execute(select(ImportItem).where(ImportItem.batch_id == batch_id))
    ).scalar_one()

    result = await confirm_import_items(session, batch_id, [item.id])

    assert result["confirmed_count"] == 1
    work = await session.get(Work, result["work_ids"][0])
    assert work.platform == "bilibili"
    assert work.import_source == "platform_pull"
    assert work.library_state == "pending"

    refreshed = await session.get(ImportItem, item.id)
    assert refreshed.status == "confirmed"
    assert refreshed.existing_work_id == work.id

    await session.close()
    await engine.dispose()


async def test_confirm_still_rejects_assetless_external_item():
    """放宽只针对 platform_pull；external_batch 仍然必须有视频资产。"""
    engine, session = await _session()

    batch = ImportBatch(
        id="batch-external",
        job_id="job-external",
        source_type="external_batch",
        raw_input="{}",
        state="succeeded",
        total_items=1,
    )
    session.add(Job(id="job-external", job_type="external_import", state="succeeded"))
    await session.flush()
    session.add(batch)
    await session.flush()
    item = ImportItem(
        batch_id=batch.id,
        ordinal=1,
        platform="douyin",
        client_item_id="ext-1",
        input_url="https://www.douyin.com/video/7012345678901234567",
        normalized_url="https://www.douyin.com/video/7012345678901234567",
        platform_work_id="7012345678901234567",
        status="needs_local_file",
    )
    session.add(item)
    await session.commit()

    with pytest.raises(ValueError):
        await confirm_import_items(session, batch.id, [item.id])

    refreshed = await session.get(ImportItem, item.id)
    assert refreshed.status == "needs_local_file"

    await session.close()
    await engine.dispose()
