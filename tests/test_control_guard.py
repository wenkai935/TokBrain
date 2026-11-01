"""8010/8011 共用的 HTTP 护栏测试（纯 stdlib，不需要 mcp 包）。"""

from __future__ import annotations

import pytest

from mcp_server._http_guard import (
    MIN_TOKEN_LENGTH,
    BearerGuard,
    is_loopback,
    resolve_bind,
)

TOKEN = "a" * MIN_TOKEN_LENGTH


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("192.168.10.100", False),
        ("0.0.0.0", False),
        ("example.com", False),
    ],
)
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_resolve_bind_requires_enable_flag():
    with pytest.raises(RuntimeError, match="ENABLE"):
        resolve_bind(
            {}, prefix="TOKBRAIN_CONTROL_", default_port=8011,
            enable_var="TOKBRAIN_CONTROL_ENABLE",
        )


def test_resolve_bind_rejects_non_loopback_without_token():
    env = {"TOKBRAIN_CONTROL_ENABLE": "1", "TOKBRAIN_CONTROL_HOST": "192.168.10.100"}
    with pytest.raises(RuntimeError, match="TOKEN"):
        resolve_bind(
            env, prefix="TOKBRAIN_CONTROL_", default_port=8011,
            enable_var="TOKBRAIN_CONTROL_ENABLE",
        )


def test_resolve_bind_rejects_short_token():
    """挡住随手写的 4 位数字令牌。"""
    env = {
        "TOKBRAIN_CONTROL_ENABLE": "1",
        "TOKBRAIN_CONTROL_HOST": "192.168.10.100",
        "TOKBRAIN_CONTROL_TOKEN": "1234",
    }
    with pytest.raises(RuntimeError, match=str(MIN_TOKEN_LENGTH)):
        resolve_bind(
            env, prefix="TOKBRAIN_CONTROL_", default_port=8011,
            enable_var="TOKBRAIN_CONTROL_ENABLE",
        )


def test_resolve_bind_loopback_needs_no_token():
    env = {"TOKBRAIN_CONTROL_ENABLE": "1"}
    bind = resolve_bind(
        env, prefix="TOKBRAIN_CONTROL_", default_port=8011,
        enable_var="TOKBRAIN_CONTROL_ENABLE",
    )
    assert bind.host == "127.0.0.1"
    assert bind.port == 8011
    assert bind.loopback is True
    assert bind.allowed_hosts == []


def test_resolve_bind_builds_allowed_hosts_for_lan():
    env = {
        "TOKBRAIN_CONTROL_ENABLE": "1",
        "TOKBRAIN_CONTROL_HOST": "192.168.10.100",
        "TOKBRAIN_CONTROL_TOKEN": TOKEN,
    }
    bind = resolve_bind(
        env, prefix="TOKBRAIN_CONTROL_", default_port=8011,
        enable_var="TOKBRAIN_CONTROL_ENABLE",
    )
    assert bind.allowed_hosts == ["192.168.10.100:8011"]
    assert bind.url() == "http://192.168.10.100:8011/mcp"


def test_resolve_bind_rejects_bad_port():
    env = {"TOKBRAIN_CONTROL_ENABLE": "1", "TOKBRAIN_CONTROL_PORT": "abc"}
    with pytest.raises(RuntimeError, match="PORT"):
        resolve_bind(
            env, prefix="TOKBRAIN_CONTROL_", default_port=8011,
            enable_var="TOKBRAIN_CONTROL_ENABLE",
        )


async def _run_guard(token: str, presented: bytes | None) -> list[dict]:
    called: list[str] = []

    async def app(scope, receive, send):
        called.append("app")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = [(b"authorization", presented)] if presented else []
    scope = {"type": "http", "headers": headers}
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    guard = BearerGuard(app, token)
    await guard(scope, receive, send)
    return sent


async def test_guard_allows_matching_token():
    sent = await _run_guard(TOKEN, f"Bearer {TOKEN}".encode())
    assert sent and sent[0]["status"] == 200


async def test_guard_rejects_missing_token():
    sent = await _run_guard(TOKEN, None)
    assert sent[0]["status"] == 401


async def test_guard_rejects_wrong_token():
    sent = await _run_guard(TOKEN, b"Bearer wrong-token")
    assert sent[0]["status"] == 401
