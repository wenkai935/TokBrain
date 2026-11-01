"""CLI 测试：参数解析、退出码、JSON 信封形状。"""

from __future__ import annotations

import json

import pytest

from tokbrain_control import cli
from tokbrain_control.errors import EXIT_FAIL, EXIT_OFFLINE, EXIT_OK, EXIT_USAGE


def test_exit_codes_map_by_kind():
    assert cli._exit_for({"ok": True}) == EXIT_OK
    assert cli._exit_for({"ok": False, "error": {"kind": "usage"}}) == EXIT_USAGE
    assert cli._exit_for({"ok": False, "error": {"kind": "offline"}}) == EXIT_OFFLINE
    assert cli._exit_for({"ok": False, "error": {"kind": "business"}}) == EXIT_FAIL
    assert cli._exit_for({"ok": False, "error": {"kind": "server"}}) == EXIT_FAIL


def test_envelope_shape_is_stable():
    envelope = {
        "ok": True,
        "tool": "tb_status",
        "audit_id": None,
        "elapsed_ms": 12,
        "data": {"backend": {"alive": True}},
        "error": None,
    }
    assert set(envelope) == {"ok", "tool", "audit_id", "elapsed_ms", "data", "error"}


def test_emit_json_roundtrip(capsys):
    envelope = {
        "ok": True,
        "tool": "tb_status",
        "audit_id": "abc123",
        "elapsed_ms": 5,
        "data": {"n": 1},
        "error": None,
    }
    cli._emit(envelope, as_json=True)
    out = json.loads(capsys.readouterr().out)
    assert out == envelope


def test_emit_error_is_readable(capsys):
    cli._emit(
        {
            "ok": False,
            "tool": "tb_status",
            "audit_id": None,
            "elapsed_ms": 3,
            "data": None,
            "error": {
                "kind": "offline",
                "code": "backend_offline",
                "message": "连不上 TokBrain 后端",
                "retryable": True,
                "hint": "PC 可能休眠",
                "http_status": None,
                "detail": None,
            },
        },
        as_json=False,
    )
    text = capsys.readouterr().out
    assert "backend_offline" in text
    assert "PC 可能休眠" in text


@pytest.mark.parametrize(
    "argv,expected_group,expected_action",
    [
        (["status"], "status", None),
        (["service", "start"], "service", "start"),
        (["import", "links", "--text", "https://x"], "import", "links"),
        (["job", "wait", "j_1"], "job", "wait"),
        (["search", "视频里讲了什么"], "search", None),
        (["fs", "probe", "Z:\\videos"], "fs", "probe"),
        (["work", "delete", "12", "--confirm-title", "标题"], "work", "delete"),
    ],
)
def test_parser_accepts(argv, expected_group, expected_action):
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    assert args.group == expected_group
    assert getattr(args, "action", None) == expected_action


def test_parser_requires_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_rejects_bad_library_state():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["work", "list", "--state", "bogus"])


def test_ids_helper():
    assert cli._ids("101, 102 ,103") == [101, 102, 103]
    assert cli._ids("") == []
    assert cli._ids("a,1") == [1]


def test_search_renders_evidence_and_reminder(capsys):
    cli._emit(
        {
            "ok": True,
            "tool": "tb_search",
            "audit_id": None,
            "elapsed_ms": 40,
            "data": {
                "mode": "hybrid",
                "query": "三个要点",
                "collection": None,
                "count": 1,
                "note": None,
                "results": [
                    {
                        "work_id": 7,
                        "title": "某个视频",
                        "collection": "AI工具",
                        "timestamp": "00:01:30",
                        "url": "https://example.com/1",
                        "score": 0.82,
                        "snippet": "第一点……",
                    }
                ],
            },
            "error": None,
        },
        as_json=False,
    )
    text = capsys.readouterr().out
    assert "某个视频" in text
    assert "00:01:30" in text
    assert "不含生成答案" in text


def test_status_render_shows_counts(capsys):
    cli._emit(
        {
            "ok": True,
            "tool": "tb_status",
            "audit_id": None,
            "elapsed_ms": 10,
            "data": {
                "backend": {"alive": True, "api_contract": 7},
                "library": {"in_library": 14, "pending": 2, "issues": 1, "collections": 3},
                "jobs": [],
                "coordinators": {"status": "healthy"},
            },
            "error": None,
        },
        as_json=False,
    )
    text = capsys.readouterr().out
    assert "在库 14" in text
    assert "契约: 7" in text
