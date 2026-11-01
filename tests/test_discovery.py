"""飞书发现批次的可见性约束。

用户定的交互（2026-11-02）：bridge 补齐期间前端必须保持静默，补齐全部落终
态后一次性弹出，避免用户看到数字在中途跳动。这里把四条约束钉住：

1. dispatch 里还有非终态条目 → state=filling、清单为空（数字不跳的根）；
2. 全部终态 → 一次性返回可解析项 + 本轮取不到信息的项；
3. 30 分钟以前的历史失败不计入本轮（否则 429 积压会天天报"刚失败 120 条"）；
4. 确认只提交用户选中的条目，其余留在 awaiting_confirm 等下次决定。
"""

import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, ImportItem
from app.services import discovery
from app.services.dispatch_store import DispatchStore
from app.services.import_integrations import create_platform_import_batch


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.db"
    monkeypatch.setattr(discovery, "_store", lambda: DispatchStore(str(path)))
    return DispatchStore(str(path))


async def _ready_batch(session, index: int) -> tuple[str, int]:
    """造一个「预检已补齐」的批次：条目 ready，返回 (batch_id, item_id)。"""
    view = await create_platform_import_batch(
        session,
        platform="douyin",
        items=[
            {
                "platform_work_id": f"7{index:018d}",
                "source_url": f"https://www.douyin.com/video/7{index:018d}",
                "title": f"示例视频 {index}",
                "author_name": "某作者",
            }
        ],
    )
    batch_id = view["id"]
    item = (
        await session.execute(
            select(ImportItem).where(ImportItem.batch_id == batch_id)
        )
    ).scalar_one()
    item.status = "ready"
    await session.commit()
    return batch_id, item.id


def _enqueue(store, record_id: str, batch_id: str, status: str, *, minutes_ago: int = 0) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - minutes_ago * 60))
    con = store._connect()
    try:
        with con:
            con.execute(
                "INSERT INTO dispatch (record_id, link, status, batch_id, lid, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (record_id, f"https://v.douyin.com/{record_id}/", status, batch_id, "lid_x", stamp, stamp),
            )
    finally:
        con.close()


async def test_filling_hides_everything(store):
    engine, session = await _session()
    batch_id, _ = await _ready_batch(session, 1)
    _enqueue(store, "rec-1", batch_id, "awaiting_confirm")
    # 同轮里另一条还在跑 → 这一轮没结束，不能弹
    _enqueue(store, "rec-2", "", "queued")

    view = await discovery.discovery_view(session)

    assert view["state"] == "filling"
    assert view["filling"] == 1
    assert view["items"] == []
    assert view["total"] == 0
    await engine.dispose()


async def test_ready_returns_all_at_once(store):
    engine, session = await _session()
    batch_one, item_one = await _ready_batch(session, 1)
    batch_two, item_two = await _ready_batch(session, 2)
    _enqueue(store, "rec-1", batch_one, "awaiting_confirm")
    _enqueue(store, "rec-2", batch_two, "awaiting_confirm")
    # 预检失败没有 batch_id，只能按链接展示
    _enqueue(store, "rec-3", "", "failed")

    view = await discovery.discovery_view(session)

    assert view["state"] == "ready"
    assert view["total"] == 3
    assert view["resolvable"] == 2
    assert view["unresolved"] == 1
    assert [item["state"] for item in view["items"]] == ["ready", "ready", "unresolved"]
    assert {item["item_id"] for item in view["items"] if item["item_id"]} == {item_one, item_two}
    await engine.dispose()


async def test_old_failures_are_not_counted(store):
    engine, session = await _session()
    _enqueue(store, "rec-old", "", "failed", minutes_ago=120)

    view = await discovery.discovery_view(session)

    assert view["state"] == "idle"
    assert view["total"] == 0
    await engine.dispose()


async def test_approve_only_submits_selected(store):
    engine, session = await _session()
    batch_one, item_one = await _ready_batch(session, 1)
    batch_two, item_two = await _ready_batch(session, 2)
    _enqueue(store, "rec-1", batch_one, "awaiting_confirm")
    _enqueue(store, "rec-2", batch_two, "awaiting_confirm")

    result = await discovery.approve_discovery(session, item_ids=[item_one])

    assert result["confirmed"] == 1
    assert result["remaining"] == 1
    assert store.get("rec-1")["status"] == "ingested"
    assert store.get("rec-1")["work_id"] is not None
    # 没选中的那条必须留在原地，用户下次还能点
    assert store.get("rec-2")["status"] == "awaiting_confirm"
    await engine.dispose()


async def test_rejects_item_outside_discovery(store):
    engine, session = await _session()
    _, item_one = await _ready_batch(session, 1)  # 有批次但没进 awaiting_confirm

    with pytest.raises(ValueError):
        await discovery.approve_discovery(session, item_ids=[item_one])
    await engine.dispose()
