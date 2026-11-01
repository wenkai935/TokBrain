import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "mcp_server" / "tokbrain_hermes_stdio.py"
SPEC = importlib.util.spec_from_file_location("tokbrain_hermes_stdio", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def test_search_returns_compact_grounded_evidence(monkeypatch):
    monkeypatch.setattr(module, "DB_PATH", Path(__file__).parents[1] / "data" / "douyin_rag.db")
    result = module._search({"query": "视频", "limit": 3})
    assert result["ok"] is True
    assert len(result["results"]) <= 3
    for hit in result["results"]:
        assert len(hit["snippet"]) <= module.SNIPPET_CHARS + 1
        assert hit["work_id"] > 0
        assert hit["title"]


def test_mcp_tool_contract_is_read_only_and_bounded():
    names = {tool["name"] for tool in module.TOOLS}
    assert names == {"tokbrain_search", "tokbrain_get_work", "tokbrain_get_transcript",
                     "tokbrain_list_collections", "tokbrain_stats"}
    assert all(tool["annotations"]["readOnlyHint"] for tool in module.TOOLS)
    search = next(tool for tool in module.TOOLS if tool["name"] == "tokbrain_search")
    assert search["inputSchema"]["properties"]["limit"]["maximum"] == 5
    transcript = next(tool for tool in module.TOOLS if tool["name"] == "tokbrain_get_transcript")
    assert transcript["inputSchema"]["properties"]["limit"]["maximum"] == 8


def test_json_rpc_initialize_contains_token_budget_instructions():
    response = module._reply({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["result"]["serverInfo"]["name"] == "tokbrain-readonly"
    assert "最多三次" in response["result"]["instructions"]
    json.dumps(response, ensure_ascii=False)
