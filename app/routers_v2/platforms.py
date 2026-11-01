from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import database, get_db
from app.models import ImportBatch, ImportItem
from app.services import cookie_store
from app.services.doubaosave_bridge import (
    field_for_scope,
    read_latest_groups,
    read_latest_videos,
    records_to_items,
    run_doubaosave_collect,
    scope_supported,
)
from app.services.import_integrations import (
    IntegrationImportError,
    create_platform_import_batch,
    enqueue_platform_batch,
)
from app.services.platforms import (
    PLATFORM_AUTH_COOKIE_NAMES,
    VALID_PLATFORMS,
    collect_bilibili,
    collect_douyin,
    collect_xiaohongshu,
    has_platform_auth_cookie,
    parse_cookie_header,
    PlatformPullError,
)
from app.services.secrets import SecretUnavailableError, get_secret, set_secret

router = APIRouter(prefix="/api/platforms", tags=["platforms"])

# Which SecretRecord name stores the login cookie for each platform.
# Douyin now uses the zhicui handoff cookie (douyin_cookie); the legacy F2
# (f2_cookie) discovery path is deprecated — see app/services/f2_links.py.
SECRET_FOR_PLATFORM = {
    "douyin": "douyin_cookie",
    "bilibili": "bilibili_cookie",
    "xiaohongshu": "xhs_cookie",
}
# 抖音 favorite/post 端点还需要 sec_user_id（cookie 里不含），握手时一并存入。
SEC_USER_ID_FOR_PLATFORM = {
    "douyin": "douyin_sec_user_id",
}
EXPECTED_COOKIES = {p: sorted(PLATFORM_AUTH_COOKIE_NAMES[p]) for p in VALID_PLATFORMS}


class HandoffRequest(BaseModel):
    cookies: str = Field(min_length=1, max_length=20000)
    profile_key: Optional[str] = None
    sec_user_id: Optional[str] = Field(default=None, max_length=512)


class PullRequest(BaseModel):
    mode: str = Field(default="like", pattern=r"^(like|collect|post|favorite|history)$")
    limit: int = Field(default=50, ge=1, le=200)
    enqueue: bool = False
    profile_url: Optional[str] = None


class DomCollectRequest(BaseModel):
    scope: str = Field(pattern=r"^(fav|like|history|work)$")
    auto_import: bool = False


class SyncSelectedRequest(BaseModel):
    scope: str = Field(pattern=r"^(fav|like|history|work)$")
    aweme_ids: list[str] = Field(min_length=1, max_length=500)


# DOM 采集任务状态（内存字典，单机低频够用）。task_id -> 状态。
_dom_collect_tasks: dict[str, dict[str, Any]] = {}


@router.post("/{platform}/handoff")
async def handoff(
    platform: str, payload: HandoffRequest, session: AsyncSession = Depends(get_db)
):
    """Receive logged-in cookies from a desktop/helper (zhicui handoff contract).

    zhicui launches the user's real browser for login; the server cannot do that,
    so the cookie is handed off here, validated, and stored as a SecretRecord.
    """
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_platform", "message": "未知平台"}
        )
    cookies = parse_cookie_header(payload.cookies)
    if not has_platform_auth_cookie(platform, cookies):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "auth_cookie_missing",
                "message": f"未检测到 {platform} 的登录态 Cookie（需要：{EXPECTED_COOKIES[platform]}）",
            },
        )
    await set_secret(session, SECRET_FOR_PLATFORM[platform], payload.cookies.strip())
    if payload.sec_user_id and platform in SEC_USER_ID_FOR_PLATFORM:
        await set_secret(
            session, SEC_USER_ID_FOR_PLATFORM[platform], payload.sec_user_id.strip()
        )
    # 统一 Cookie 库：握手拿到的登录态同时落成 data/cookies/<platform>.txt，
    # 下载器 / yt-dlp / F2 / 采集器都从这一份取，用户不必再单独上传一次。
    file_saved, file_error = False, ""
    try:
        cookie_store.save_text(platform, payload.cookies.strip())
        file_saved = True
    except cookie_store.CookieStoreError as exc:
        file_error = exc.message
    await session.commit()
    return {
        "platform": platform,
        "stored": True,
        "cookie_count": len(cookies),
        "cookie_file_saved": file_saved,
        "cookie_file_error": file_error,
    }


@router.get("/{platform}/status")
async def platform_status(platform: str, session: AsyncSession = Depends(get_db)):
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_platform", "message": "未知平台"}
        )
    # 统一 Cookie 库优先；旧版 SecretRecord 仅作过渡兜底。
    file_status = cookie_store.status(platform)
    secret = await get_secret(session, SECRET_FOR_PLATFORM[platform])
    return {
        "platform": platform,
        "connected": bool(file_status["exists"] or secret),
        "source": "file" if file_status["exists"] else ("secret" if secret else "none"),
        "cookie_count": file_status["cookie_count"],
        "auth_ready": file_status["auth_ready"],
        "updated_at": file_status["updated_at"],
        "cookie_file": file_status["path"],
    }


@router.post("/{platform}/pull")
async def pull(
    platform: str, payload: PullRequest, session: AsyncSession = Depends(get_db)
):
    """Pull the user's videos for ``platform`` using the stored login cookie.

    Ports zhicui's collect logic (Bilibili Web API / Xiaohongshu URL extraction /
    Douyin Aweme normalization + recursive tree walk). All three platforms can be
    enqueued into the shared ingest -> parse -> library pipeline (the zhicui scheme
    now replaces F2 for Douyin discovery).
    """
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_platform", "message": "未知平台"}
        )
    # 统一 Cookie 库是唯一事实来源：文件优先，缺失时回退旧 SecretRecord。
    try:
        cookie_header = await cookie_store.cookie_header_for(session, platform)
    except SecretUnavailableError:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "cookie_unreadable",
                "message": "已保存的 Cookie 无法解密（可能换过 Windows 用户），"
                "请重新上传该平台的 cookies.txt",
            },
        )
    if not cookie_header:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "not_connected",
                "message": "未连接，请先上传该平台的 cookies.txt（设置 → Cookie 库）",
            },
        )
    try:
        if platform == "bilibili":
            items = await collect_bilibili(cookie_header, payload.mode, payload.limit)
        elif platform == "xiaohongshu":
            items = await collect_xiaohongshu(
                cookie_header, payload.mode, payload.limit, payload.profile_url
            )
        else:
            douyin_sec_user_id = await get_secret(
                session, SEC_USER_ID_FOR_PLATFORM.get(platform)
            )
            items = await collect_douyin(
                cookie_header, payload.mode, payload.limit, douyin_sec_user_id
            )
    except PlatformPullError as e:
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        )

    result: dict[str, Any] = {
        "platform": platform,
        "pulled_count": len(items),
        "items": [i.to_dict() for i in items],
    }
    if payload.enqueue and items:
        try:
            batch = await create_platform_import_batch(
                session, platform=platform, items=[i.to_dict() for i in items]
            )
        except IntegrationImportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
        queued = await enqueue_platform_batch(session, batch["id"])
        result["batch_id"] = batch["id"]
        # 只有抖音这类有链接解析器的平台才会真的进预检队列；B站/小红书直接落
        # needs_local_file（元数据入库、视频待补件），不能谎报"已入队"。
        result["enqueued"] = queued > 0
        result["queued_count"] = queued
    elif payload.enqueue:
        result["note"] = "没有拉取到可导入的视频"
        result["enqueued"] = False
    return result


@router.post("/douyin/dom-collect")
async def douyin_dom_collect(payload: DomCollectRequest):
    """DOM 路线采集抖音：后台跑 doubaosave（Playwright 弹浏览器登录）。

    默认只把数据拉到 douyin_data JSON（auto_import=false），随后由前端勾选视频、
    调 POST /douyin/sync-selected 建批入库；auto_import=true 时保持旧行为
    （采集完自动全量建批入队）。

    返回 task_id，前端轮询 GET /douyin/dom-collect/{task_id} 拿结果。
    """
    if not scope_supported(payload.scope):
        raise HTTPException(
            status_code=422,
            detail={"code": "scope_unsupported", "message": "该来源暂未接入 DOM 采集"},
        )
    task_id = str(uuid.uuid4())
    _dom_collect_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "message": "采集进行中，请在弹出的浏览器窗口完成登录（首次）",
        "scope": payload.scope,
        "pulled_count": 0,
        "batch_id": None,
    }
    asyncio.create_task(_run_dom_collect(task_id, payload.scope, payload.auto_import))
    return {"task_id": task_id, "status": "running"}


@router.get("/douyin/dom-collect/{task_id}")
async def douyin_dom_collect_status(task_id: str):
    task = _dom_collect_tasks.get(task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail={"code": "task_not_found", "message": "采集任务不存在或已过期"},
        )
    return task


@router.get("/douyin/last-batch")
async def douyin_last_platform_batch(session: AsyncSession = Depends(get_db)):
    """返回最近一次抖音 DOM 采集产生的 platform_pull 批次摘要。

    用于前端在刷新后或切换账号后找回上次采集的视频，避免重复拉取。
    """
    batch = (
        await session.execute(
            select(ImportBatch)
            .where(ImportBatch.source_type == "platform_pull")
            .order_by(ImportBatch.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if not batch:
        return None
    rows = (
        await session.execute(
            select(ImportItem.status, func.count())
            .where(ImportItem.batch_id == batch.id)
            .group_by(ImportItem.status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}
    return {
        "id": batch.id,
        "state": batch.state,
        "total_items": batch.total_items,
        "created_at": batch.created_at,
        "counts": counts,
    }


def _collected_video_preview(r: dict[str, Any]) -> dict[str, Any]:
    """doubaosave 记录（中文键）→ 前端预览卡片字段。"""
    aweme_id = str(r.get("aweme_id") or "")
    return {
        "aweme_id": aweme_id,
        "title": str(r.get("标题") or "").strip() or None,
        "author": str(r.get("作者") or "").strip() or None,
        "source_url": str(r.get("视频链接") or r.get("分享链接") or "").strip()
        or f"https://www.douyin.com/video/{aweme_id}",
        "published_at": str(r.get("发布时间") or "").strip() or None,
        "cover_url": None,
    }


@router.get("/douyin/collected")
async def douyin_collected(scope: str):
    """返回最近一次采集在指定 scope 下的视频预览，供前端分类展示与勾选。

    scope ∈ {fav, like, history, work}。fav 按收藏夹路径分组返回 groups，
    其余 scope 单组；items 始终为平铺（前 200），供无分组场景直接用。
    """
    if not field_for_scope(scope):
        raise HTTPException(
            status_code=422,
            detail={"code": "scope_unsupported", "message": "未知采集来源"},
        )
    records = read_latest_videos(scope)
    groups = [
        {
            "name": g["name"],
            "count": g["count"],
            "items": [_collected_video_preview(v) for v in g["videos"][:200]],
        }
        for g in read_latest_groups(scope)
    ]
    return {
        "scope": scope,
        "count": len(records),
        "groups": groups,
        "items": [_collected_video_preview(r) for r in records[:200]],
    }


@router.post("/douyin/sync-selected")
async def douyin_sync_selected(
    payload: SyncSelectedRequest, session: AsyncSession = Depends(get_db)
):
    """把用户在预览里勾选的视频建为一个小导入批次，进入解析/入库流程。

    与 dom-collect 的全量采集不同：这里从最近一次 doubaosave JSON 中按
    aweme_id 筛选出所选视频，只把这批建批次入队（不重新拉起浏览器）。
    """
    if not field_for_scope(payload.scope):
        raise HTTPException(
            status_code=422,
            detail={"code": "scope_unsupported", "message": "未知采集来源"},
        )
    wanted = {str(a).strip() for a in payload.aweme_ids if str(a).strip()}
    if not wanted:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_selection", "message": "请至少选择一个视频"},
        )
    records = read_latest_videos(payload.scope)
    chosen = [
        r for r in records if str(r.get("aweme_id") or "").strip() in wanted
    ]
    # 同一视频可能出现在多个收藏夹，read_latest_videos 展平后含重复记录；
    # 按 aweme_id 去重，避免勾选 ≤100 个视频却因跨夹重复膨胀超 100 而建批失败。
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in chosen:
        aid = str(r.get("aweme_id") or "").strip()
        if aid and aid not in seen:
            seen.add(aid)
            deduped.append(r)
    chosen = deduped
    if not chosen:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "nothing_matched",
                "message": "所选视频不在最新采集数据中，请先采集再同步",
            },
        )
    items = records_to_items(chosen, scope=payload.scope)
    try:
        batch = await create_platform_import_batch(
            session, platform="douyin", items=[i.to_dict() for i in items]
        )
    except IntegrationImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    queued = await enqueue_platform_batch(session, batch["id"])
    return {
        "batch_id": batch["id"],
        "count": len(items),
        "enqueued": queued > 0,
        "queued_count": queued,
        "message": (
            f"已将选中的 {len(items)} 条加入解析进度"
            if queued
            else f"已将选中的 {len(items)} 条入库（元数据），视频待补件"
        ),
    }


async def _run_dom_collect(task_id: str, scope: str, auto_import: bool) -> None:
    """后台任务：采集 → 读 JSON →（可选）自动建批导入。

    auto_import=false：只采集，前端勾选后由 sync-selected 决定入库范围。
    auto_import=true：保留旧行为，采集完全量建批入队。
    """
    task = _dom_collect_tasks[task_id]
    try:
        ok, message = await run_doubaosave_collect(scope)
        if not ok:
            task["status"] = "error"
            task["message"] = message
            return
        records = read_latest_videos(scope)
        if not records:
            task["status"] = "error"
            task["message"] = "采集完成但未解析到视频（可能未登录或收藏夹为空）"
            return
        task["pulled_count"] = len(records)
        if auto_import:
            items = records_to_items(records, scope=scope)
            if not items:
                task["status"] = "error"
                task["message"] = "采集完成但未解析到可导入的视频"
                return
            async with database.session() as session:
                try:
                    batch = await create_platform_import_batch(
                        session, platform="douyin", items=[i.to_dict() for i in items]
                    )
                    await enqueue_platform_batch(session, batch["id"])
                except IntegrationImportError as exc:
                    task["status"] = "error"
                    task["message"] = exc.message
                    return
            task["status"] = "done"
            task["message"] = f"已采集并导入 {len(items)} 条，进入导入进度"
            task["batch_id"] = batch["id"]
        else:
            task["status"] = "done"
            task["message"] = f"已采集 {len(records)} 条，请勾选后点「同步当前 N 条」入库"
    except Exception as exc:  # noqa: BLE001
        task["status"] = "error"
        task["message"] = f"采集/导入出错：{exc}"
