"""把浏览器里复制的登录态 Cookie 写进统一 Cookie 库。

为什么需要它：DPAPI 加密绑定 Windows 用户账户，换过用户或重装系统后，库里
存的旧 Cookie 会**从密码学意义上解不开**（``SecretUnavailableError``），改配置
修不回来，只能重新提供一次登录态。

本脚本只写**文件层** ``data/cookies/<platform>.txt``——它是运行时的事实来源，
优先级高于 DPAPI SecretRecord，所以写完立刻生效，不需要重启后端、也不碰加密。

用法（Cookie 先存成文件，避免出现在命令行历史里）::

    .venv/Scripts/python.exe scripts/set_cookie.py --platform douyin --file D:/Temp/douyin_cookie.txt

校验：写入前先确认关键登录字段存在（抖音要 ``sessionid``），避免把别的站的
Cookie 或半截复制的内容写进去。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import cookie_store  # noqa: E402
from app.services.platforms.cookies import (  # noqa: E402
    PLATFORM_AUTH_COOKIE_NAMES,
    parse_cookie_header,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="写入平台登录态 Cookie")
    parser.add_argument(
        "--platform",
        default="douyin",
        choices=sorted(PLATFORM_AUTH_COOKIE_NAMES),
        help="目标平台（默认 douyin）",
    )
    parser.add_argument("--file", required=True, help="Cookie 文本所在文件路径")
    args = parser.parse_args()

    source = Path(args.file)
    if not source.exists():
        print(f"[fail] 文件不存在: {source}")
        return 2
    text = source.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        print("[fail] 文件是空的")
        return 2

    cookies = parse_cookie_header(text)
    names = {cookie.get("name") for cookie in cookies}
    expected = PLATFORM_AUTH_COOKIE_NAMES[args.platform]
    if not (names & expected):
        print(f"[fail] 缺少登录字段 {sorted(expected)}，解析到 {len(cookies)} 个 cookie")
        print("       请确认复制的是已登录状态的完整 Cookie（含 HttpOnly 项）")
        return 3

    try:
        path = cookie_store.save_text(args.platform, text)
    except cookie_store.CookieStoreError as exc:
        print(f"[fail] 写入失败: {exc}")
        return 4

    print(f"[ok] 已写入 {path}（{len(text)} 字符，{len(cookies)} 个 cookie）")
    print(f"     命中登录字段: {sorted(names & expected)}")
    print()
    print("下一步验证：.venv/Scripts/python.exe scripts/check_cookie.py")
    print("第 3 层应显示「✅ 拿到 Cookie」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
