"""媒体路径约定：NAS 侧路径 ⇄ 本机 Windows 路径。

外部 Agent（Hermes / 大肥鱼）跑在 NAS 上，控制面跑在 Windows 上。两边看到的
路径**不是一回事**：NAS 上写 ``/vol1/1000/media/videos/a.mp4``，Windows 上要么
是映射盘符 ``Z:\\videos\\a.mp4``，要么根本看不见。

所以不能靠猜，必须有一份显式对应表。配置项：

    TOKBRAIN_CONTROL_MEDIA_ROOTS=<Windows 根目录>[|<NAS 前缀>];<...>

例：

    Z:\\videos|/vol1/1000/media/videos;D:\\本地视频

解析顺序（第一个 ``stat`` 成功的胜出）：

1. Windows 绝对路径（``X:\\`` 或 ``\\\\server\\share``）—— 原样使用；
2. 以某个 NAS 前缀开头的路径 —— 替换成对应的 Windows 根目录；
3. 相对路径或裸文件名 —— 逐个拼到各 root 下找；
4. 兜底 —— 原样（让 ``stat`` 失败，交给调用方报 ``path_not_visible``）。

没有配置这个变量时行为与之前一致：只认本机可见的绝对路径。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ENV_ROOTS = "TOKBRAIN_CONTROL_MEDIA_ROOTS"

_WIN_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".wmv",
    ".mp3",
    ".m4a",
    ".wav",
    ".aac",
    ".flac",
    ".zip",
}


def _parse(raw: str) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        win, _, nas = chunk.partition("|")
        win = win.strip().rstrip("\\/")
        nas_norm = nas.strip().replace("\\", "/").rstrip("/")
        if not win:
            continue
        entries.append((win, nas_norm or None))
    return entries


def specs() -> list[tuple[str, str | None]]:
    """当前生效的 (Windows 根目录, NAS 前缀) 列表。"""
    return _parse(os.environ.get(ENV_ROOTS) or "")


def _clean(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    text = text.strip()
    if not text:
        return ""
    return os.path.expandvars(os.path.expanduser(text))


def _has_dotdot(text: str) -> bool:
    return ".." in text.replace("\\", "/").split("/")


def candidates(raw: str) -> list[str]:
    """给出待尝试的 Windows 路径，按优先级去重。

    返回值只表示「值得 stat 一下」，不保证存在。
    """
    text = _clean(raw)
    if not text or _has_dotdot(text):
        return []

    out: list[str] = []

    def push(value: str) -> None:
        if value and value not in out:
            out.append(value)

    if _WIN_ABSOLUTE.match(text) or text.startswith("\\\\"):
        push(text)
        return out

    entries = specs()
    posix = text.replace("\\", "/")
    looks_posix_absolute = posix.startswith("/")

    if looks_posix_absolute:
        for win, nas in entries:
            if nas and (posix == nas or posix.startswith(nas + "/")):
                rest = posix[len(nas) :].lstrip("/")
                push(str(Path(win) / rest) if rest else win)
        # 没命中前缀也试一次「同名相对结构」，方便前缀写漏一个层级时自愈
        for win, _nas in entries:
            push(str(Path(win) / posix.lstrip("/")))
    else:
        for win, _nas in entries:
            push(str(Path(win) / posix))

    push(text)
    return out


def _sample(dir_path: Path, limit: int = 8) -> list[dict[str, Any]]:
    try:
        children = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    picked: list[dict[str, Any]] = []
    for child in children:
        if len(picked) >= limit:
            break
        try:
            if not child.is_file():
                continue
            if child.suffix.lower() not in _MEDIA_SUFFIXES:
                continue
            picked.append({"name": child.name, "size": child.stat().st_size})
        except OSError:
            continue
    return picked


def describe(include_samples: bool = True) -> dict[str, Any]:
    """给外部 Agent 看的约定说明：每个 root 的 Windows 路径、NAS 前缀、可用性。"""
    entries: list[dict[str, Any]] = []
    for win, nas in specs():
        root = Path(win)
        item: dict[str, Any] = {
            "windows_path": win,
            "nas_prefix": nas,
            "exists": False,
            "is_dir": False,
        }
        try:
            item["exists"] = root.exists()
            item["is_dir"] = root.is_dir() if item["exists"] else False
        except OSError as exc:
            item["error"] = repr(exc)
        if item["is_dir"] and include_samples:
            item["sample_files"] = _sample(root)
        entries.append(item)

    available = [entry for entry in entries if entry.get("is_dir")]
    data: dict[str, Any] = {
        "configured": bool(entries),
        "env_name": ENV_ROOTS,
        "roots": entries,
        "usable_roots": len(available),
        "rule": (
            "传 Windows 路径（如 Z:\\videos\\a.mp4）或 NAS 路径（如 "
            "/vol1/1000/media/videos/a.mp4）都可以；NAS 路径会按上表自动翻译。"
        ),
    }
    if not entries:
        data["hint"] = (
            f"还没有约定媒体目录。在 mcp_server/control.env 里设置 {ENV_ROOTS}，"
            "格式：Z:\\videos|/vol1/1000/media/videos（Windows 根目录|NAS 前缀，"
            "分号分隔多组）。没配时只能传本机绝对路径。"
        )
    elif not available:
        data["hint"] = (
            "配置里的目录当前都不可访问。先确认 NAS 映射盘是否在线："
            "双击 mcp_server/挂载NAS媒体盘.cmd 重新映射（net use 是登录时重连，"
            "NAS 重启后可能失效）。"
        )
    return data


def resolve(raw: str) -> tuple[str | None, list[dict[str, Any]]]:
    """按约定把输入翻译成一个**存在**的路径。

    返回 ``(已存在的路径 或 None, 尝试记录)``。尝试记录用于报错时告诉调用方
    「我试了哪些、为什么都不行」。
    """
    attempts: list[dict[str, Any]] = []
    for candidate in candidates(raw):
        target = Path(candidate)
        entry: dict[str, Any] = {"path": candidate}
        try:
            if not target.exists():
                entry["reason"] = "not_found"
                attempts.append(entry)
                continue
            entry.update(exists=True, is_dir=target.is_dir())
            attempts.append(entry)
            return candidate, attempts
        except OSError as exc:
            entry["reason"] = repr(exc)
            attempts.append(entry)
    return None, attempts
