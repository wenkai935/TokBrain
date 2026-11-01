"""服务生命周期判定测试（不碰真实进程）。"""

from __future__ import annotations

import json
import os
import time

import pytest

from tokbrain_control import service
from tokbrain_control.errors import ControlError


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    runtime = tmp_path / "runtime.json"
    monkeypatch.setattr(service, "LOG_DIR", log_dir)
    monkeypatch.setattr(service, "RUNTIME_JSON", runtime)
    monkeypatch.setattr(service, "LOCK_PATH", log_dir / ".control-service.lock")
    return {"log_dir": log_dir, "runtime": runtime}


def test_read_runtime_missing(sandbox):
    assert service.read_runtime() == {"exists": False}


def test_read_runtime_ok(sandbox):
    sandbox["runtime"].write_text(
        json.dumps({"state_version": 2, "backend": {"pid": 123, "port": 8001}}),
        encoding="utf-8",
    )
    info = service.read_runtime()
    assert info["exists"] is True
    assert info["state_version"] == 2
    assert info["backend"]["pid"] == 123
    assert len(info["sha"]) == 16


def test_read_runtime_corrupted(sandbox):
    sandbox["runtime"].write_text("{not json", encoding="utf-8")
    info = service.read_runtime()
    assert info["exists"] is True
    assert "parse_error" in info


def test_tail_log_utf8(sandbox):
    (sandbox["log_dir"] / "startup-console.log").write_text(
        "第一行\n第二行\n", encoding="utf-8"
    )
    assert service.tail_log("startup-console.log", 5) == ["第一行", "第二行"]


def test_tail_log_utf16_from_cmd_redirect(sandbox):
    """cmd 的 `>` 重定向写 UTF-16LE，必须能正确解码，否则日志全是乱码。"""
    (sandbox["log_dir"] / "startup-console.log").write_bytes(
        "TokBrain 1.0.0 已启动：http://127.0.0.1:3001\n".encode("utf-16-le")
    )
    lines = service.tail_log("startup-console.log", 5)
    assert lines and "已启动" in lines[0]


def test_tail_log_drops_nul_lines(sandbox):
    (sandbox["log_dir"] / "startup-console.log").write_bytes(
        "\x00\x00\r\n有效行\r\n".encode("utf-16-le")
    )
    lines = service.tail_log("startup-console.log", 5)
    assert lines == ["有效行"]


def test_started_recently_within_window(sandbox):
    path = sandbox["log_dir"] / "startup-console.log"
    path.write_text("TokBrain 1.0.0 已启动\n", encoding="utf-8")
    assert service._started_recently() is True


def test_started_recently_outside_window(sandbox):
    path = sandbox["log_dir"] / "startup-console.log"
    path.write_text("TokBrain 1.0.0 已启动\n", encoding="utf-8")
    old = time.time() - 7200
    os.utime(path, (old, old))
    assert service._started_recently() is False


def test_probe_state_stopped(sandbox, monkeypatch):
    monkeypatch.setattr(service, "port_open", lambda *_a, **_k: False)
    monkeypatch.setattr(service, "_health", lambda: {"reachable": False})
    state = service.probe_state()
    assert state["state"] == "stopped"


def test_probe_state_running(sandbox, monkeypatch):
    monkeypatch.setattr(service, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(
        service, "_health", lambda: {"reachable": True, "api_contract": 7}
    )
    state = service.probe_state()
    assert state["state"] == "running"
    assert state["note"] is None


def test_probe_state_warns_on_contract_mismatch(sandbox, monkeypatch):
    monkeypatch.setattr(service, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(
        service, "_health", lambda: {"reachable": True, "api_contract": 99}
    )
    state = service.probe_state()
    assert state["state"] == "running"
    assert "99" in state["note"]


def test_probe_state_orphan_when_port_busy_without_runtime(sandbox, monkeypatch):
    monkeypatch.setattr(service, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(service, "_health", lambda: {"reachable": False})
    state = service.probe_state()
    assert state["state"] == "orphan"


def test_service_lock_is_exclusive(sandbox):
    with service._ServiceLock():
        with pytest.raises(ControlError) as info:
            with service._ServiceLock():
                pass
        assert "正在进行" in str(info.value)


def test_service_lock_cleared_after_exit(sandbox):
    with service._ServiceLock():
        assert service.LOCK_PATH.exists()
    assert not service.LOCK_PATH.exists()


def test_service_lock_takes_over_stale(sandbox):
    service.LOCK_PATH.write_text("999 0\n", encoding="utf-8")
    old = time.time() - service.LOCK_STALE_SECONDS - 10
    os.utime(service.LOCK_PATH, (old, old))
    with service._ServiceLock():
        assert service.LOCK_PATH.exists()


def test_stop_when_already_stopped_is_idempotent(sandbox, monkeypatch):
    monkeypatch.setattr(service, "probe_state", lambda: {
        "state": "stopped",
        "runtime_json": {"exists": False},
        "ports": {},
    })
    result = service.stop()
    assert result == {"state": "stopped", "already_stopped": True, "elapsed": 0.0}
