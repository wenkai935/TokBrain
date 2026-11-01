"""飞书同步桥控制面的单测。

重点钉住三件容易出错的事：
1. 重投必须分批（历史 120 条 429 就是一次性放开造成的）；
2. 队列读写只认 ``dispatch`` 表，状态语义要与 bridge 的终态定义一致；
3. 进程识别只认命令行里带 ``bridge_feishu_obsidian.py`` 的 python，
   不能变成「杀掉所有 python」。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tokbrain_control import bridge
from tokbrain_control.errors import ControlError

SCHEMA = """
CREATE TABLE dispatch (
    record_id TEXT PRIMARY KEY, link TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', local_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, work_id INTEGER, note_path TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'feishu', normalized_key TEXT NOT NULL DEFAULT '',
    lid TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


@pytest.fixture
def queue_db(tmp_path, monkeypatch):
    path = tmp_path / "dispatch.db"
    con = sqlite3.connect(str(path))
    con.execute(SCHEMA)
    rows = [
        ("r1", "https://a/1", "failed", "429 Too Many Requests"),
        ("r2", "https://a/2", "failed", "429 Too Many Requests"),
        ("r3", "https://a/3", "failed", "502 Bad Gateway"),
        ("r4", "https://a/4", "processed", ""),
        ("r5", "https://a/5", "pending", ""),
    ]
    for rid, link, status, err in rows:
        con.execute(
            "INSERT INTO dispatch (record_id, link, status, error_code, error_message,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (rid, link, status, "exception" if err else "", err, "2026-09-06 10:00:00",
             "2026-09-06 10:00:00"),
        )
    con.commit()
    con.close()
    monkeypatch.setattr(bridge, "DISPATCH_DB", path)
    return path


def _statuses(path):
    con = sqlite3.connect(str(path))
    try:
        return dict(con.execute("SELECT record_id, status FROM dispatch").fetchall())
    finally:
        con.close()


# ------------------------------------------------------------------ 队列


def test_queue_counts(queue_db):
    data = bridge.queue_counts()
    assert data["available"] is True
    assert data["total"] == 5
    assert data["failed"] == 3
    assert data["done"] == 1
    assert data["in_flight"] == 1  # 只有 pending 一条


def test_queue_counts_without_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "DISPATCH_DB", tmp_path / "nope.db")
    data = bridge.queue_counts()
    assert data["available"] is False
    assert "不存在" in data["reason"]


def test_recent_failures_ordered_and_truncated(queue_db):
    items = bridge.recent_failures(2)
    assert len(items) == 2
    assert all(item["error_code"] == "exception" for item in items)


# ------------------------------------------------------------------ 重投


def test_replay_moves_failed_to_queued(queue_db):
    before = _statuses(queue_db)
    assert sum(1 for v in before.values() if v == "failed") == 3

    data = bridge.replay(2)
    assert data["requeued"] == 2
    assert data["failed_remaining"] == 1

    after = _statuses(queue_db)
    assert sum(1 for v in after.values() if v == "queued") == 2
    assert sum(1 for v in after.values() if v == "failed") == 1
    # processed / pending 不该被动
    assert after["r4"] == "processed"
    assert after["r5"] == "pending"


def test_replay_clears_error_fields(queue_db):
    bridge.replay(1)
    con = sqlite3.connect(str(queue_db))
    try:
        row = con.execute(
            "SELECT error_code, error_message FROM dispatch WHERE status='queued'"
        ).fetchone()
    finally:
        con.close()
    assert row == ("", "")


def test_replay_by_record_ids_touches_only_named_rows(queue_db):
    """用户指着飞书表某几行说「补齐这几个」时，不能按数量挑到无关的旧链接。"""
    data = bridge.replay(record_ids=["r2", "r3"])

    assert data["requeued"] == 2
    assert data["record_ids"] == ["r2", "r3"]
    assert data["order"] == "requested"
    after = _statuses(queue_db)
    assert after["r1"] == "failed"  # 没点名，保持原样
    assert after["r2"] == "queued"
    assert after["r3"] == "queued"


def test_replay_by_record_ids_reports_skipped(queue_db):
    """点名了但当前不是 failed 的，要如实回报而不是静默吞掉。"""
    data = bridge.replay(record_ids=["r4", "r9"])

    assert data["requeued"] == 0
    assert sorted(data["skipped"]) == ["r4", "r9"]
    assert "skipped_hint" in data
    assert _statuses(queue_db)["r4"] == "processed"


def test_replay_by_record_ids_rejects_blank(queue_db):
    with pytest.raises(ControlError):
        bridge.replay(record_ids=["", "  "])


def test_replay_rejects_zero(queue_db):
    with pytest.raises(ControlError) as info:
        bridge.replay(0)
    assert info.value.code == "invalid_argument"


def test_replay_rejects_over_cap(queue_db):
    with pytest.raises(ControlError) as info:
        bridge.replay(bridge.MAX_REPLAY_PER_CALL + 1)
    assert "最多重投" in info.value.message
    assert "分批" in (info.value.hint or "")


def test_replay_rejects_non_integer(queue_db):
    with pytest.raises(ControlError) as info:
        bridge.replay("abc")
    assert info.value.code == "invalid_argument"


def test_replay_warns_when_bridge_down(queue_db, monkeypatch):
    monkeypatch.setattr(bridge, "processes", lambda: {"count": 0, "items": []})
    data = bridge.replay(1)
    assert data["bridge_running"] is False
    assert "不会被处理" in data["note"]


def test_replay_next_step_when_leftover(queue_db, monkeypatch):
    monkeypatch.setattr(bridge, "processes", lambda: {"count": 1, "items": [{"pid": 1}]})
    data = bridge.replay(1)
    assert "还剩 2 条" in data["next_step"]


def test_replay_without_db(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "DISPATCH_DB", tmp_path / "nope.db")
    with pytest.raises(ControlError) as info:
        bridge.replay(1)
    assert info.value.code == "queue_missing"


# ------------------------------------------------------------------ 进程识别


def _fake_cim(monkeypatch, payload):
    text = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(bridge, "_run", lambda *a, **k: (0, text))


def test_processes_filters_by_script_name(monkeypatch):
    _fake_cim(
        monkeypatch,
        [
            {
                "ProcessId": 111,
                "CommandLine": r'python.exe -u bridge_feishu_obsidian.py --watch',
                "CreationDate": "20261101220000",
            },
            {
                "ProcessId": 222,
                "CommandLine": r'python.exe -m uvicorn app.main:app',
                "CreationDate": "20261101220000",
            },
        ],
    )
    data = bridge.processes()
    assert data["count"] == 1
    assert data["items"][0]["pid"] == 111
    assert data["items"][0]["mode"] == "watch"


def test_processes_handles_single_object(monkeypatch):
    # ConvertTo-Json 对单条结果输出对象而不是数组
    _fake_cim(
        monkeypatch,
        {"ProcessId": 333, "CommandLine": "python bridge_feishu_obsidian.py", "CreationDate": "x"},
    )
    data = bridge.processes()
    assert data["count"] == 1
    assert data["items"][0]["mode"] == "once"


def test_processes_empty_and_broken(monkeypatch):
    monkeypatch.setattr(bridge, "_run", lambda *a, **k: (0, ""))
    assert bridge.processes()["count"] == 0

    monkeypatch.setattr(bridge, "_run", lambda *a, **k: (0, "not json"))
    data = bridge.processes()
    assert data["count"] == 0
    assert "解析失败" in data["error"]


# ------------------------------------------------------------------ 状态与配置


def test_table_config_defaults(monkeypatch):
    monkeypatch.delenv("FEISHU_BITABLE_APP_TOKEN", raising=False)
    cfg = bridge.table_config()
    assert cfg["app_token"] == bridge.DEFAULT_APP_TOKEN
    assert cfg["table_id"] == bridge.DEFAULT_TABLE_ID
    assert "内置默认" in cfg["source"]


def test_table_config_env_override(monkeypatch):
    monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "custom")
    cfg = bridge.table_config()
    assert cfg["app_token"] == "custom"
    assert cfg["source"] == "环境变量"


def test_status_reports_hints(queue_db, monkeypatch):
    monkeypatch.setattr(bridge, "processes", lambda: {"count": 0, "items": []})
    data = bridge.status(include_tail=False)
    assert data["running"] is False
    joined = " ".join(data["hints"])
    assert "没在跑" in joined
    assert "3 条 failed" in joined


def test_start_returns_already_running(monkeypatch):
    monkeypatch.setattr(
        bridge, "processes", lambda: {"count": 1, "items": [{"pid": 9, "mode": "watch"}]}
    )
    data = bridge.start()
    assert data["started"] is False
    assert data["already_running"] is True


# ------------------------------------------------------- 代理绕过（实测踩过）


def test_no_proxy_bypass_from_empty():
    assert bridge._with_localhost_bypass("") == "127.0.0.1,localhost,::1"


def test_no_proxy_bypass_keeps_existing():
    # 不能覆盖用户原有条目，否则会把本该走代理的域名也绕过去
    result = bridge._with_localhost_bypass("example.com, 127.0.0.1")
    assert result.startswith("example.com")
    assert "127.0.0.1" in result
    assert result.count("127.0.0.1") == 1
    assert "localhost" in result


# ------------------------------------------------------------------ 等待与模式


def test_wait_idle_returns_immediately_when_nothing_queued(queue_db, monkeypatch):
    bridge.replay(3)  # 全改成 queued
    # 把非终态全部抹成 processed，模拟队列已经空闲
    con = sqlite3.connect(str(queue_db))
    con.execute(
        "UPDATE dispatch SET status='processed' WHERE status IN ('queued','pending','processing')"
    )
    con.commit()
    con.close()

    data = bridge.wait_idle(timeout=2, poll=1)
    assert data["idle"] is True
    assert data["elapsed"] < 2


def test_wait_idle_times_out_when_busy(queue_db):
    # r5 是 pending，永远算 in_flight
    data = bridge.wait_idle(timeout=0.6, poll=0.2)
    assert data["idle"] is False
    assert data["in_flight"] == 1
    assert "再调一次" in data["next_step"]


def test_wait_idle_reports_processed_delta(queue_db, monkeypatch):
    calls = {"n": 0}
    real = bridge.queue_counts

    def fake():
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "available": True,
                "by_state": {"queued": 1, "failed": 2, "processed": 1},
                "in_flight": 1,
                "failed": 2,
            }
        return {
            "available": True,
            "by_state": {"failed": 2, "processed": 2},
            "in_flight": 0,
            "failed": 2,
        }

    monkeypatch.setattr(bridge, "queue_counts", fake)
    monkeypatch.setattr(bridge, "recent_done", lambda n=3: [{"work_id": 7}])
    data = bridge.wait_idle(timeout=2, poll=0.1)
    assert data["idle"] is True
    assert data["delta"]["processed"] == 1
    assert "新增 1 条入库" in data["note"]
    assert bridge.queue_counts is fake and real is not None


def test_detect_mode_metadata_only(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_log_activity",
        lambda: {"tail": ["[slow] 待处理 1 条", "[import-link:metadata-only] https://a/1"]},
    )
    assert bridge._detect_mode()["mode"] == "metadata_only"


def test_detect_mode_download(monkeypatch):
    monkeypatch.setattr(
        bridge, "_log_activity", lambda: {"tail": ["[import-link] https://a/1", "[ok]"]}
    )
    data = bridge._detect_mode()
    assert data["mode"] == "download"
    assert "下载" in data["detail"]


def test_detect_mode_unknown(monkeypatch):
    monkeypatch.setattr(bridge, "_log_activity", lambda: {"tail": ["[watch] 启动"]})
    assert bridge._detect_mode()["mode"] == "unknown"


def test_replay_warns_when_metadata_only(queue_db, monkeypatch):
    monkeypatch.setattr(bridge, "processes", lambda: {"count": 1, "items": [{"pid": 1}]})
    monkeypatch.setattr(bridge, "_detect_mode", lambda: {"mode": "metadata_only"})
    data = bridge.replay(1)
    assert "不会下载视频" in data["warning"]
    assert "download=true" in data["warning"]


def test_recent_done_carries_work_id(queue_db):
    con = sqlite3.connect(str(queue_db))
    con.execute("UPDATE dispatch SET work_id=42 WHERE record_id='r4'")
    con.commit()
    con.close()
    items = bridge.recent_done(3)
    assert items and items[0]["work_id"] == 42
