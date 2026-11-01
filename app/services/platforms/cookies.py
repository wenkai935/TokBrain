from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

# Ported from zhicui-desktop dist/platform-account.js :: hasPlatformAuthCookie
PLATFORM_AUTH_COOKIE_NAMES: Dict[str, set] = {
    "bilibili": {"SESSDATA", "DedeUserID"},
    "douyin": {"sessionid", "sessionid_ss"},
    "xiaohongshu": {"web_session"},
}
PLATFORM_AUTH_DOMAINS = {
    "bilibili": "bilibili.com",
    "douyin": "douyin.com",
    "xiaohongshu": "xiaohongshu.com",
}
VALID_PLATFORMS = ("douyin", "bilibili", "xiaohongshu")

# Douyin cookie names worth persisting on handoff (zhicui dist/douyin-login.js)
DOUYIN_AUTH_COOKIE_NAMES = {
    "passport_auth_status",
    "passport_auth_status_ss",
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "uid_tt",
    "uid_tt_ss",
}
DOUYIN_COOKIE_NAME_PATTERN = r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$"


def parse_cookie_header(value: str) -> List[dict]:
    """Parse a ``Cookie`` header string into a list of ``{name, value, domain}`` dicts."""
    out: List[dict] = []
    for part in value.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, val = part.partition("=")
        out.append({"name": name.strip(), "value": val.strip(), "domain": None})
    return out


def cookies_to_header(cookies: Iterable[dict]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def has_platform_auth_cookie(platform: str, cookies: Iterable[dict]) -> bool:
    """Return True if the cookie set carries the auth cookies for ``platform``.

    Mirrors zhicui's ``hasPlatformAuthCookie``. Domain matching is relaxed here
    because pasted cookies from browser devtools rarely carry a domain field;
    the cookie *name* set is the reliable login-state signal.
    """
    expected = PLATFORM_AUTH_COOKIE_NAMES.get(platform)
    if not expected:
        return False
    names = {c.get("name") for c in cookies}
    return expected.issubset(names)


def filter_platform_cookie_names(platform: str, cookies: Iterable[dict]) -> List[dict]:
    """Keep only cookies whose name belongs to the platform's known auth set.

    Used when persisting a handoff so we don't store unrelated site cookies.
    """
    if platform == "douyin":
        allowed = DOUYIN_AUTH_COOKIE_NAMES
    else:
        allowed = PLATFORM_AUTH_COOKIE_NAMES.get(platform, set())
    return [c for c in cookies if c.get("name") in allowed]


# --- URL normalization (zhicui dist/platform-account.js) -------------------


def normalize_douyin_url(raw_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(raw_url if "://" in raw_url else f"https://www.douyin.com{raw_url}")
    except ValueError:
        return None
    if not parsed.hostname or not parsed.hostname.endswith("douyin.com"):
        return None
    match = re.match(r"^/video/(\d{5,32})/?$", parsed.path)
    return f"https://www.douyin.com/video/{match.group(1)}" if match else None


def normalize_bilibili_url(raw_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(raw_url if "://" in raw_url else f"https://www.bilibili.com{raw_url}")
    except ValueError:
        return None
    if not parsed.hostname or not parsed.hostname.endswith("bilibili.com"):
        return None
    match = re.search(r"/video/(BV[A-Za-z0-9]+|av\d+)", parsed.path)
    return f"https://www.bilibili.com/video/{match.group(1)}" if match else None


def normalize_xhs_url(raw_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(raw_url if "://" in raw_url else f"https://www.xiaohongshu.com{raw_url}")
    except ValueError:
        return None
    if not parsed.hostname or not parsed.hostname.endswith("xiaohongshu.com"):
        return None
    if not re.match(r"^/(explore|discovery/item|user/profile)/[A-Za-z0-9]", parsed.path):
        return None
    if parsed.query:
        q = parse_qs(parsed.query)
        params = {k: q[k][0] for k in ("xsec_token", "xsec_source") if k in q}
        if params:
            return f"https://www.xiaohongshu.com{parsed.path}?{urlencode(params)}"
    return f"https://www.xiaohongshu.com{parsed.path}"


def bounded_platform_urls(platform: str, values: Iterable[str], limit: int = 100) -> List[str]:
    """Deduplicate + normalize + cap, exactly like zhicui's boundedPlatformUrls."""
    bounded = max(1, min(100, int(limit) or 1))
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        normalizer = (
            normalize_bilibili_url
            if platform == "bilibili"
            else normalize_douyin_url
            if platform == "douyin"
            else normalize_xhs_url
        )
        normalized = normalizer(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= bounded:
            break
    return result


def bound_douyin_cookies(cookies: List[dict]) -> List[dict]:
    """Port of zhicui's boundDouyinCookies: keep only douyin auth cookies.

    Pasted devtools cookies carry no domain, so when a domain is absent we fall
    back to name-based filtering (same behaviour as has_platform_auth_cookie).
    """
    result: List[dict] = []
    total = 0
    for c in cookies:
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "")
        domain_ok = (
            not c.get("domain")
            or "douyin.com" in str(c.get("domain") or "").lower()
        )
        if (
            not name
            or not re.match(DOUYIN_COOKIE_NAME_PATTERN, name)
            or not value
            or not domain_ok
        ):
            continue
        if len(result) >= 96:
            break
        nbytes = len(name) + len(value)
        if total + nbytes > 64 * 1024:
            break
        result.append({"name": name, "value": value, "domain": c.get("domain")})
        total += nbytes
    return result

