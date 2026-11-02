"""Regression checks use isolated databases and fake bridge clients."""
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bridge_feishu_obsidian as bridge
from app.models import Base, ImportBatch, ImportItem, Job
from app.routers_v2 import imports, jobs
from app.schemas import ImportItemsRetryRequest


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'regression.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as value:
        yield value
    await engine.dispose()


async def preview(session, state="cancelled"):
    job = Job(id="job", job_type="link_preview", state="succeeded")
    session.add(job)
    await session.flush()
    batch = ImportBatch(id="batch", job_id=job.id, raw_input="url", source_type="link",
                        total_items=1, state="cancelled", cancel_requested=True)
    session.add(batch)
    await session.flush()
    item = ImportItem(batch_id=batch.id, ordinal=0, input_url="url", normalized_url="url",
                      status=state)
    session.add(item)
    await session.commit()
    return job, batch, item


@pytest.mark.asyncio
async def test_clear_history_preserves_preview_and_can_repeat(session):
    job, batch, item = await preview(session, "ready")
    assert await jobs.clear_completed_jobs(session) == {"deleted": 1}
    assert await session.get(ImportItem, item.id) is not None
    assert await session.get(ImportBatch, batch.id) is not None
    assert await jobs.list_jobs(session) == []
    assert (await jobs.get_job(job.id, session))["id"] == job.id
    assert await jobs.clear_completed_jobs(session) == {"deleted": 0}


@pytest.mark.asyncio
async def test_retry_reopens_cancelled_batch_and_enqueues(session, monkeypatch):
    job, batch, item = await preview(session)
    enqueued = []

    async def enqueue(ids):
        enqueued.extend(ids)

    monkeypatch.setattr(imports.coordinator, "enqueue", enqueue)
    result = await imports.retry_import_items(ImportItemsRetryRequest(item_ids=[item.id]), session)
    assert result["retried"] == enqueued == [item.id]
    assert item.status == "queued"
    assert not batch.cancel_requested and not job.cancel_requested
    assert batch.state == job.state == "running"


def test_duplicate_preview_finishes_without_sleep(monkeypatch):
    monkeypatch.setattr(bridge.time, "sleep", lambda _: pytest.fail("duplicate must be terminal"))
    client = SimpleNamespace(get_batch=lambda _: {"items": [{"status": "duplicate"}]})
    assert bridge.wait_precheck(client, "b") == [{"status": "duplicate"}]


@pytest.mark.parametrize("state,expected", [("processed", []), ("discovered", [[42]]),
                                         ("processing", [])])
def test_duplicate_reuses_work_and_submits_only_unfinished(monkeypatch, state, expected):
    monkeypatch.setattr(bridge, "FS_NO_DOUYIN", False)
    monkeypatch.setattr(bridge, "FS_AUTO_CONFIRM", True)
    monkeypatch.setattr(bridge, "work_processing_state", lambda _: state)
    submitted = []
    client = SimpleNamespace(
        import_link=lambda _: "b",
        get_batch=lambda _: {"items": [{"status": "duplicate", "existing_work_id": 42}]},
        ingest=lambda ids: submitted.append(ids),
    )
    assert bridge.process_link(client, "url", "") == [42]
    assert submitted == expected


def test_supplement_preserves_existing_work_id(tmp_path, monkeypatch):
    from app.services.dispatch_store import DispatchStore
    store = DispatchStore(tmp_path / "dispatch.db")
    store.enqueue("r", "url", "", "", "")
    store.update("r", work_id=42)

    def missing(*args):
        raise bridge.NeedsSupplement("missing media")

    monkeypatch.setattr(bridge, "process_link", missing)
    monkeypatch.setattr(bridge, "mark_note_status", lambda *args: None)
    bridge.process_queue(None, store)
    assert store.get("r")["work_id"] == 42
    assert store.get("r")["status"] == "needs_supplement"


def test_cycle_defers_processing_and_writes_back(tmp_path, monkeypatch):
    from app.services.dispatch_store import DispatchStore
    store = DispatchStore(tmp_path / "dispatch.db")
    for rid in ("r1", "r2"):
        store.enqueue(rid, "url", "", "", "")
    monkeypatch.setattr(bridge, "process_link", lambda *args: [42])
    monkeypatch.setattr(bridge, "work_processing_state", lambda _: "processing")
    writes = []
    monkeypatch.setattr(bridge, "writeback_feishu_status", lambda *args: writes.append(True))
    bridge.process_queue(None, store, {})
    assert store.get("r1")["status"] == "ingested"
    assert store.get("r1")["work_id"] == 42
    assert store.get("r2")["status"] == "pending"
    assert writes == [True]


def test_preview_pending_reuses_saved_batch(tmp_path, monkeypatch):
    from app.services.dispatch_store import DispatchStore
    store = DispatchStore(tmp_path / "dispatch.db")
    store.enqueue("r", "url", "", "", "")
    store.update("r", batch_id="original", error_code="preview_pending")
    seen = []

    def pending(*args, batch_id=None):
        seen.append(batch_id)
        raise bridge.AwaitingPreview(batch_id)

    monkeypatch.setattr(bridge, "process_link", pending)
    bridge.process_queue(None, store)
    assert seen == ["original"]
    assert store.get("r")["batch_id"] == "original"
    assert store.get("r")["status"] == "queued"


def test_completed_metadata_work_exports_full_body(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_METADATA_ONLY_WORK_IDS", {42})
    monkeypatch.setattr(bridge, "work_processing_state", lambda _: "processed")

    def full_manifest(_):
        raise RuntimeError("full export reached")

    client = SimpleNamespace(obsidian_manifest=full_manifest)
    with pytest.raises(RuntimeError, match="full export reached"):
        bridge.backfill_note(client, 42, {"note_path": str(tmp_path / "note.md")})
    assert 42 not in bridge._METADATA_ONLY_WORK_IDS


def test_configured_interpreter_overrides_project_environment(monkeypatch):
    from tokbrain_control import bridge as control
    monkeypatch.setenv("TOKBRAIN_BRIDGE_PYTHON", "custom-python")
    assert control._python() == "custom-python"
