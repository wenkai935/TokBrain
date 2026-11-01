"""sqlite-vec integration: vec0 extension + ``chunk_vec`` virtual table helpers.

The vec0 extension is loaded per pooled connection.  aiosqlite runs the SQLite
connection on its own worker thread, so we must use aiosqlite's async
``enable_load_extension`` / ``load_extension`` API instead of touching the raw
sqlite3 connection from the event loop.  Every helper degrades gracefully: if
sqlite-vec is missing or cannot be loaded, callers fall back to the in-memory
scanner, so the app stays fully functional without the vector index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from loguru import logger
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_LOADED_CONNECTIONS: set[int] = set()


def vec0_path() -> str | None:
    """Absolute path of the loadable vec0 extension, or None if unavailable."""
    try:
        import sqlite_vec
    except ImportError:
        return None
    try:
        return sqlite_vec.loadable_path()
    except Exception:
        return None


def chunk_vec_ddl(dimensions: int | None = None) -> str:
    size = dimensions or settings.embedding_dimensions
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec "
        f"USING vec0(embedding float[{size}], work_id int)"
    )


async def ensure_vec0(session: AsyncSession) -> bool:
    """Load vec0 on this session's pooled connection and ensure chunk_vec exists.

    Returns True when vector retrieval is usable on this session.
    """
    path = vec0_path()
    if not path:
        return False
    try:
        async_connection = await session.connection()
        fairy = await async_connection.get_raw_connection()
        dbapi = getattr(fairy, "_connection", None)
        if dbapi is None or not hasattr(dbapi, "enable_load_extension"):
            return False
        key = id(dbapi)
        if key not in _LOADED_CONNECTIONS:
            await dbapi.enable_load_extension(True)
            await dbapi.load_extension(path)
            _LOADED_CONNECTIONS.add(key)
        await session.execute(text(chunk_vec_ddl()))
        return True
    except Exception as exc:
        logger.warning("加载 sqlite-vec 扩展失败，将回退为内存检索: {}", exc)
        return False


async def vector_candidates(
    session: AsyncSession,
    work_ids: set[int],
    query_vector: list[float],
    *,
    k: int,
) -> dict[int, float]:
    """KNN over ``chunk_vec`` filtered by work ids -> ``{chunk_id: distance}``."""
    if not work_ids:
        return {}
    statement = text(
        "SELECT rowid, distance FROM chunk_vec "
        "WHERE work_id IN :ids AND embedding MATCH :vector AND k = :k"
    ).bindparams(bindparam("ids", expanding=True))
    result = await session.execute(
        statement,
        {
            "ids": sorted(work_ids),
            "vector": json.dumps(query_vector, ensure_ascii=False),
            "k": int(k),
        },
    )
    return {int(row[0]): float(row[1]) for row in result.all()}


async def replace_chunk_vectors(
    session: AsyncSession,
    items: list[tuple[int, int, list[float] | None]],
) -> None:
    """(chunk_id, work_id, embedding) replacements for one work.

    Deletes stale rows for the affected works, then inserts fresh vectors.
    Chunks without an embedding are simply not indexed.
    """
    work_ids = {int(work_id) for _, work_id, _ in items}
    if work_ids:
        await session.execute(
            text(
                "DELETE FROM chunk_vec WHERE work_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": sorted(work_ids)},
        )
    rows = [
        {
            "rowid": int(chunk_id),
            "embedding": json.dumps(embedding, ensure_ascii=False),
            "work_id": int(work_id),
        }
        for chunk_id, work_id, embedding in items
        if embedding
    ]
    if rows:
        await session.execute(
            text(
                "INSERT INTO chunk_vec(rowid, embedding, work_id) "
                "VALUES (:rowid, :embedding, :work_id)"
            ),
            rows,
        )


def ensure_chunk_vec_sync(path: Path) -> bool:
    """Create ``chunk_vec`` and backfill/clean it with a sync sqlite3 connection.

    Runs in a worker thread at startup/migration.  Returns True when the index
    is ready; False when sqlite-vec is unavailable (retrieval falls back).
    """
    if not path.exists():
        return False
    try:
        import sqlite_vec
    except ImportError:
        return False
    connection = sqlite3.connect(path)
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.execute(chunk_vec_ddl())
        # vec0 does not honour INSERT OR IGNORE for duplicate rowids, so only
        # backfill rows that are missing from the index (idempotent re-runs).
        connection.execute(
            "INSERT INTO chunk_vec(rowid, embedding, work_id) "
            "SELECT id, embedding, work_id FROM knowledge_chunks "
            "WHERE embedding IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM chunk_vec cv WHERE cv.rowid = knowledge_chunks.id)"
        )
        connection.execute(
            "DELETE FROM chunk_vec "
            "WHERE rowid NOT IN (SELECT id FROM knowledge_chunks)"
        )
        connection.commit()
        return True
    except Exception as exc:
        logger.warning("构建 chunk_vec 向量索引失败，将回退为内存检索: {}", exc)
        return False
    finally:
        connection.close()
