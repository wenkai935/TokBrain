"""批量重投：把 data/_retry_ids.txt 里的 record_id 从 failed 重置为 queued。

等价于逐条执行 `bridge_feishu_obsidian.py --retry <id>`（重置状态机 + 笔记标 pending），
但一次调用完成，供抖音熔断过期后批量恢复 8/29 环境故障遗留的失败条目。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.dispatch_store import DispatchStore

ID_FILE = PROJECT_ROOT / "data" / "_retry_ids.txt"


def main() -> None:
    if not ID_FILE.exists():
        print(f"[retry-batch] 未找到 {ID_FILE}")
        return
    ids = [ln.strip() for ln in ID_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    store = DispatchStore()
    ok = skipped = 0
    for rid in ids:
        item = store.get(rid)
        if not item:
            print(f"  [skip] 不存在 {rid}")
            skipped += 1
            continue
        store.update(rid, status="queued", error_code="", error_message="")
        # mark_note_status(item, "pending") —— 笔记若存在则同步标回 pending
        try:
            from bridge_feishu_obsidian import mark_note_status
            mark_note_status(item, "pending")
        except Exception:
            pass
        ok += 1
    print(f"[retry-batch] 完成: {ok} 条重置为 queued, {skipped} 条跳过")


if __name__ == "__main__":
    main()
