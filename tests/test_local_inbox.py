"""P4 inbox API tests: /api/integrations/v1/inbox/items.

Covers TC-P4-01 .. TC-P4-12 from docs/test-cases-p3-p5.md:
  - normal single submit, batch <=100, >100 atomic reject, URL normalization
    dedup, Idempotency-Key replay, rights_attested mandatory, illegal URL partial
    reject, adapter slot, auth (401/503), field validation, concurrent dedup,
    and the double-track queue entry.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bridge_feishu_obsidian as bridge
from app.database import get_db
from app.main import create_application
from app.models import Base
from app.services.dispatch_store import DispatchStore


def _douyin_items(n: int) -> list[dict]:
    return [
        {"link": f"https://v.douyin.com/item{i}/", "tags": f"tag{i}"}
        for i in range(n)
    ]


@pytest.fixture
def dispatch_path(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.db"
    monkeypatch.setenv("DISPATCH_DB", str(path))
    return path


@pytest.fixture
async def client_and_token(dispatch_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with factory() as session:
            yield session

    application = create_application()
    application.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        generated = (await client.post("/api/settings/integration-token")).json()
        yield client, generated["token"]
    await engine.dispose()


def _store(dispatch_path) -> DispatchStore:
    return DispatchStore(path=dispatch_path)


def _row_count(store: DispatchStore) -> int:
    return len(store.pending_items())


# --- TC-P4-01: single valid submit lands in dispatch queue ---
async def test_p4_single_submit_enqueues(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-01"},
        json={
            "rights_attested": True,
            "items": [{"link": "https://v.douyin.com/abc123/"}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 0
    assert body["items"][0]["status"] == "pending"
    rid = body["items"][0]["record_id"]
    assert rid.startswith("inbox-")

    store = _store(dispatch_path)
    row = store.get(rid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["source"] == "douyin_share"
    assert _row_count(store) == 1


# --- TC-P4-02: batch of exactly 100 is accepted ---
async def test_p4_batch_of_100(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-02"},
        json={"rights_attested": True, "items": _douyin_items(100)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["accepted"] == 100
    assert _row_count(_store(dispatch_path)) == 100


# --- TC-P4-03: 101 rejected atomically, nothing inserted ---
async def test_p4_over_100_rejected_atomically(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-03"},
        json={"rights_attested": True, "items": _douyin_items(101)},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "invalid_item_count"
    assert _row_count(_store(dispatch_path)) == 0


# --- TC-P4-04: URL normalization dedup (query param dropped) ---
async def test_p4_url_normalization_dedup(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-04"},
        json={
            "rights_attested": True,
            "items": [
                {"link": "https://v.douyin.com/abc123/"},
                {"link": "https://v.douyin.com/abc123/?share=1"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    # Two submissions of the same normalized URL -> a single queue row, with
    # the duplicate reported separately (TC-P4-04 "返回去重计数").
    assert _row_count(_store(dispatch_path)) == 1
    assert resp.json()["accepted"] == 1
    assert resp.json()["duplicates"] == 1


# --- TC-P4-05: Idempotency-Key replay returns first result, no re-insert ---
async def test_p4_idempotency_replay(client_and_token, dispatch_path):
    client, token = client_and_token
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-05"}
    first = await client.post(
        "/api/integrations/v1/inbox/items",
        headers=headers,
        json={"rights_attested": True, "items": [{"link": "https://v.douyin.com/replay/"}]},
    )
    assert first.status_code == 201
    assert first.json()["replayed"] is False

    second = await client.post(
        "/api/integrations/v1/inbox/items",
        headers=headers,
        json={"rights_attested": True, "items": [{"link": "https://v.douyin.com/replay/"}]},
    )
    assert second.status_code == 201
    assert second.json()["replayed"] is True
    # No duplicate row from the replay.
    assert _row_count(_store(dispatch_path)) == 1

    # Different items under the same key -> conflict.
    conflict = await client.post(
        "/api/integrations/v1/inbox/items",
        headers=headers,
        json={
            "rights_attested": True,
            "items": [{"link": "https://v.douyin.com/other/"}],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


# --- TC-P4-06: rights_attested missing / false is rejected ---
async def test_p4_rights_attestation_required(client_and_token, dispatch_path):
    client, token = client_and_token
    missing = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-06a"},
        json={"items": [{"link": "https://v.douyin.com/x/"}]},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "rights_attestation_required"
    assert _row_count(_store(dispatch_path)) == 0

    false_flag = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-06b"},
        json={"rights_attested": False, "items": [{"link": "https://v.douyin.com/x/"}]},
    )
    assert false_flag.status_code == 400
    assert _row_count(_store(dispatch_path)) == 0


# --- TC-P4-07: illegal URL partially rejected, legal still ingested ---
async def test_p4_illegal_url_partial_reject(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-07"},
        json={
            "rights_attested": True,
            "items": [
                {"link": "https://v.douyin.com/legal/"},
                {"link": "https://example.com/not-douyin"},
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["accepted"] == 1
    assert detail["rejected"] == 1
    illegal = [r for r in detail["items"] if r["status"] == "rejected"]
    assert illegal and illegal[0]["error"]["code"] == "invalid_link"
    # The legal item was still queued despite the partial rejection.
    assert _row_count(_store(dispatch_path)) == 1

    replay = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-07"},
        json={
            "rights_attested": True,
            "items": [
                {"link": "https://v.douyin.com/legal/"},
                {"link": "https://example.com/not-douyin"},
            ],
        },
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["replayed"] is True
    assert replay.json()["detail"]["rejected"] == 1
    assert _row_count(_store(dispatch_path)) == 1


# --- TC-P4-08: adapter slot (source) — local_text accepted, unknown rejected ---
async def test_p4_adapter_slot(client_and_token, dispatch_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-08"},
        json={
            "rights_attested": True,
            "items": [
                {"link": "local-ref-001", "source": "local_text"},
                {"link": "https://v.douyin.com/ok/", "source": "douyin_share"},
                {"link": "https://v.douyin.com/x/", "source": "unknown_adapter"},
            ],
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    by_source = {r["source"]: r["status"] for r in detail["items"]}
    assert by_source["local_text"] == "pending"
    assert by_source["douyin_share"] == "pending"
    assert by_source["unknown_adapter"] == "rejected"
    assert detail["accepted"] == 2
    assert detail["rejected"] == 1


# --- TC-P4-09: auth failures (401) and unconfigured token (503) ---
async def test_p4_auth_failures(dispatch_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with factory() as session:
            yield session

    application = create_application()
    application.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        body = {"rights_attested": True, "items": [{"link": "https://v.douyin.com/x/"}]}
        # Token not configured yet -> 503.
        unconfigured = await client.post(
            "/api/integrations/v1/inbox/items",
            headers={"Authorization": "Bearer anything", "Idempotency-Key": "p4-09a"},
            json=body,
        )
        assert unconfigured.status_code == 503
        assert unconfigured.json()["detail"]["code"] == "token_not_configured"

        # Generate the integration token so unauthenticated requests now get a
        # proper 401 (rather than 503 token_not_configured).
        await client.post("/api/settings/integration-token")
        # No Authorization header -> 401.
        no_auth = await client.post(
            "/api/integrations/v1/inbox/items",
            headers={"Idempotency-Key": "p4-09b"},
            json=body,
        )
        assert no_auth.status_code == 401
        assert no_auth.headers.get("www-authenticate") == "Bearer"
        # Wrong token -> 401.
        wrong = await client.post(
            "/api/integrations/v1/inbox/items",
            headers={"Authorization": "Bearer wrong", "Idempotency-Key": "p4-09c"},
            json=body,
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"]["code"] == "invalid_token"
    await engine.dispose()


# --- TC-P4-10: link required (empty -> invalid), tags optional ---
async def test_p4_field_validation(client_and_token, dispatch_path):
    client, token = client_and_token
    # Missing / empty link -> rejected (invalid_link), batch 400, nothing queued.
    empty = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-10a"},
        json={"rights_attested": True, "items": [{"link": ""}]},
    )
    assert empty.status_code == 400, empty.text
    assert empty.json()["detail"]["rejected"] == 1
    assert _row_count(_store(dispatch_path)) == 0

    # Item without tags is accepted (tags default to empty).
    no_tags = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-10b"},
        json={"rights_attested": True, "items": [{"link": "https://v.douyin.com/notags/"}]},
    )
    assert no_tags.status_code == 201
    assert no_tags.json()["accepted"] == 1
    row = _store(dispatch_path).pending_items()[0]
    assert row["tags"] == ""


# --- TC-P4-11: concurrent identical submits -> single row ---
async def test_p4_concurrent_dedup(client_and_token, dispatch_path):
    client, token = client_and_token
    headers = {"Authorization": f"Bearer {token}"}
    body = {"rights_attested": True, "items": [{"link": "https://v.douyin.com/concurrent/"}]}
    results = await asyncio.gather(
        client.post(
            "/api/integrations/v1/inbox/items",
            headers={**headers, "Idempotency-Key": "p4-11a"},
            json=body,
        ),
        client.post(
            "/api/integrations/v1/inbox/items",
            headers={**headers, "Idempotency-Key": "p4-11b"},
            json=body,
        ),
    )
    assert {r.status_code for r in results} == {201}
    assert _row_count(_store(dispatch_path)) == 1


# --- TC-P4-12: inbound data enters the same double-track queue (placeholder note) ---
async def test_p4_double_track_queue_entry(client_and_token, dispatch_path, tmp_path):
    client, token = client_and_token
    resp = await client.post(
        "/api/integrations/v1/inbox/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "p4-12"},
        json={"rights_attested": True, "items": [{"link": "https://v.douyin.com/dt/"}]},
    )
    assert resp.status_code == 201
    rid = resp.json()["items"][0]["record_id"]

    store = _store(dispatch_path)
    row = store.get(rid)
    assert row is not None and row["status"] == "pending"

    # Simulate the bridge fast track: a pending item without a note gets a
    # placeholder Obsidian note, exactly like Feishu rows (TC-P4-12).
    vault = tmp_path / "vault"
    saved_vault = bridge.VAULT_PATH
    bridge.VAULT_PATH = vault
    try:
        assert not row.get("note_path")
        note = bridge.write_placeholder_note(row["record_id"], "", row["link"], row["tags"])
        store.update(row["record_id"], note_path=str(note))
        assert note.exists()
        assert note.read_text(encoding="utf-8").startswith("---")
    finally:
        bridge.VAULT_PATH = saved_vault

    # After the note exists, the slow track (process_queue) would pick it up via
    # pending_items(); verify it still surfaces as a pending, note-backed row.
    updated = store.get(rid)
    assert updated["note_path"] == str(note)
    assert updated["status"] == "pending"
