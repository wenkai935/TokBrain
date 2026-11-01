"""「预检完成、等确认」这条支线的单测。

设计意图：手机往飞书丢链接是高频动作，桥只负责预检（f2 补标题/作者/封面），
**不自动解析**——停在这里等网页或 agent 放行。这条链最容易错的三处：

1. awaiting_confirm 必须是终态，否则慢轨每轮重新导入同一行；
2. 老数据库没有 batch_id 列时不能整个工具崩掉；
3. confirm_pending 只能动 awaiting_confirm 的行，别把两个月前的失败条目拉起来跑。
"""

from __future__ import annotations

import sqlite3

import pytest

from tokbrain_control import bridge
from tokbrain_control.errors import ControlError

SCHEMA_V2 = """
CREATE TABLE dispatch (
    record_id TEXT PRIMARY KEY, link TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', local_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, work_id INTEGER, note_path TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'feishu', normalized_key TEXT NOT NULL DEFAULT '',
    lid TEXT NOT NULL DEFAULT '', batch_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
"""

# 9-06 那批旧库：没有 batch_id 列
SCHEMA_V1 = """
CREATE TABLE dispatch (
    record_id TEXT PRIMARY KEY, link TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', local_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, work_id INTEGER, note_path TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
"""

ROWS = [
    ("r-wait1", "https://v.douyin.com/aaa/", "awaiting_confirm", "batch-a"),
    ("r-wait2", "https://v.douyin.com/bbb/", "awaiting_confirm", "batch-b"),
    ("r-fail", "https://v.douyin.com/ccc/", "failed", ""),
    ("r-done", "https://v.douyin.com/ddd/", "processed", "batch-c"),
]


def _make_db(path, schema, with_batch: bool):
    con = sqlite3.connect(str(path))
    con.execute(schema)
    for rid, link, status, batch in ROWS:
        if with_batch:
            con.execute(
                "INSERT INTO dispatch (record_id, link, status, batch_id, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (rid, link, status, batch, "2026-11-01 20:00:00", "2026-11-01 20:00:00"),
            )
        else:
            con.execute(
                "INSERT INTO dispatch (record_id, link, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (rid, link, status, "2026-11-01 20:00:00", "2026-11-01 20:00:00"),
            )
    con.commit()
    con.close()


@pytest.fixture
def queue_v2(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.db"
    _make_db(path, SCHEMA_V2, with_batch=True)
    monkeypatch.setattr(bridge, "DISPATCH_DB", path)
    return path


@pytest.fixture
def queue_v1(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.db"
    _make_db(path, SCHEMA_V1, with_batch=False)
    monkeypatch.setattr(bridge, "DISPATCH_DB", path)
    return path


def test_awaiting_confirm_is_not_counted_as_in_flight(queue_v2):
    data = bridge.queue_counts()
    assert data["awaiting_confirm"] == 2
    assert data["in_flight"] == 0, "待确认的球在用户那边，不是在后台跑"
    assert data["failed"] == 1
    assert data["done"] == 1


def test_pending_confirm_lists_only_awaiting_rows(queue_v2):
    data = bridge.pending_confirm(10)
    assert data["available"] is True
    assert data["count"] == 2
    ids = {entry["record_id"] for entry in data["items"]}
    assert ids == {"r-wait1", "r-wait2"}
    assert all(entry["batch_id"] for entry in data["items"])


def test_pending_confirm_survives_legacy_schema(queue_v1):
    """9-06 那版库没有 batch_id 列：要退化显示，而不是抛 OperationalError。"""
    data = bridge.pending_confirm(10)
    assert data["available"] is True
    assert data["count"] == 2
    assert all(entry["batch_id"] == "" for entry in data["items"])


def test_confirm_pending_rejects_non_awaiting_rows(queue_v2):
    """failed / processed 的行一律跳过：不能因为传了个 id 就把旧失败条目重新拉起来。"""
    data = bridge.confirm_pending(["r-fail", "r-done"])
    assert data["count"] == 0
    assert {item["record_id"] for item in data["skipped"]} == {"r-fail", "r-done"}
    assert data["confirmed"] == []


def test_confirm_pending_requires_known_record(queue_v2):
    data = bridge.confirm_pending(["no-such-record"])
    assert data["count"] == 0
    assert data["failed"][0]["record_id"] == "no-such-record"


def test_confirm_pending_reports_missing_batch_id(queue_v1):
    """老库里没有 batch_id，确认时要说清「重新投一次让桥补上」，不能静默跳过。"""
    data = bridge.confirm_pending(["r-wait1"])
    assert data["count"] == 0
    assert "batch_id" in data["failed"][0]["reason"]


def test_confirm_pending_requires_record_ids(queue_v2):
    with pytest.raises(ControlError) as info:
        bridge.confirm_pending([])
    assert info.value.code == "invalid_argument"
