"""doubaosave 独立脚本桥接：DOM 路线采集抖音个人数据。

路线选择（用户 2026-09-06 定）：放弃「粘贴 Cookie → handoff → pull」路线，
改用 doubaosave 的 Playwright 浏览器自动化（DOM + API 拦截）采集抖音收藏/喜欢/历史，
采集出的 JSON 由本桥接读入并转成 PlatformVideoItem，进入现有导入管线。

doubaosave 脚本本身是独立 skill（Playwright 装在其专属 Python 环境，TokBrain 的
.venv 不含 Playwright），因此通过 subprocess 调用，避免污染后端依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.services.platforms.schemas import PlatformVideoItem

# doubaosave 脚本目录（项目内副本，TokBrain 自包含，不依赖 skill 注册目录）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOUBAOSAVE_SCRIPTS_DIR = _PROJECT_ROOT / "doubaosave Skill" / "source" / "scripts"
DOUBAOSAVE_MAIN = DOUBAOSAVE_SCRIPTS_DIR / "main.py"
DOUBAOSAVE_OUTPUT_DIR = DOUBAOSAVE_SCRIPTS_DIR / "output"

# 采集脚本的 Python：Playwright 装在项目内专用环境 .venv-collect，与后端 .venv 隔离
# （后端不需要 Playwright，避免污染依赖）。可用环境变量 DOUBAOSAVE_PYTHON 覆盖。
_COLLECT_VENV_PYTHON = _PROJECT_ROOT / ".venv-collect" / "Scripts" / "python.exe"
_FALLBACK_PYTHON = "C:/Users/xxsyl/.workbuddy/binaries/python/envs/default/Scripts/python.exe"


def _resolve_collect_python() -> str:
    """采集脚本解释器优先级：环境变量 > 项目内 .venv-collect > 旧的 WorkBuddy 环境。"""
    override = os.environ.get("DOUBAOSAVE_PYTHON")
    if override:
        return override
    if _COLLECT_VENV_PYTHON.exists():
        return str(_COLLECT_VENV_PYTHON)
    return _FALLBACK_PYTHON


DOUBAOSAVE_PYTHON = _resolve_collect_python()

# 前端 scope → doubaosave main.py 的 --only 参数。
DOUBAOSAVE_SCOPE_ONLY = {
    "fav": "favor",  # 收藏夹层级树
    "like": "like",  # 喜欢
    "history": "history",  # 观看历史
    "work": "post",  # 我的作品（发布视频）
}

# 前端 scope → douyin_data_*.json 里的字段名（用于预览/导入读取）。
SCOPE_FIELD = {
    "fav": "收藏",
    "like": "喜欢",
    "history": "观看历史",
    "work": "我的作品",
}

_COLLECT_TIMEOUT_SECONDS = 900  # 首次需浏览器登录，给足 15 分钟


def _child_env() -> dict[str, str]:
    """子进程环境：强制 UTF-8 输出。

    Windows 下 Python 以管道输出时默认用控制台代码页（中文系统为 cp936/GBK），
    脚本里的中文会以 GBK 字节写出；父进程若按 utf-8 解码就会得到一串替换字符
    （前端显示成方块，只有 ASCII 的英文命令能读）。这里统一钉死 UTF-8。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _decode_output(raw: bytes | None) -> str:
    """按 utf-8 → gbk → utf-8(replace) 依次解码子进程输出。

    即便已用 _child_env() 强制 UTF-8，仍保留 GBK 兜底：旧环境、或脚本内部
    另行调用外部命令时，仍可能吐出 GBK 字节。
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _friendly_failure(tail: str) -> str | None:
    """把常见失败的原始输出换成可执行的中文提示；不匹配时返回 None（保留原文）。"""
    low = tail.lower()
    if "no module named 'playwright'" in low or "未安装 playwright" in low:
        return (
            "采集环境缺少 Playwright（浏览器自动化依赖）。"
            f"请在 {DOUBAOSAVE_PYTHON} 这个环境执行 pip install playwright 后重试；"
            "本机已装 Chrome/Edge，可不必下载 chromium。"
        )
    if "executable doesn't exist" in low or ("playwright install" in low and "browser" in low):
        return (
            "采集所需浏览器未就绪。请执行 playwright install chromium，"
            "或在本机安装 Chrome/Edge 后重试。"
        )
    if "target page, context or browser has been closed" in low:
        return "浏览器被提前关闭，采集中断。请重试，并在采集完成前不要关掉弹出的浏览器窗口。"
    if "timed out" in low and "login" in low:
        return "等待登录超时。请重试，并在弹出的浏览器里完成抖音登录。"
    return None


def scope_supported(scope: str) -> bool:
    return scope in DOUBAOSAVE_SCOPE_ONLY


def field_for_scope(scope: str) -> str | None:
    """返回该 scope 在 douyin_data JSON 中对应的字段名。"""
    return SCOPE_FIELD.get(scope)


async def run_doubaosave_collect(scope: str) -> tuple[bool, str]:
    """subprocess 跑 doubaosave 采集（Playwright，可能数分钟），返回 (ok, message)。"""
    only = DOUBAOSAVE_SCOPE_ONLY[scope]
    cmd = [DOUBAOSAVE_PYTHON, str(DOUBAOSAVE_MAIN), "--only", only]

    def _run() -> tuple[bool, str]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=_COLLECT_TIMEOUT_SECONDS,
                cwd=str(DOUBAOSAVE_SCRIPTS_DIR),
                env=_child_env(),
            )
        except subprocess.TimeoutExpired:
            return False, "采集超时（15 分钟）"
        except FileNotFoundError:
            return False, (
                f"采集脚本 Python 不存在：{DOUBAOSAVE_PYTHON}。"
                "请确认 Playwright 已安装于该环境（pip install playwright && playwright install chromium）。"
            )
        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)
        if result.returncode != 0:
            tail = (stderr or stdout or "").strip()[-1000:]
            return False, _friendly_failure(tail) or f"采集失败：{tail}"
        return True, stdout.strip()[-2000:]

    return await asyncio.to_thread(_run)


def read_latest_favor_videos() -> list[dict[str, Any]]:
    """读最新的 douyin_data_*.json 的「收藏」字段（向后兼容别名）。"""
    return read_latest_videos("fav")


def _latest_douyin_data() -> dict | None:
    """返回最新 douyin_data_*.json 的内容（不存在/损坏时为 None）。"""
    if not DOUBAOSAVE_OUTPUT_DIR.exists():
        return None
    files = sorted(
        DOUBAOSAVE_OUTPUT_DIR.glob("douyin_data_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_latest_videos(scope: str) -> list[dict[str, Any]]:
    """读最新的 douyin_data_*.json，按 scope 取出对应分类的视频记录列表。

    - fav：main.py 的「收藏」字段是树展平后的 {层级路径: [视频]} dict；
    - like / history / work：对应字段是视频记录列表。

    每条记录自带 aweme_id / 标题 / 作者 / 视频链接 等字段。
    """
    data = _latest_douyin_data()
    if not data:
        return []
    field = SCOPE_FIELD.get(scope)
    if not field:
        return []
    raw = data.get(field)
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):  # fav：{路径: [视频]}
        for vids in raw.values():
            if isinstance(vids, list):
                for v in vids:
                    if isinstance(v, dict) and v.get("aweme_id"):
                        out.append(v)
    elif isinstance(raw, list):
        for v in raw:
            if isinstance(v, dict) and v.get("aweme_id"):
                out.append(v)
    return out


def read_latest_groups(scope: str) -> list[dict[str, Any]]:
    """读最新 douyin_data JSON 该 scope 的数据并按子集分组（用于前端左栏）。

    - fav：{收藏夹路径: [视频]} → 每条路径一组，组名为完整层级路径；
    - like / history / work：平铺列表 → 单个组（组名=分类名），
      便于调用方统一按 groups 渲染（一组时也可直接退化为平铺 items）。

    返回 [{name, count, videos: [record...]}]，组内已过滤无 aweme_id 的记录。
    """
    data = _latest_douyin_data()
    if not data:
        return []
    field = SCOPE_FIELD.get(scope)
    if not field:
        return []
    raw = data.get(field)
    groups: list[dict[str, Any]] = []
    if isinstance(raw, dict):  # fav：按收藏夹路径分组
        for path, vids in raw.items():
            if not isinstance(vids, list):
                continue
            kept = [v for v in vids if isinstance(v, dict) and v.get("aweme_id")]
            if kept:
                groups.append({"name": str(path), "count": len(kept), "videos": kept})
    elif isinstance(raw, list):  # like / history / work：单组
        kept = [v for v in raw if isinstance(v, dict) and v.get("aweme_id")]
        if kept:
            label = SCOPE_FIELD.get(scope, scope)
            groups.append({"name": label, "count": len(kept), "videos": kept})
    return groups


def records_to_items(records: list[dict[str, Any]], scope: str | None = None) -> list[PlatformVideoItem]:
    """doubaosave 记录（中文键）→ PlatformVideoItem 列表。

    source_url 归一化为 https://www.douyin.com/video/{aweme_id} 长链，
    保证进入导入管线后可被 F2 预检解析（分享短链 v.douyin.com 不稳）。
    """
    items: list[PlatformVideoItem] = []
    for r in records:
        aweme_id = str(r.get("aweme_id") or "").strip()
        if not aweme_id:
            continue
        source_url = str(
            r.get("视频链接") or r.get("分享链接") or ""
        ).strip()
        if "douyin.com/video/" not in source_url:
            source_url = f"https://www.douyin.com/video/{aweme_id}"
        title = str(r.get("标题") or "").strip() or None
        author = str(r.get("作者") or "").strip() or None
        published = str(r.get("发布时间") or "").strip() or None
        items.append(
            PlatformVideoItem(
                platform="douyin",
                platform_work_id=aweme_id,
                source_url=source_url,
                title=title,
                author_name=author,
                published_at=published,
                kind="video",
                raw={
                    "收藏夹ID": r.get("收藏夹ID"),
                    "收藏夹路径": r.get("收藏夹路径"),
                    "doubaosave_source": "dom",
                    **({"douyosave_scope": scope} if scope else {}),
                },
            )
        )
    return items
