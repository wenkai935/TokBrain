"""Dependency-free, read-only TokBrain MCP adapter for Hermes on the NAS.

The adapter speaks MCP JSON-RPC over stdio and opens a consistent SQLite
snapshot in read-only mode.  It deliberately returns small evidence bundles:
Hermes should answer from search results first and request a transcript only
when the first bundle is insufficient.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


DB_PATH = Path(os.environ.get("TOKBRAIN_DB_PATH", "/vol1/1000/tokbrain/data/douyin_rag.db"))
MAX_QUERY_CHARS = 200
MAX_SEARCH_RESULTS = 5
MAX_TRANSCRIPT_SEGMENTS = 8
SNIPPET_CHARS = 600
TRANSCRIPT_CHARS = 320
EVIDENCE_KINDS = ("subtitle", "transcript", "ocr", "visual", "notes")

INSTRUCTIONS = (
    "TokBrain 是用户自己的视频知识库。仅在问题可能涉及其收藏、视频内容或过去保存的资料时调用。"
    "先调用 tokbrain_search，默认 limit=3；只要 snippet 能回答就立即作答并停止调用工具。"
    "只有缺少关键结论时才调用 tokbrain_get_work；只有用户明确追问细节或原话时，才按命中时间前后各 30 秒调用 tokbrain_get_transcript。"
    "每个问题最多三次 TokBrain 工具调用，不得用同一参数重复查询。"
    "答案必须忠于 evidence，不得补造；无命中就说明知识库未找到。"
    "回答尽量简短，并在末尾注明《标题》与时间戳；用户要求链接时再输出 url。"
)


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


TOOLS = [
    _schema(
        "tokbrain_search",
        "搜索用户的 TokBrain 视频知识库。先用本工具；snippet 通常足够直接回答。默认只取 3 条以节省上下文。",
        {
            "query": {"type": "string", "maxLength": MAX_QUERY_CHARS},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS, "default": 3},
            "collection": {"type": "string"},
        },
        ["query"],
    ),
    _schema(
        "tokbrain_get_work",
        "搜索证据不足时，读取一个命中作品的紧凑摘要。不要对所有命中逐个调用。",
        {"work_id": {"type": "integer", "minimum": 1}},
        ["work_id"],
    ),
    _schema(
        "tokbrain_get_transcript",
        "仅在搜索和作品摘要仍不足时读取时间轴。优先传搜索命中附近的 start_seconds/end_seconds。",
        {
            "work_id": {"type": "integer", "minimum": 1},
            "start_seconds": {"type": "number", "minimum": 0},
            "end_seconds": {"type": "number", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_TRANSCRIPT_SEGMENTS, "default": 4},
        },
        ["work_id"],
    ),
    _schema("tokbrain_list_collections", "仅在用户询问知识库有哪些主题或明确要限定主题时调用。", {}, []),
    _schema("tokbrain_stats", "检查 TokBrain 副本是否有数据；不要用于普通内容问答。", {}, []),
]


def _connect() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"TokBrain database not found: {DB_PATH}")
    uri = f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _clean(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _timestamp(seconds: Any) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _lexical_score(query: str, text: str) -> float:
    question = re.sub(r"\s+", "", query.lower())
    haystack = re.sub(r"\s+", "", text.lower())
    if not question or not haystack:
        return 0.0
    terms: set[str] = set()
    for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", question):
        terms.add(token)
        for sequence in re.findall(r"[\u4e00-\u9fff]+", token):
            for width in (2, 3):
                terms.update(sequence[i : i + width] for i in range(max(0, len(sequence) - width + 1)))
    matched = sum(1 for term in terms if term in haystack) / max(1, len(terms))
    return matched + (1.5 if len(question) >= 2 and question in haystack else 0.0)


def _search(args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    if len(query) > MAX_QUERY_CHARS:
        return {"ok": False, "error": f"query 最多 {MAX_QUERY_CHARS} 字"}
    limit = min(MAX_SEARCH_RESULTS, max(1, int(args.get("limit", 3))))
    collection = str(args.get("collection") or "").strip()
    placeholders = ",".join("?" for _ in EVIDENCE_KINDS)
    sql = f"""
        SELECT kc.work_id, kc.source_kind, kc.text, kc.start_seconds,
               w.title, w.description, w.source_url, c.title AS collection
          FROM knowledge_chunks kc
          JOIN works w ON w.id = kc.work_id
          LEFT JOIN collection_memberships cm ON cm.work_id = w.id
          LEFT JOIN collections c ON c.id = cm.collection_id
         WHERE w.library_state = 'in_library'
           AND w.processing_state = 'processed'
           AND w.evidence_state IN ('sufficient', 'unverified')
           AND kc.source_kind IN ({placeholders})
    """
    params: list[Any] = list(EVIDENCE_KINDS)
    if collection:
        sql += " AND c.title = ?"
        params.append(collection)
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    ranked = []
    for row in rows:
        score = _lexical_score(query, f"{row['title']} {row['description']} {row['text']}")
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    results, seen = [], set()
    for score, row in ranked:
        work_id = int(row["work_id"])
        if work_id in seen:
            continue
        seen.add(work_id)
        results.append({
            "work_id": work_id,
            "title": row["title"],
            "collection": row["collection"],
            "source_kind": row["source_kind"],
            "timestamp": _timestamp(row["start_seconds"]),
            "timestamp_seconds": row["start_seconds"],
            "score": round(score, 4),
            "snippet": _clean(row["text"], SNIPPET_CHARS),
            "url": row["source_url"],
        })
        if len(results) >= limit:
            break
    return {"ok": True, "mode": "lexical", "count": len(results), "results": results}


def _get_work(args: dict) -> dict:
    work_id = int(args.get("work_id", 0))
    with _connect() as db:
        row = db.execute("""
            SELECT w.id, w.title, w.author_name, w.duration_seconds, w.source_url,
                   w.description, c.title AS collection, ws.one_sentence, ws.content_json, ws.tags
              FROM works w
              LEFT JOIN collection_memberships cm ON cm.work_id = w.id
              LEFT JOIN collections c ON c.id = cm.collection_id
              LEFT JOIN work_summaries ws ON ws.id = (SELECT MAX(id) FROM work_summaries WHERE work_id=w.id)
             WHERE w.id = ?
        """, (work_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": f"找不到 work_id={work_id}"}
    return {"ok": True, "work": {
        "work_id": row["id"], "title": row["title"], "author": row["author_name"],
        "duration": _timestamp(row["duration_seconds"]), "collection": row["collection"],
        "description": _clean(row["description"], 360),
        "summary": _clean(row["one_sentence"], 360),
        "detail": _clean(row["content_json"], 900), "tags": _clean(row["tags"], 240),
        "url": row["source_url"],
    }}


def _get_transcript(args: dict) -> dict:
    work_id = int(args.get("work_id", 0))
    limit = min(MAX_TRANSCRIPT_SEGMENTS, max(1, int(args.get("limit", 4))))
    sql = "SELECT source_kind,text,start_seconds,end_seconds FROM knowledge_chunks WHERE work_id=?"
    params: list[Any] = [work_id]
    if args.get("start_seconds") is not None:
        sql += " AND COALESCE(end_seconds,start_seconds,0)>=?"
        params.append(float(args["start_seconds"]))
    if args.get("end_seconds") is not None:
        sql += " AND COALESCE(start_seconds,0)<=?"
        params.append(float(args["end_seconds"]))
    sql += " ORDER BY chunk_index LIMIT ?"
    params.append(limit)
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    return {"ok": True, "work_id": work_id, "returned": len(rows), "segments": [
        {"timestamp": _timestamp(row["start_seconds"]), "start_seconds": row["start_seconds"],
         "end_seconds": row["end_seconds"], "source_kind": row["source_kind"],
         "text": _clean(row["text"], TRANSCRIPT_CHARS)} for row in rows
    ]}


def _list_collections(_args: dict) -> dict:
    with _connect() as db:
        rows = db.execute("""
            SELECT c.title, COUNT(cm.work_id) AS works FROM collections c
            LEFT JOIN collection_memberships cm ON cm.collection_id=c.id
            GROUP BY c.id ORDER BY c.sort_order,c.id
        """).fetchall()
    return {"ok": True, "collections": [{"title": r["title"], "works": r["works"]} for r in rows]}


def _stats(_args: dict) -> dict:
    with _connect() as db:
        works = db.execute("SELECT COUNT(*) FROM works WHERE library_state='in_library'").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    return {"ok": True, "works_in_library": works, "chunks_total": chunks, "mode": "read_only_snapshot"}


HANDLERS = {
    "tokbrain_search": _search,
    "tokbrain_get_work": _get_work,
    "tokbrain_get_transcript": _get_transcript,
    "tokbrain_list_collections": _list_collections,
    "tokbrain_stats": _stats,
}


def _reply(request: dict) -> dict | None:
    request_id = request.get("id")
    if request_id is None:
        return None
    method = request.get("method")
    params = request.get("params") or {}
    try:
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "tokbrain-readonly", "version": "1.0.0"},
                      "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if name not in HANDLERS:
                raise ValueError(f"unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            payload = HANDLERS[name](arguments)
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
                      "isError": payload.get("ok") is False}
        else:
            raise ValueError(f"unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}}


def main() -> None:
    for line in sys.stdin:
        try:
            response = _reply(json.loads(line))
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": f"parse error: {exc}"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
