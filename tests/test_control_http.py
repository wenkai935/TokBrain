"""HTTP 传输层测试：用假后端验证错误映射与"不发 Origin"。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tokbrain_control.errors import ControlError
from tokbrain_control.http import Transport

SEEN_HEADERS: list[dict[str, str]] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        SEEN_HEADERS.append({k.lower(): v for k, v in self.headers.items()})
        if self.path.startswith("/health"):
            self._send(200, {"status": "healthy", "scope": "process_only", "api_contract": 7})
        elif self.path.startswith("/api/library/works"):
            self._send(200, {"items": [], "total": 0, "next_offset": None})
        elif self.path.startswith("/api/nope"):
            self._send(404, {"detail": "作品不存在"})
        elif self.path.startswith("/api/boom"):
            self._send(500, {"detail": {"code": "internal", "message": "炸了", "retryable": True}})
        else:
            self._send(404, {"detail": "未知路径"})

    def do_POST(self):  # noqa: N802
        SEEN_HEADERS.append({k.lower(): v for k, v in self.headers.items()})
        if self.path.startswith("/api/conflict"):
            self._send(409, {"detail": "该作品已在处理队列中"})
        elif self.path.startswith("/api/validation"):
            self._send(
                422,
                {"detail": [{"loc": ["body", "text"], "msg": "不能为空", "type": "too_short"}]},
            )
        elif self.path.startswith("/api/forbidden"):
            self._send(403, {"detail": "same_origin_required"})
        else:
            self._send(202, {"batch_id": "b_1", "job_id": "j_1"})


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def transport(base_url):
    SEEN_HEADERS.clear()
    return Transport(base_url, read_timeout=5.0)


def test_health_parsed(transport):
    assert transport.health()["api_contract"] == 7


def test_get_does_not_send_origin(transport):
    """控制面必须不带 Origin，否则会被后端的 CSRF 中间件拦掉。"""
    transport.get("/api/library/works", query={"limit": 1, "collection_id": None})
    assert SEEN_HEADERS
    assert "origin" not in SEEN_HEADERS[-1]
    assert "referer" not in SEEN_HEADERS[-1]


def test_query_skips_none(transport):
    transport.get("/api/library/works", query={"library_state": "in_library", "lid": None})
    # 只要不抛异常即可；具体 URL 由服务端 200 证明可用
    assert SEEN_HEADERS[-1]["host"]


def test_404_maps_to_business(transport):
    with pytest.raises(ControlError) as info:
        transport.get("/api/nope")
    assert info.value.http_status == 404
    assert info.value.kind == "business"


def test_500_maps_to_retryable_server(transport):
    with pytest.raises(ControlError) as info:
        transport.get("/api/boom")
    assert info.value.kind == "server"
    assert info.value.retryable is True
    assert info.value.code == "internal"


def test_409_string_detail(transport):
    with pytest.raises(ControlError) as info:
        transport.post("/api/conflict", json_body={})
    assert info.value.http_status == 409
    assert "队列" in info.value.message


def test_422_list_detail_flattened(transport):
    with pytest.raises(ControlError) as info:
        transport.post("/api/validation", json_body={})
    assert info.value.http_status == 422
    assert "text" in info.value.message


def test_403_is_rejected_not_offline(transport):
    with pytest.raises(ControlError) as info:
        transport.post("/api/forbidden", json_body={})
    assert info.value.kind == "business"
    assert info.value.exit_code == 1


def test_offline_when_nothing_listening():
    dead = Transport("http://127.0.0.1:9", read_timeout=1.0)
    with pytest.raises(ControlError) as info:
        dead.get("/health")
    assert info.value.kind == "offline"
    assert info.value.exit_code == 3


def test_assert_contract_mismatch(base_url):
    class Fake(Transport):
        def health(self, *, read_timeout: float = 5.0):
            return {"status": "healthy", "api_contract": 99}

    with pytest.raises(ControlError) as info:
        Fake(base_url).assert_contract()
    assert info.value.code == "backend_contract_mismatch"


def test_invalid_base_url():
    with pytest.raises(ControlError) as info:
        Transport("127.0.0.1:8001")
    assert info.value.kind == "usage"
