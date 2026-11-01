import pytest


pytest.importorskip("mcp")

import mcp_server.tokbrain_mcp as tokbrain_mcp


async def _fake_mcp_app(_scope, _receive, _send):
    raise AssertionError("not called")


def test_mcp_defaults_to_loopback_without_token(monkeypatch):
    captured = {}

    def build(**kwargs):
        captured.update(kwargs)
        return _fake_mcp_app

    monkeypatch.delenv("TOKBRAIN_MCP_HOST", raising=False)
    monkeypatch.delenv("TOKBRAIN_MCP_TOKEN", raising=False)
    monkeypatch.delenv("TOKBRAIN_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(tokbrain_mcp.mcp, "streamable_http_app", build)
    assert tokbrain_mcp.build_app() is _fake_mcp_app
    assert captured["host"] == "127.0.0.1"


def test_mcp_rejects_unauthenticated_non_loopback(monkeypatch):
    monkeypatch.setenv("TOKBRAIN_MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("TOKBRAIN_MCP_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TOKBRAIN_MCP_TOKEN"):
        tokbrain_mcp.build_app()


def test_mcp_non_loopback_with_token_enables_guard(monkeypatch):
    monkeypatch.setenv("TOKBRAIN_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("TOKBRAIN_MCP_TOKEN", "secret")
    monkeypatch.delenv("TOKBRAIN_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(
        tokbrain_mcp.mcp, "streamable_http_app", lambda **_kwargs: _fake_mcp_app
    )
    assert isinstance(tokbrain_mcp.build_app(), tokbrain_mcp._BearerGuard)
