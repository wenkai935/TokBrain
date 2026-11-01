from app.services.platforms.errors import PlatformPullError
from app.services.platforms.schemas import PlatformVideoItem
from app.services.platforms.cookies import (
    PLATFORM_AUTH_COOKIE_NAMES,
    PLATFORM_AUTH_DOMAINS,
    VALID_PLATFORMS,
    bound_douyin_cookies,
    bounded_platform_urls,
    cookies_to_header,
    filter_platform_cookie_names,
    has_platform_auth_cookie,
    normalize_bilibili_url,
    normalize_douyin_url,
    normalize_xhs_url,
    parse_cookie_header,
)
from app.services.platforms.bilibili import collect_bilibili
from app.services.platforms.xiaohongshu import collect_xiaohongshu
from app.services.platforms.douyin import collect_douyin, normalize_douyin_record

__all__ = [
    "PlatformPullError",
    "PlatformVideoItem",
    "PLATFORM_AUTH_COOKIE_NAMES",
    "PLATFORM_AUTH_DOMAINS",
    "VALID_PLATFORMS",
    "parse_cookie_header",
    "cookies_to_header",
    "has_platform_auth_cookie",
    "bound_douyin_cookies",
    "bounded_platform_urls",
    "filter_platform_cookie_names",
    "normalize_douyin_url",
    "normalize_bilibili_url",
    "normalize_xhs_url",
    "collect_bilibili",
    "collect_xiaohongshu",
    "collect_douyin",
    "normalize_douyin_record",
]
