"""Shared dispatch queue + inbound link normalization.

This module is the single source of truth for the ``dispatch`` queue that sits
between "something submitted a video link" and "the bridge processed it into
Obsidian". Both the FastAPI inbound API (``/api/integrations/v1/inbox/items``)
and the offline ``bridge_feishu_obsidian.py`` import from here so that API
submissions flow through the exact same state machine as Feishu rows.

Queue state machine (see bridge_feishu_obsidian.py):
    pending -> queued -> processing -> processed
                                       -> needs_supplement
                                       -> failed
Terminal (never auto-retried): processed / needs_supplement / failed.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path


def dispatch_db_path() -> Path:
    """Resolve the dispatch SQLite path, honoring the same env var the bridge uses.

    Resolved lazily (per call) so tests can override ``DISPATCH_DB`` before the
    store is constructed.
    """

    return Path(
        os.environ.get(
            "DISPATCH_DB",
            str(Path(__file__).resolve().parents[2] / "data" / "pipeline" / "dispatch.db"),
        )
    )


class InboxLinkError(ValueError):
    """Raised when an inbound link cannot be normalized for its adapter."""


def _douyin_share_normalize(link: str) -> str:
    """Normalize a Douyin share / video URL.

    Reuses the project's hardened ``normalize_input_url`` (HTTPS-only, Douyin
    host allow-list, canonical path without tracking query). Raises
    ``InboxLinkError`` on any violation.
    """

    from app.services.f2_links import PublicLinkError, normalize_input_url

    try:
        return normalize_input_url(link)
    except PublicLinkError as exc:
        raise InboxLinkError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise InboxLinkError(f"无法解析链接：{exc}") from exc


def _local_text_normalize(link: str) -> str:
    """Adapter slot for locally-authored references (extensibility demo).

    Accepts any non-empty string as a stable reference without host validation.
    Registered to prove new adapters can be added without touching the API or
    the rest of the pipeline (TC-P4-08).
    """

    value = (link or "").strip()
    if not value:
        raise InboxLinkError("link 不能为空")
    return value


# Adapter registry: source name -> normalizer. Add new legal sources here.
ADAPTERS: dict[str, object] = {
    "douyin_share": _douyin_share_normalize,
    "local_text": _local_text_normalize,
}
DEFAULT_ADAPTER = "douyin_share"
KNOWN_SOURCES = tuple(ADAPTERS)


def normalize_inbox_link(link: str, source: str = DEFAULT_ADAPTER) -> tuple[bool, str, str]:
    """Normalize ``link`` for ``source``.

    Returns ``(ok, normalized, error)``. When ``ok`` is False, ``normalized`` is
    empty and ``error`` carries a human-readable reason. Unregistered sources
    are rejected up front so the API can flag an unknown adapter explicitly.
    """

    if source not in ADAPTERS:
        return False, "", f"未知来源适配器：{source}"
    try:
        return True, ADAPTERS[source](link), ""
    except InboxLinkError as exc:
        return False, "", str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return False, "", f"链接规范化失败：{exc}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DispatchStore:
    """SQLite-backed queue shared by the inbound API and the bridge.

    Mirrors the schema/semantics previously defined inline in
    ``bridge_feishu_obsidian.py`` but adds ``source`` / ``normalized_key``
    columns (for inbound attribution + dedupe) and an ``inbox_idempotency``
    table used by the API's ``Idempotency-Key`` support.
    """

    #: 终态：慢轨不再把它们当「待处理」捡起来。
    #: awaiting_confirm 也是终态——预检已经做完，球在用户/agent 那边；
    #: 若当非终态，每轮都会重新导入同一行（重复建批）。
    #: ingested 同理：网页/控制面已确认并提交解析，只剩 bridge 回填 Obsidian，
    #: 若当非终态会被 process_queue 重新 process_link 一次，白建一个批次。
    TERMINAL = {
        "processed",
        "needs_supplement",
        "failed",
        "awaiting_confirm",
        "ingested",
    }

    def __init__(self, path=None):
        self.path = Path(path) if path else dispatch_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            with con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS dispatch (
                        record_id      TEXT PRIMARY KEY,
                        link           TEXT NOT NULL DEFAULT '',
                        tags           TEXT NOT NULL DEFAULT '',
                        local_path     TEXT NOT NULL DEFAULT '',
                        status         TEXT NOT NULL,
                        work_id        INTEGER,
                        note_path      TEXT NOT NULL DEFAULT '',
                        error_code     TEXT NOT NULL DEFAULT '',
                        error_message  TEXT NOT NULL DEFAULT '',
                        source         TEXT NOT NULL DEFAULT 'feishu',
                        normalized_key TEXT NOT NULL DEFAULT '',
                        lid            TEXT NOT NULL DEFAULT '',
                        batch_id       TEXT NOT NULL DEFAULT '',
                        created_at     TEXT NOT NULL,
                        updated_at     TEXT NOT NULL
                    )
                    """
                )
                # Backfill columns for databases created by older bridge versions.
                existing = {
                    row[1]
                    for row in con.execute("PRAGMA table_info(dispatch)").fetchall()
                }
                for column, col_type in (
                    ("source", "TEXT NOT NULL DEFAULT 'feishu'"),
                    ("normalized_key", "TEXT NOT NULL DEFAULT ''"),
                    ("lid", "TEXT NOT NULL DEFAULT ''"),
                    # 预检完成但还没确认的批次：确认接口靠它找回 import batch
                    ("batch_id", "TEXT NOT NULL DEFAULT ''"),
                ):
                    if column not in existing:
                        con.execute(
                            f"ALTER TABLE dispatch ADD COLUMN {column} {col_type}"
                        )
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS inbox_idempotency (
                        key_hash      TEXT PRIMARY KEY,
                        digest        TEXT NOT NULL,
                        response_json TEXT NOT NULL,
                        created_at    TEXT NOT NULL
                    )
                    """
                )
        finally:
            con.close()

    def enqueue(
        self,
        record_id: str,
        link: str,
        tags: str,
        local_path: str,
        note_path: str,
        *,
        source: str = "feishu",
        normalized_key: str = "",
        lid: str = "",
    ) -> bool:
        """Insert a queue row, skipping if ``record_id`` already exists (idempotent).

        Returns True when a new row was created.
        """

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con = self._connect()
        try:
            with con:
                con.execute(
                    "INSERT INTO dispatch (record_id, link, tags, local_path, status,"
                    " note_path, source, normalized_key, lid, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_id,
                        link,
                        tags,
                        local_path,
                        "pending",
                        note_path,
                        source,
                        normalized_key,
                        lid,
                        now,
                        now,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            con.close()

    def record_id_for_key(self, normalized_key: str):
        """Return an existing record_id for ``normalized_key``, else None.

        Used to dedupe inbound links across requests (TC-P4-04 / TC-P4-11).
        """

        if not normalized_key:
            return None
        con = self._connect()
        try:
            row = con.execute(
                "SELECT record_id FROM dispatch WHERE normalized_key=? LIMIT 1",
                (normalized_key,),
            ).fetchone()
            return row["record_id"] if row else None
        finally:
            con.close()

    def pending_items(self) -> list:
        """All non-terminal rows, for the slow track to resume."""

        # 占位符数量必须跟着 TERMINAL 走：写死 (?,?,?) 会在加状态时静默炸掉
        # （历史教训：awaiting_confirm 加进终态后这里少一个问号）。
        placeholders = ",".join("?" for _ in self.TERMINAL)
        con = self._connect()
        try:
            rows = con.execute(
                f"SELECT * FROM dispatch WHERE status NOT IN ({placeholders})"
                " ORDER BY created_at",
                tuple(self.TERMINAL),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def update(self, record_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cols = ", ".join(f"{k}=?" for k in fields)
        con = self._connect()
        try:
            with con:
                con.execute(
                    f"UPDATE dispatch SET {cols} WHERE record_id=?",
                    tuple(fields.values()) + (record_id,),
                )
        finally:
            con.close()

    def ids_by_status(self, status: str) -> list:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT record_id FROM dispatch WHERE status=? ORDER BY updated_at",
                (status,),
            ).fetchall()
            return [r["record_id"] for r in rows]
        finally:
            con.close()

    def counts(self) -> dict:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT status, COUNT(*) c FROM dispatch GROUP BY status"
            ).fetchall()
            return {r["status"]: r["c"] for r in rows}
        finally:
            con.close()

    def get(self, record_id: str):
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM dispatch WHERE record_id=?", (record_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    # ---- Idempotency-Key support (inbound API) ----

    def get_idempotency(self, key_hash: str):
        """Return ``(digest, response_json)`` for a stored key, else None."""

        con = self._connect()
        try:
            row = con.execute(
                "SELECT digest, response_json FROM inbox_idempotency WHERE key_hash=?",
                (key_hash,),
            ).fetchone()
            if not row:
                return None
            return row["digest"], row["response_json"]
        finally:
            con.close()

    def put_idempotency(self, key_hash: str, digest: str, response_json: str) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con = self._connect()
        try:
            with con:
                con.execute(
                    "INSERT OR REPLACE INTO inbox_idempotency"
                    " (key_hash, digest, response_json, created_at)"
                    " VALUES (?,?,?,?)",
                    (key_hash, digest, response_json, now),
                )
        finally:
            con.close()


def init_dispatch_db(path=None) -> DispatchStore:
    """Construct (and ensure schema of) the dispatch store at ``path``."""

    return DispatchStore(path=path)
