"""诊断抖音 / F2 Cookie 是否可用。

回答的是「为什么报 f2_cookie_unreadable」这类问题——不要靠猜，跑这个。

三层链路（与运行时完全一致）：

1. **文件层**：``data/cookies/douyin.txt``（新格式，优先级最高）
2. **DPAPI 层**：``secret_records`` 表里的 ``douyin_cookie`` / ``f2_cookie``
3. **实际层**：直接调 ``cookie_header_for(session, "f2")``，看预检真正拿到什么

用法::

    .venv/Scripts/python.exe scripts/check_cookie.py

不打印任何 Cookie 内容，只看存不存在、能不能解开、长度多少。

关键判据：**DPAPI 密钥绑定 Windows 用户账户**。同一个库里如果旧 secret 解不开、
新 secret 能解开，说明中间换过 Windows 用户或重装过系统——那些 Cookie 已经从
密码学意义上失效，只能重新保存，改配置没用。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import async_session_factory  # noqa: E402
from app.models import SecretRecord  # noqa: E402
from app.services import cookie_store, secrets  # noqa: E402

PLATFORMS = ("douyin", "f2", "bilibili", "xiaohongshu")


def _file_layer() -> None:
    print("=" * 64)
    print("第 1 层 · 文件（优先级最高）")
    print("=" * 64)
    root = cookie_store.cookie_root()
    print(f"目录: {root}")
    print(f"存在: {root.exists()}")
    seen: set[str] = set()
    for platform in PLATFORMS:
        spec = cookie_store.PLATFORMS[cookie_store.resolve_platform(platform)]
        if spec.filename in seen:
            continue
        seen.add(spec.filename)
        path = root / spec.filename
        size = path.stat().st_size if path.exists() else 0
        print(f"  {spec.filename:<16} 存在={path.exists()!s:<5} 字节={size}")


async def _secret_layer(session) -> None:
    print()
    print("=" * 64)
    print("第 2 层 · DPAPI SecretRecord（文件缺失时的回退）")
    print("=" * 64)
    rows = (
        await session.execute(
            select(SecretRecord.name, SecretRecord.updated_at).order_by(
                SecretRecord.updated_at
            )
        )
    ).all()
    if not rows:
        print("  表里没有任何 SecretRecord")
        return
    for row in rows:
        record = await session.get(SecretRecord, row.name)
        try:
            plain = secrets.unprotect_text(record.encrypted_value)
            print(f"  {row.name:<22} ✅ 可解密，明文 {len(plain)} 字符   ({row.updated_at})")
        except secrets.SecretUnavailableError as exc:
            print(f"  {row.name:<22} ❌ {exc}   ({row.updated_at})")
        except Exception as exc:  # noqa: BLE001 - 诊断工具，如实打印
            print(f"  {row.name:<22} ❌ {type(exc).__name__}: {exc}   ({row.updated_at})")


async def _runtime_layer(session) -> None:
    print()
    print("=" * 64)
    print("第 3 层 · 运行时实际取值 cookie_header_for('f2')")
    print("=" * 64)
    try:
        header = await cookie_store.cookie_header_for(session, "f2")
    except secrets.SecretUnavailableError as exc:
        print(f"  ❌ 抛 SecretUnavailableError: {exc}")
        print("     → 这就是 f2_cookie_unreadable 的直接来源")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ {type(exc).__name__}: {exc}")
        return
    if header:
        print(f"  ✅ 拿到 Cookie，长度 {len(header)}（内容不打印）")
        print("     f2 可以带登录态解析，私密/需登录的作品也能取")
    else:
        print("  ⚠️ 拿到空串 —— 等同于未登录")
        print("     f2 只能匿名解析，需登录的作品会报 f2_cookie_required")


async def main() -> None:
    _file_layer()
    async with async_session_factory() as session:
        await _secret_layer(session)
        await _runtime_layer(session)
    print()
    print("=" * 64)
    print("怎么修")
    print("=" * 64)
    print("  TokenBrain 网页 → 设置 → 抖音登录态 → 重新保存（粘贴 Cookie）")
    print("  保存后重跑本脚本，第 3 层应显示「✅ 拿到 Cookie」")


if __name__ == "__main__":
    asyncio.run(main())
