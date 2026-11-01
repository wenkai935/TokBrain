from __future__ import annotations

from typing import List

import httpx

from app.services.platforms.cookies import cookies_to_header
from app.services.platforms.errors import PlatformPullError
from app.services.platforms.schemas import PlatformVideoItem

# Ported from zhicui-desktop dist/platform-account.js :: collectBilibili
BILI_NAV = "https://api.bilibili.com/x/web-interface/nav"
BILI_LIKE = "https://api.bilibili.com/x/space/like/video"
BILI_FAV_LIST = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
BILI_FAV_RES = "https://api.bilibili.com/x/v3/fav/resource/list"
UA = "Zhicui-Desktop/1.0"
MAX_FOLDERS = 20


async def collect_bilibili(
    cookies_header: str, mode: str = "like", limit: int = 50
) -> List[PlatformVideoItem]:
    """Pull the user's liked/collected Bilibili videos using the logged-in cookie.

    Faithful port of zhicui's ``collectBilibili``: official Web APIs, ``Referer``
    header, ``code === 0`` check, pagination. Returns normalized video items.
    """
    limit = max(1, min(int(limit), 200))
    headers = {
        "User-Agent": UA,
        "Referer": "https://space.bilibili.com/",
        "Cookie": cookies_header,
    }
    items: List[PlatformVideoItem] = []
    async with httpx.AsyncClient(
        headers=headers, timeout=20.0, follow_redirects=True
    ) as client:
        nav = (await client.get(BILI_NAV)).json()
        if nav.get("code") != 0:
            raise PlatformPullError(
                "bili_unauthorized", "B站 Cookie 无效或已过期（nav 接口返回非 0）", 401
            )
        mid = nav["data"]["mid"]

        if mode == "collect":
            folders = (await client.get(BILI_FAV_LIST, params={"up_mid": mid})).json()
            # zhicui: media_id is the field used to list resources; fid/id may be
            # 0 for the default folder, so never skip on fid == 0.
            folder_ids = [
                (f.get("media_id") or f.get("id") or f.get("fid"))
                for f in (folders.get("data") or {}).get("list") or []
                if (f.get("media_id") or f.get("id") or f.get("fid"))
            ][:MAX_FOLDERS]
            for fid in folder_ids:
                if len(items) >= limit:
                    break
                pn = 1
                while len(items) < limit:
                    resp = (
                        await client.get(
                            BILI_FAV_RES,
                            params={
                                "media_id": fid,
                                "pn": pn,
                                "ps": 20,
                                "order": "mtime",
                                "type": 0,
                                "tid": 0,
                                "platform": "web",
                            },
                        )
                    ).json()
                    medias = (resp.get("data") or {}).get("medias") or []
                    if not medias:
                        break
                    for m in medias:
                        bvid = m.get("bvid") or m.get("id")
                        if not bvid:
                            continue
                        items.append(_bili_item(m, bvid))
                        if len(items) >= limit:
                            break
                    if not (resp.get("data") or {}).get("has_more"):
                        break
                    pn += 1
        else:  # like (default)
            pn = 1
            while len(items) < limit:
                resp = (
                    await client.get(
                        BILI_LIKE, params={"vmid": mid, "pn": pn, "ps": 50}
                    )
                ).json()
                if resp.get("code") != 0:
                    raise PlatformPullError(
                        "bili_like_failed", f"B站喜欢列表接口错误: {resp.get('message')}", 502
                    )
                vlist = (resp.get("data") or {}).get("list") or {}
                vlist = vlist.get("vlist", []) if isinstance(vlist, dict) else vlist
                if not vlist:
                    break
                for v in vlist:
                    bvid = v.get("bvid")
                    if not bvid:
                        continue
                    items.append(_bili_item(v, bvid))
                    if len(items) >= limit:
                        break
                if not vlist:
                    break
                pn += 1
    return items[:limit]


def _bili_item(v: dict, bvid: str) -> PlatformVideoItem:
    return PlatformVideoItem(
        platform="bilibili",
        platform_work_id=bvid,
        source_url=f"https://www.bilibili.com/video/{bvid}",
        title=v.get("title"),
        author_name=v.get("author"),
        cover_url=v.get("pic"),
        published_at=str(v["created"]) if v.get("created") else None,
        raw=v,
    )
