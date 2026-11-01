from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.f2_links import PublicLinkError, ensure_public_dns
from app.services.platforms.cookies import bounded_platform_urls, normalize_xhs_url
from app.services.platforms.errors import PlatformPullError
from app.services.platforms.schemas import PlatformVideoItem

# Ported from zhicui-desktop dist/platform-account.js :: collectXiaohongshu /
# normalizeXhsUrl / boundedPlatformUrls.
# zhicui scrapes the visible DOM in a real browser; server-side we fetch the page
# HTML with the login cookie and extract note-card links, then normalize them with
# the same URL shape (xsec_token / xsec_source preserved). URLs only (no metadata) —
# same limitation as zhicui's DOM scraper, and fragile to risk control.
XHS_NOTE_RE = re.compile(r"/(?:explore|discovery/item)/([A-Za-z0-9]+)", re.I)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_REDIRECTS = 5


def _validate_xhs_url(url: str) -> str:
    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise PlatformPullError("xhs_invalid_url", "小红书页面地址不合法", 400) from exc
    host = (parsed.hostname or "").lower().strip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not (host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"))
    ):
        raise PlatformPullError("xhs_invalid_url", "仅支持小红书官方 HTTPS 页面", 400)
    return urlunsplit(
        ("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )


async def collect_xiaohongshu(
    cookies_header: str,
    mode: str = "like",
    limit: int = 50,
    profile_url: Optional[str] = None,
    pasted_urls: Optional[List[str]] = None,
) -> List[PlatformVideoItem]:
    """Best-effort pull of Xiaohongshu note URLs for the logged-in user.

    Two inputs are honored:
    * ``pasted_urls`` — note links the user copied (the reliable server path).
    * ``profile_url`` — fetch the page HTML and extract note cards (best-effort).

    All URLs are normalized with zhicui's ``normalize_xhs_url`` and de-duplicated
    via ``bounded_platform_urls``.
    """
    limit = max(1, min(int(limit), 200))
    found: List[str] = list(pasted_urls or [])

    if not found and profile_url:
        headers = {
            "User-Agent": UA,
            "Cookie": cookies_header,
            "Referer": "https://www.xiaohongshu.com/",
        }
        try:
            current_url = _validate_xhs_url(profile_url)
            async with httpx.AsyncClient(
                headers=headers, timeout=20.0, follow_redirects=False
            ) as client:
                for redirect_index in range(MAX_REDIRECTS + 1):
                    try:
                        await ensure_public_dns(current_url)
                    except PublicLinkError as exc:
                        raise PlatformPullError(
                            "xhs_invalid_url", "小红书页面地址无法安全访问", 400
                        ) from exc
                    resp = await client.get(current_url)
                    if not resp.is_redirect:
                        resp.raise_for_status()
                        html = resp.text
                        break
                    if redirect_index >= MAX_REDIRECTS:
                        raise PlatformPullError(
                            "xhs_fetch_failed", "小红书页面重定向次数过多", 502
                        )
                    location = resp.headers.get("location")
                    if not location:
                        raise PlatformPullError(
                            "xhs_fetch_failed", "小红书页面返回了无效重定向", 502
                        )
                    current_url = _validate_xhs_url(urljoin(current_url, location))
        except PlatformPullError:
            raise
        except httpx.HTTPError as e:
            raise PlatformPullError("xhs_fetch_failed", f"小红书页面抓取失败: {e}", 502)
        found = [m.group(0) for m in XHS_NOTE_RE.finditer(html)]

    normalized = bounded_platform_urls("xiaohongshu", found, limit)
    return [
        PlatformVideoItem(platform="xiaohongshu", platform_work_id=_xhs_id(u), source_url=u)
        for u in normalized
    ]


def _xhs_id(url: str) -> str:
    m = XHS_NOTE_RE.search(url)
    return m.group(1) if m else url
