"""统一 Cookie 库：全项目只认一个存放位置。

为什么要这一层
--------------
以前每个消费者各自找 Cookie：F2 读 ``SecretRecord(f2_cookie)``、平台采集读
``SecretRecord(douyin_cookie)``、外部下载脚本读自己目录下的 ``cookies.txt``。
同一份登录态要贴三遍，换号或过期时必然漏改一处，而且失败现象各不相同
（有的报"未登录"，有的直接下到无音轨分片）。

现在约定：**``data/cookies/<platform>.txt`` 是唯一事实来源**。设置页负责上传，
其余模块只按自己需要的形态来取，不各自保存副本：

======================  ======================================  ==================
消费者                   需要的形态                               调什么
======================  ======================================  ==================
F2 / 平台采集 / 爬虫     ``Cookie`` 请求头字符串                    :func:`cookie_header`
requests / httpx        name→value 字典                          :func:`cookie_dict`
yt-dlp / curl           Netscape 文件路径                        :func:`cookie_file`
排查/验收               仅名字、条数、是否含登录态                 :func:`status`
======================  ======================================  ==================

上传格式无需自行转换：Netscape cookies.txt、Cookie-Editor / EditThisCookie 导出的
JSON、浏览器里复制的 ``a=1; b=2`` 请求头字符串都能解析，落盘统一写成 Netscape
——这样 yt-dlp 可以直接 ``--cookies`` 吃这个文件。

安全边界
--------
* 平台名走白名单，文件名由注册表派生，**不接受外部传入的路径**，杜绝路径穿越；
* 目录 0700、文件 0600，原子替换写入，不会写出半截文件；
* 接口只回名字与条数，**任何响应都不带 Cookie 值**；
* 单文件上限 1 MiB、条目上限 4000，防止误传大文件把服务拖死。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.config import DATA_DIR

__all__ = [
    "PLATFORMS",
    "CookieRecord",
    "CookiePlatform",
    "CookieStoreError",
    "cookie_root",
    "resolve_platform",
    "parse_cookie_text",
    "decode_bytes",
    "to_netscape",
    "save_text",
    "delete_cookies",
    "load_cookies",
    "cookie_header",
    "cookie_dict",
    "cookie_file",
    "status",
    "cookie_header_for",
    "migrate_legacy_secrets",
]

# 单份 cookies.txt 的上限：正常导出 200~300 行，1 MiB 足够宽松。
MAX_BYTES = 1 << 20
MAX_RECORDS = 4000
COOKIE_DIR_ENV = "TOKBRAIN_COOKIE_DIR"

# Cookie 名不允许出现分隔符，否则拼进 Header 会把后面的 Cookie 截断。
_NAME_RE = re.compile(r"^[^\s=;,\t\r\n]+$")

_NETSCAPE_HEADER = (
    "# Netscape HTTP Cookie File\n"
    "# 由 TokBrain 设置页统一管理，重新上传会整份覆盖本文件。\n"
    "# 手工编辑请保持 7 个 Tab 分隔字段：domain / subdomains / path / secure / expiry / name / value\n"
)


class CookieStoreError(ValueError):
    """Cookie 库的可直接展示给用户的错误。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class CookiePlatform:
    """一个平台的 Cookie 约定：放哪个文件、认哪些域、登录态靠哪几个名字。"""

    key: str
    label: str
    filename: str
    domains: tuple[str, ...]
    auth_names: frozenset[str]
    note: str


@dataclass(frozen=True)
class CookieRecord:
    """一条 Cookie。``domain`` 去掉了前导点，便于比较。"""

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    expires: int = 0
    http_only: bool = False

    def as_netscape(self) -> str:
        """写回 Netscape 行。

        ``http_only`` 必须还原成 ``#HttpOnly_`` 前缀——这不是注释标记，而是这一列
        的唯一载体；漏掉就等于把 HttpOnly 属性洗掉了，下次读回来会话态判据可能变。
        """

        domain = self.domain if self.domain.startswith(".") else f".{self.domain}"
        if self.http_only:
            domain = f"#HttpOnly_{domain}"
        return "\t".join(
            (
                domain,
                "TRUE",
                self.path or "/",
                "TRUE" if self.secure else "FALSE",
                str(int(self.expires or 0)),
                self.name,
                self.value,
            )
        )


# 新增平台只在这里加一行：文件名、域名过滤、登录态判据一次定义，
# 所有消费者自动可用（小红书 / B 站就是这么接进来的）。
PLATFORMS: dict[str, CookiePlatform] = {
    "douyin": CookiePlatform(
        key="douyin",
        label="抖音",
        filename="douyin.txt",
        domains=("douyin.com", "iesdouyin.com"),
        auth_names=frozenset({"sessionid", "sessionid_ss"}),
        note="登录 douyin.com 后导出；下载器、F2、DOM 采集都读这一份。",
    ),
    "bilibili": CookiePlatform(
        key="bilibili",
        label="B 站",
        filename="bilibili.txt",
        domains=("bilibili.com",),
        auth_names=frozenset({"SESSDATA", "DedeUserID"}),
        note="登录 bilibili.com 后导出；采集「我的视频/收藏」需要。",
    ),
    "xiaohongshu": CookiePlatform(
        key="xiaohongshu",
        label="小红书",
        filename="xiaohongshu.txt",
        domains=("xiaohongshu.com",),
        auth_names=frozenset({"web_session"}),
        note="登录 xiaohongshu.com 后导出；web_session 是登录态关键项。",
    ),
}

# 历史名字兼容：老代码里 f2/xhs 的叫法继续可用，但落到同一个文件。
PLATFORM_ALIASES = {
    "f2": "douyin",
    "xhs": "xiaohongshu",
    "b站": "bilibili",
    "xiaohongshu.com": "xiaohongshu",
}

# 旧版 DPAPI SecretRecord 名 → 平台。仅用于"文件缺失时兜底"和一次性迁移，
# 新数据一律只写文件，避免两份登录态互相打架。
LEGACY_SECRETS = {
    "douyin": ("douyin_cookie", "f2_cookie"),
    "bilibili": ("bilibili_cookie",),
    "xiaohongshu": ("xhs_cookie",),
}


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def cookie_root() -> Path:
    """Cookie 目录；可用 ``TOKBRAIN_COOKIE_DIR`` 指向别处（多机共享/测试用）。"""

    override = os.environ.get(COOKIE_DIR_ENV, "").strip()
    root = Path(override).expanduser() if override else DATA_DIR / "cookies"
    return root


def _ensure_root() -> Path:
    root = cookie_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def resolve_platform(platform: str) -> str:
    """别名归一 + 白名单校验；未知平台直接拒绝，绝不拼进路径。"""

    key = PLATFORM_ALIASES.get(str(platform or "").strip().lower(), str(platform or "").strip().lower())
    if key not in PLATFORMS:
        raise CookieStoreError(
            "unknown_platform",
            f"未知平台：{platform}（支持 {', '.join(PLATFORMS)}）",
            404,
        )
    return key


def cookie_path(platform: str) -> Path:
    spec = PLATFORMS[resolve_platform(platform)]
    return cookie_root() / spec.filename


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def _clean_domain(value: str) -> str:
    domain = str(value or "").strip().lower()
    if domain.startswith("#httponly_"):
        domain = domain[len("#httponly_") :]
    domain = domain.lstrip(".")
    if ":" in domain:  # 去掉端口
        domain = domain.split(":", 1)[0]
    return domain


def _domain_matches(domain: str, allowed: Sequence[str]) -> bool:
    """严格后缀匹配：``evildouyin.com`` 不能命中 ``douyin.com``。"""

    if not allowed:  # 没给域名（例如纯 Header 字符串）就不做过滤
        return True
    if not domain:
        return True
    return any(domain == item or domain.endswith(f".{item}") for item in allowed)


def _parse_netscape_line(line: str) -> CookieRecord | None:
    fields = line.split("\t")
    if len(fields) < 7:
        return None
    domain, _subdomains, path, secure, expiry, name, value = fields[:7]
    name = name.strip()
    if not name or not _NAME_RE.match(name):
        return None
    try:
        expires = int(float(expiry or 0))
    except (TypeError, ValueError):
        expires = 0
    return CookieRecord(
        name=name,
        value=value.strip(),
        domain=_clean_domain(domain),
        path=(path or "/").strip() or "/",
        secure=str(secure).strip().upper() in {"TRUE", "1"},
        expires=max(0, expires),
        http_only=str(domain).strip().lower().startswith("#httponly_"),
    )


def _records_from_json(payload: Any) -> list[CookieRecord]:
    if isinstance(payload, dict):
        if isinstance(payload.get("cookies"), list):
            payload = payload["cookies"]
        else:
            payload = [{"name": k, "value": v} for k, v in payload.items()]
    if not isinstance(payload, list):
        return []
    out: list[CookieRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            continue
        raw_expiry = item.get("expirationDate", item.get("expires", 0))
        try:
            expires = int(float(raw_expiry or 0))
        except (TypeError, ValueError):
            expires = 0
        out.append(
            CookieRecord(
                name=name,
                value=str(item.get("value") or ""),
                domain=_clean_domain(str(item.get("domain") or "")),
                path=str(item.get("path") or "/") or "/",
                secure=bool(item.get("secure", False)),
                expires=max(0, expires),
                http_only=bool(item.get("httpOnly", False)),
            )
        )
    return out


def _records_from_header(text: str) -> list[CookieRecord]:
    out: list[CookieRecord] = []
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if not name or not _NAME_RE.match(name):
            continue
        out.append(CookieRecord(name=name, value=value.strip().strip('"'), domain=""))
    return out


def parse_cookie_text(text: str, *, domains: Sequence[str] = ()) -> list[CookieRecord]:
    """把任意受支持格式解析成去重后的记录列表。

    顺序很重要：先判 JSON，再判 Netscape（靠 Tab + 7 字段），最后当 Header 串。
    ``#HttpOnly_`` 开头的行是**有效 Cookie**，不是注释——直接按 ``#`` 跳过会
    静默丢掉登录态，这是这份代码最容易踩的坑。
    """

    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return []

    sniff = raw.lstrip()
    records: list[CookieRecord] = []
    if sniff[:1] in {"[", "{"}:
        try:
            records = _records_from_json(json.loads(sniff))
        except (json.JSONDecodeError, TypeError, ValueError):
            records = []
    if not records:
        netscape: list[CookieRecord] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") and not stripped.lower().startswith("#httponly_"):
                continue
            record = _parse_netscape_line(line)
            if record:
                netscape.append(record)
        records = netscape
    if not records:
        records = _records_from_header(raw)

    # 无域名的（Header 串 / 部分 JSON 导出）补上平台主域，才能被下游按域复用。
    if domains:
        primary = domains[0]
        records = [
            record if record.domain else CookieRecord(**{**record.__dict__, "domain": primary})
            for record in records
        ]
        records = [record for record in records if _domain_matches(record.domain, domains)]

    return _dedupe(records, domains)


def _match_rank(domain: str, domains: Sequence[str]) -> int:
    """域越贴近主域越优先：精确主域 0，子域 1，其它 2。"""

    for index, primary in enumerate(domains):
        if domain == primary:
            return index
        if domain.endswith(f".{primary}"):
            return 10 + index
    return 99


def _dedupe(records: Iterable[CookieRecord], domains: Sequence[str]) -> list[CookieRecord]:
    """同名只留一条。

    同一份导出里 ``.douyin.com`` 与 ``.iesdouyin.com`` 常有同名 Cookie，两个都塞进
    Header 时服务端取到哪个不确定（可能是已过期那个），必须先去重。

    优先级统一成"越小越好"的键：域越贴近主域越优先，其次路径更长（更具体），
    再次过期时间更晚。注意不能比大小两次，方向会写反——这里只算一次。
    """

    def preference(record: CookieRecord) -> tuple[int, int, int]:
        return (
            _match_rank(record.domain, domains),
            -len(record.path or "/"),
            -record.expires,
        )

    best: dict[str, CookieRecord] = {}
    for record in records:
        current = best.get(record.name)
        if current is None or preference(record) < preference(current):
            best[record.name] = record
    return sorted(best.values(), key=lambda item: item.name)


# --------------------------------------------------------------------------- #
# 序列化与落盘
# --------------------------------------------------------------------------- #


def to_netscape(records: Iterable[CookieRecord]) -> str:
    body = "\n".join(record.as_netscape() for record in records)
    return f"{_NETSCAPE_HEADER}{body}\n" if body else _NETSCAPE_HEADER


def _decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def decode_bytes(data: bytes | str) -> str:
    """上传字节 → 文本（兼容 UTF-8 BOM 与 GBK 导出）。"""

    return _decode(data)


def _atomic_write(path: Path, text: str) -> None:
    _ensure_root()
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".cookie-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def save_text(platform: str, data: bytes | str) -> dict[str, Any]:
    """解析并整份覆盖保存，返回保存后的状态（不含任何 Cookie 值）。"""

    key = resolve_platform(platform)
    spec = PLATFORMS[key]
    text = _decode(data)
    if len(text.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise CookieStoreError(
            "cookie_file_too_large",
            f"Cookie 文件超过 {MAX_BYTES // 1024} KB 上限，请确认导出的是 cookies.txt 而不是整站备份",
            413,
        )
    records = parse_cookie_text(text, domains=spec.domains)
    if not records:
        raise CookieStoreError(
            "cookie_empty",
            "没有解析到任何该平台的 Cookie：请确认导出的是 cookies.txt（Netscape）或包含该平台域名的 JSON",
            422,
        )
    if len(records) > MAX_RECORDS:
        raise CookieStoreError(
            "cookie_too_many",
            f"Cookie 条数 {len(records)} 超过 {MAX_RECORDS} 上限，请只保留 {spec.label} 域名下的条目",
            413,
        )
    destination = cookie_path(key)
    _atomic_write(destination, to_netscape(records))
    return status(key)


def delete_cookies(platform: str) -> dict[str, Any]:
    key = resolve_platform(platform)
    path = cookie_path(key)
    removed = False
    if path.exists():
        path.unlink()
        removed = True
    payload = status(key)
    payload["removed"] = removed
    return payload


# --------------------------------------------------------------------------- #
# 读取：消费者只拿自己需要的那一形态
# --------------------------------------------------------------------------- #


def load_cookies(platform: str) -> list[CookieRecord]:
    """读取并解析该平台的 Cookie；文件不存在返回空列表（不抛异常）。"""

    key = resolve_platform(platform)
    path = cookie_path(key)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_cookie_text(text, domains=PLATFORMS[key].domains)


def cookie_header(platform: str) -> str:
    """``sessionid=...; ttwid=...``——F2、采集器、httpx 直接当 ``Cookie`` 头用。"""

    return "; ".join(
        f"{record.name}={record.value}" for record in load_cookies(platform)
    )


def cookie_dict(platform: str) -> dict[str, str]:
    """name→value；requests 习惯的形态。"""

    return {record.name: record.value for record in load_cookies(platform)}


def cookie_file(platform: str) -> Path:
    """Netscape 文件路径，给 yt-dlp ``--cookies`` / curl ``-b`` 直接用。

    文件不存在时也返回预期路径，调用方可用 :func:`status` 的 ``exists`` 先判断。
    """

    return cookie_path(platform)


def status(platform: str) -> dict[str, Any]:
    """仅描述性信息：文件名、条数、是否含登录态、更新时间、名字清单。"""

    key = resolve_platform(platform)
    spec = PLATFORMS[key]
    path = cookie_path(key)
    payload: dict[str, Any] = {
        "platform": key,
        "label": spec.label,
        "filename": spec.filename,
        "path": str(path),
        "note": spec.note,
        "exists": path.exists(),
        "cookie_count": 0,
        "auth_ready": False,
        "missing_auth": sorted(spec.auth_names),
        "names": [],
        "expired_count": 0,
        "size_bytes": 0,
        "updated_at": None,
    }
    if not path.exists():
        return payload
    try:
        stat = path.stat()
        payload["size_bytes"] = stat.st_size
        payload["updated_at"] = (
            datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        )
    except OSError:
        pass
    records = load_cookies(key)
    names = {record.name for record in records}
    now = int(time.time())
    payload.update(
        {
            "cookie_count": len(records),
            "auth_ready": bool(spec.auth_names & names),
            "missing_auth": sorted(spec.auth_names - names),
            "names": sorted(names)[:200],
            "expired_count": sum(
                1 for record in records if record.expires and record.expires < now
            ),
        }
    )
    return payload


def all_status() -> list[dict[str, Any]]:
    return [status(key) for key in PLATFORMS]


# --------------------------------------------------------------------------- #
# 兼容旧版 SecretRecord
# --------------------------------------------------------------------------- #


async def _legacy_secret_value(session: Any, platform: str) -> str:
    """读旧版 DPAPI SecretRecord。

    ``SecretUnavailableError`` **不在此处吞掉**：解不开（换 Windows 用户/重装系统）
    和"本来就没填"是两件不同的事，必须让调用方明确失败并提示重新上传，
    否则会静默降级成匿名请求，用户只会看到"登录态无效"这种误导性错误。
    """

    from app.services.secrets import get_secret

    for name in LEGACY_SECRETS.get(platform, ()):  # 顺序即优先级
        value = await get_secret(session, name)
        if value and value.strip():
            return value.strip()
    return ""


async def cookie_header_for(session: Any, platform: str) -> str:
    """给异步调用方用：**先读统一文件，文件缺失才回退旧 SecretRecord**。

    回退只是过渡期的安全带——老用户升级后不用重新上传；一旦上传过文件，
    文件即生效，旧 Secret 不再被读取。
    """

    key = resolve_platform(platform)
    header = cookie_header(key)
    if header:
        return header
    return await _legacy_secret_value(session, key)


async def migrate_legacy_secrets(session: Any) -> list[str]:
    """把旧 SecretRecord 里的 Cookie 一次性写成文件，避免两份登录态并存。

    已存在文件（哪怕内容为空）的平台不动：用户上传过的文件优先级最高。
    迁移失败只影响迁移本身，绝不阻断启动。
    """

    from app.services.secrets import SecretUnavailableError

    migrated: list[str] = []
    for key in PLATFORMS:
        path = cookie_path(key)
        if path.exists():
            continue
        try:
            value = await _legacy_secret_value(session, key)
            if not value:
                continue
            save_text(key, value)
        except (SecretUnavailableError, CookieStoreError, OSError):
            continue
        migrated.append(key)
    return migrated
