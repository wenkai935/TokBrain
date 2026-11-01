from __future__ import annotations

import random
import string
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.services.platforms.errors import PlatformPullError
from app.services.platforms.schemas import PlatformVideoItem

# Ported from zhicui-desktop dist/platform-account.js
# (normalizeDouyinRecord / collectDouyinRecords / mergeDouyinItem).
# Replaces TokBrain's F2-based Douyin discovery (app.services.f2_links) with the
# zhicui approach: pull the user's aweme payload with the login cookie and walk
# the JSON tree to normalize every video record.

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
AWEME_FAVORITE = "https://www.douyin.com/aweme/v1/web/aweme/favorite/"
AWEME_POST = "https://www.douyin.com/aweme/v1/web/aweme/post/"
# 收藏夹真实接口（doubaosave 已验证路径）：/collects/list 拉收藏夹列表、
# /collects/video/list 拉夹内视频。二者用登录 Cookie 识别当前用户，不需要 sec_user_id。
COLLECTS_LIST = "https://www.douyin.com/aweme/v1/web/collects/list/"
COLLECTS_VIDEO = "https://www.douyin.com/aweme/v1/web/collects/video/list/"


def _record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_url(node: Any) -> Optional[str]:
    if node is None:
        return None
    if isinstance(node, str):
        raw = node.strip()
        if raw.startswith("https://") or raw.startswith("http://"):
            return raw
        if raw.startswith("//"):
            return f"https:{raw}"
        return None
    if isinstance(node, dict):
        payload = node
        for candidate in list(payload.get("url_list") or []):
            url = _first_url(candidate)
            if url:
                return url
        for key in ("url", "uri"):
            value = payload.get(key)
            if isinstance(value, (str, dict)):
                url = _first_url(value)
                if url:
                    return url
        return None
    return None


def _first_douyin_media_url(video: Dict[str, Any]) -> str:
    for candidate in (video.get("play_addr"), video.get("play_addr_h264"), video.get("download_addr")):
        url = _first_url(candidate)
        if url:
            return url
    for bitrate in list(video.get("bit_rate", [])):
        url = _first_url(_record(bitrate).get("play_addr"))
        if url:
            return url
    return ""


def _douyin_item_quality(item: Dict[str, Any]) -> int:
    return (
        (16 if item.get("ephemeralMediaUrl") else 0)
        + (8 if item.get("authorName") else 0)
        + (4 if item.get("coverUrl") else 0)
        + (4 if item.get("publishedAt") else 0)
        + (4 if (item.get("durationSeconds") or 0) > 0 else 0)
        + (2 if item.get("caption") and item.get("caption") != "抖音作品" else 0)
        + (1 if item.get("title") and item.get("title") != "抖音作品" else 0)
    )


def normalize_douyin_record(value: Dict[str, Any], source_rank: int = 0) -> Optional[PlatformVideoItem]:
    """Faithful port of zhicui's normalizeDouyinRecord."""
    payload = _record(value)
    video_id = _first_text(payload.get("aweme_id"), payload.get("awemeId"), payload.get("item_id"))
    if not video_id or not video_id.isdigit() or not (5 <= len(video_id) <= 32):
        return None
    source_url = f"https://www.douyin.com/video/{video_id}"
    author = _record(payload.get("author"))
    video = _record(payload.get("video"))
    share = _record(payload.get("share_info"))
    caption = _first_text(payload.get("desc"), payload.get("caption"))[:20000]
    title = _first_text(payload.get("title"), share.get("share_title"), caption, "抖音作品")[:500]
    created_at = payload.get("create_time") or payload.get("createTime")
    raw_duration = video.get("duration") or payload.get("duration")
    duration_seconds = None
    if raw_duration:
        dur = float(raw_duration)
        duration_seconds = round(dur / 1000.0) if dur > 1000 else round(dur)
    published_at = None
    if created_at:
        try:
            published_at = datetime.fromtimestamp(int(created_at), tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            published_at = None
    _cover = _first_url(
        video.get("cover") or video.get("origin_cover") or video.get("dynamic_cover") or payload.get("cover")
    )
    return PlatformVideoItem(
        platform="douyin",
        platform_work_id=video_id,
        source_url=source_url,
        title=title,
        author_name=_first_text(author.get("nickname"), author.get("unique_id"), payload.get("author_name"))[:200],
        cover_url=_cover[:2048] if _cover else None,
        published_at=published_at,
        duration_seconds=duration_seconds,
        kind="image" if (payload.get("images") or payload.get("image_infos")) else "video",
        raw={
            "ephemeral_media_url": _first_douyin_media_url(video)[:8192] or None,
            "source_rank": source_rank,
        },
    )


def _flat_douyin_to_item(flat: Dict[str, Any], rank: int = 0) -> PlatformVideoItem:
    """把 _collect_douyin_records 累积的扁平 dict 转回 PlatformVideoItem。

    收集阶段为了去重把 aweme 压成 videoId/title 等驼峰键的扁平结构，
    最后一步必须用这里转换，而不是 normalize_douyin_record（它读的是原始 aweme 字段）。
    """
    return PlatformVideoItem(
        platform="douyin",
        platform_work_id=str(flat.get("videoId") or ""),
        source_url=flat.get("sourceUrl") or f"https://www.douyin.com/video/{flat.get('videoId')}",
        title=flat.get("title"),
        author_name=flat.get("authorName"),
        cover_url=flat.get("coverUrl"),
        published_at=flat.get("publishedAt"),
        duration_seconds=flat.get("durationSeconds"),
        kind=flat.get("kind") or "video",
        raw={
            "ephemeral_media_url": flat.get("ephemeralMediaUrl"),
            "source_rank": flat.get("sourceRank", rank),
        },
    )


def _merge_douyin_item(target: Dict[str, Any], item: Dict[str, Any], limit: int) -> None:
    existing = target.get(item["videoId"])
    if not existing:
        if len(target) < limit:
            target[item["videoId"]] = item
        return
    merged = {
        **existing,
        **item,
        "title": item["title"] if item.get("title") and item["title"] != "抖音作品" else existing["title"],
        "caption": item.get("caption") or existing.get("caption"),
        "authorName": item.get("authorName") or existing.get("authorName"),
        "coverUrl": item.get("coverUrl") or existing.get("coverUrl"),
        "publishedAt": item.get("publishedAt") or existing.get("publishedAt"),
        "durationSeconds": item["durationSeconds"] if (item.get("durationSeconds") or 0) > 0 else existing.get("durationSeconds"),
        "ephemeralMediaUrl": item.get("ephemeralMediaUrl") or existing.get("ephemeralMediaUrl"),
        "sourceRank": existing.get("sourceRank"),
    }
    if _douyin_item_quality(merged) > _douyin_item_quality(existing):
        target[item["videoId"]] = merged


def _collect_douyin_records(value: Any, target: Dict[str, Any], limit: int, depth: int = 0) -> None:
    """Recursive tree walk (zhicui collectDouyinRecords): surface every aweme object."""
    if depth > 7:
        return
    if isinstance(value, list):
        for entry in value[:500]:
            _collect_douyin_records(entry, target, limit, depth + 1)
        return
    payload = _record(value)
    if not payload:
        return
    item = normalize_douyin_record(payload, len(target))
    if item:
        _merge_douyin_item(
            target,
            {
                "videoId": item.platform_work_id,
                "sourceUrl": item.source_url,
                "title": item.title,
                "caption": item.raw.get("caption") if isinstance(item.raw, dict) else None,
                "authorName": item.author_name,
                "coverUrl": item.cover_url,
                "publishedAt": item.published_at,
                "durationSeconds": item.duration_seconds,
                "kind": item.kind,
                "ephemeralMediaUrl": (item.raw or {}).get("ephemeral_media_url"),
                "sourceRank": item.raw.get("source_rank") if isinstance(item.raw, dict) else 0,
            },
            limit,
        )
    for entry in payload.values():
        if isinstance(entry, (dict, list)):
            _collect_douyin_records(entry, target, limit, depth + 1)


_F2_VENDOR_DIR = Path(__file__).resolve().parents[3] / ".vendor"


def _abogus_class():
    """Load F2's ABogus signer, ensuring .vendor is on sys.path (ponytail: reuse F2)."""
    vendor = str(_F2_VENDOR_DIR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from f2.utils.abogus import ABogus  # noqa: PLC0415

    return ABogus


def _random_ms_token() -> str:
    # 与 F2 gen_false_msToken 一致：126 位随机 + "=="（抖音 web 端对 msToken 校验宽松）
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=126)) + "=="


def _random_webid() -> str:
    return "".join(random.choices(string.digits, k=19))


def _douyin_query_params(extra: Dict[str, Any]) -> str:
    """Build a signed Douyin Web API query string (通用参数 + a_bogus)。

    复用 F2 的 ABogus 签名；算法随抖音改版失效时回退为无签名查询串，
    由风控兜底报错。extra 为端点特有参数（如 sec_user_id / collects_id / cursor）。
    """
    base = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "version_code": "190500",
        "version_name": "19.5.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": 10,
        "effective_type": "4g",
        "round_trip_time": 100,
        "webid": _random_webid(),
        "msToken": _random_ms_token(),
    }
    base.update(extra)
    param_str = "&".join(f"{k}={v}" for k, v in base.items())
    try:
        abogus = _abogus_class()(user_agent=UA)
        return abogus.generate_abogus(param_str, "GET")[0]
    except Exception:
        return param_str


def _collects_list_entries(data: Any) -> List[Dict[str, Any]]:
    """从 collects/list 响应里取出收藏夹列表（兼容顶层与 data 包裹两种结构）。"""
    if not isinstance(data, dict):
        return []
    payload = data.get("data", data) if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return []
    entries = payload.get("collects_list") or payload.get("collects") or []
    return entries if isinstance(entries, list) else []


def _walk_collect_folders(entries: List[Dict[str, Any]], folders: List[str], depth: int = 0) -> None:
    """递归展开收藏夹层级树为 collects_id 列表（含子级收藏夹）。"""
    if depth > 7:
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fid = entry.get("collects_id") or entry.get("id") or entry.get("cid") or entry.get("fid")
        if fid:
            folders.append(str(fid))
        kids = entry.get("children") or entry.get("sub_collects") or entry.get("collection_list") or []
        if isinstance(kids, list):
            _walk_collect_folders(kids, folders, depth + 1)


async def _collect_douyin_folders(client: httpx.AsyncClient, limit: int) -> List[PlatformVideoItem]:
    """collect 模式：拉收藏夹列表 → 逐夹分页拉视频 → 规范化去重。"""
    url = f"{COLLECTS_LIST}?{_douyin_query_params({'count': 50, 'cursor': 0})}"
    data = (await client.get(url)).json()
    if not isinstance(data, dict) or data.get("status_code", 0) != 0:
        raise PlatformPullError(
            "douyin_blocked",
            f"抖音收藏夹列表接口拒绝：{data.get('status_msg') if isinstance(data, dict) else ''}",
            502,
        )
    folders: List[str] = []
    _walk_collect_folders(_collects_list_entries(data), folders)
    if not folders:
        return []

    target: Dict[str, Any] = {}
    for fid in folders:
        if len(target) >= limit:
            break
        cursor = 0
        while len(target) < limit:
            query = _douyin_query_params({"collects_id": fid, "cursor": cursor, "count": 20})
            resp_data = (await client.get(f"{COLLECTS_VIDEO}?{query}")).json()
            if not isinstance(resp_data, dict):
                break
            aweme_list = resp_data.get("aweme_list") or []
            for entry in aweme_list:
                _collect_douyin_records(entry, target, limit)
            if not resp_data.get("has_more"):
                break
            nxt = resp_data.get("cursor")
            if nxt is None:
                break
            cursor = int(nxt)
    items = [_flat_douyin_to_item(payload, i) for i, payload in enumerate(target.values())]
    return items[:limit]


async def collect_douyin(
    cookies_header: str,
    mode: str = "favorite",
    limit: int = 50,
    sec_user_id: Optional[str] = None,
) -> List[PlatformVideoItem]:
    """Pull the user's Douyin videos via the Web API.

    mode 语义：
    * ``collect`` — 收藏夹（collects/list + collects/video/list，用登录 Cookie 识别
      当前用户，无需 sec_user_id）。
    * ``favorite`` / ``like`` — 喜欢（aweme/favorite）。
    * ``post`` — 作品（aweme/post）。

    favorite/post 端点需要 sec_user_id（cookie 里不含）。所有端点都复用 F2 的
    ABogus 签名，算法随抖音改版失效时回退为无签名请求、由风控报错兜底。
    """
    limit = max(1, min(int(limit), 200))

    headers = {
        "User-Agent": UA,
        "Cookie": cookies_header,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=20.0, follow_redirects=True
        ) as client:
            # collect（收藏夹）：用登录 Cookie 识别当前用户，无需 sec_user_id。
            if mode == "collect":
                return await _collect_douyin_folders(client, limit)

            # favorite / post（喜欢 / 作品）：端点必须指定目标用户；cookie 只证明
            # 登录态，不含 sec_user_id。
            if not sec_user_id:
                raise PlatformPullError(
                    "douyin_sec_user_id_required",
                    "抖音「喜欢/作品」需提供主页 sec_user_id（进入抖音主页，地址栏 user/ 后的字符串），请在「粘贴 Cookie 连接」时一并填入",
                    422,
                )
            endpoint = AWEME_POST if mode == "post" else AWEME_FAVORITE
            url = f"{endpoint}?{_douyin_query_params({'sec_user_id': sec_user_id, 'count': min(limit, 20), 'max_cursor': 0})}"
            resp = await client.get(url)
            try:
                data = resp.json()
            except Exception:
                data = None
    except PlatformPullError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PlatformPullError(
            "douyin_risk",
            f"抖音接口未返回可用数据（可能触发风控或需签名）: {e}",
            502,
        )
    if not isinstance(data, dict):
        raise PlatformPullError(
            "douyin_blocked", "抖音接口返回非 JSON（可能触发风控），请用 zhicui 桌面端重新登录后重试", 502
        )
    if data.get("status_code", 0) != 0:
        raise PlatformPullError(
            "douyin_blocked", f"抖音接口拒绝：{data.get('status_msg')}", 502
        )
    target: Dict[str, Any] = {}
    _collect_douyin_records(data, target, limit)
    # Also accept a top-level aweme_list for forward-compat.
    for entry in list(data.get("aweme_list", [])):
        _collect_douyin_records(entry, target, limit)
    items = [_flat_douyin_to_item(payload, i) for i, payload in enumerate(target.values())]
    return items[:limit]
