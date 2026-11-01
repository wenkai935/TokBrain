"""Create and verify a consistent SQLite snapshot for NAS deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/douyin_rag.db")
    parser.add_argument("--output", default=".archify/tokbrain-nas-deploy/douyin_rag.db")
    args = parser.parse_args()
    source, output = Path(args.source), Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".db.tmp")
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
        src.backup(dst)
        dst.commit()
    with closing(sqlite3.connect(temporary)) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        works = db.execute("SELECT COUNT(*) FROM works WHERE library_state='in_library'").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"snapshot integrity check failed: {integrity}")
    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "source": str(source.resolve()),
                "snapshot": str(output.resolve()), "sha256": digest,
                "bytes": output.stat().st_size, "works_in_library": works, "chunks_total": chunks}
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
