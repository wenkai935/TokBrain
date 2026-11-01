"""统一 Cookie 库的读写接口。

设置页只需要一个入口：上传 ``cookies.txt``。文件按域名拆进
``data/cookies/<platform>.txt``，之后 F2、平台采集、下载器、爬虫都从这里取，
不再各自要一份。

对外约定：
* 上传支持两种——**一份浏览器全量导出**（自动按平台拆分，最省事）或
  **指定平台单传**（精确覆盖）；
* 任何响应只回名字、条数、登录态是否齐，**不回 Cookie 值**；
* 下载接口回原始 Netscape 文件，方便核对"库里存的"与"喂给 yt-dlp 的"一致。

注意路由顺序：``/upload``、``/migrate-legacy`` 这类固定路径必须声明在
``/{platform}`` 之前，否则会被当作平台名吞掉。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import cookie_store
from app.services.cookie_store import CookieStoreError

router = APIRouter(prefix="/api/cookies", tags=["cookies"])

# 只接受这些后缀，避免把压缩包/图片误传进来后报错看不懂。
# 注意不能把空串塞进元组——``"x.zip".endswith("")`` 恒为真，校验会失效。
_ACCEPTED_SUFFIXES = (".txt", ".json", ".cookies")


def _suffix_ok(filename: str) -> bool:
    """无扩展名（部分客户端不传文件名时）放行，其余必须是我们认识的导出格式。"""

    if not filename or "." not in filename.rsplit("/", 1)[-1]:
        return True
    return filename.endswith(_ACCEPTED_SUFFIXES)


class CookieTextPayload(BaseModel):
    text: str = Field(min_length=1, max_length=cookie_store.MAX_BYTES)


def _raise(exc: CookieStoreError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _overview(results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "directory": str(cookie_store.cookie_root()),
        "platforms": results if results is not None else cookie_store.all_status(),
    }


async def _read_upload(file: UploadFile) -> bytes:
    filename = (file.filename or "").lower()
    if not _suffix_ok(filename):
        raise HTTPException(
            status_code=415,
            detail={
                "code": "cookie_file_type",
                "message": "请上传 .txt 或 .json 格式的 Cookie 导出文件",
            },
        )
    data = await file.read(cookie_store.MAX_BYTES + 1)
    await file.close()
    if len(data) > cookie_store.MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "cookie_file_too_large",
                "message": f"文件超过 {cookie_store.MAX_BYTES // 1024} KB，请确认导出的是 cookies.txt",
            },
        )
    return data


@router.get("")
async def list_cookies() -> dict[str, Any]:
    """所有平台的 Cookie 状态（含存放目录，前端直接展示给用户）。"""

    return _overview()


# --------------------------------------------------------------------------- #
# 固定路径的写接口（必须早于 /{platform}）
# --------------------------------------------------------------------------- #


@router.post("/upload")
async def upload_multi_platform_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """一份全量导出，自动按域名拆到各平台。

    浏览器/扩展导出的 cookies.txt 通常包含所有站点；这里按平台域名过滤后
    分别写入 ``douyin.txt`` / ``bilibili.txt`` / ``xiaohongshu.txt``，
    用户只需要上传一次。
    """

    data = await _read_upload(file)
    text = cookie_store.decode_bytes(data)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "cookie_empty", "message": "文件是空的，请重新导出"},
        )

    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    for key, spec in cookie_store.PLATFORMS.items():
        if not cookie_store.parse_cookie_text(text, domains=spec.domains):
            skipped.append(key)
            continue
        results.append(cookie_store.save_text(key, text))
    if not results:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "cookie_empty",
                "message": "文件里没有识别到抖音 / B 站 / 小红书的 Cookie，"
                "请确认导出的是浏览器 cookies.txt",
            },
        )
    return {
        "directory": str(cookie_store.cookie_root()),
        "platforms": results,
        "skipped": skipped,
    }


@router.post("/migrate-legacy")
async def migrate_legacy(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """把旧版加密保存的 Cookie 迁成文件（升级后无需重新上传）。"""

    migrated = await cookie_store.migrate_legacy_secrets(session)
    return {"migrated": migrated, **_overview()}


# --------------------------------------------------------------------------- #
# 单平台
# --------------------------------------------------------------------------- #


@router.get("/{platform}")
async def get_cookie_status(platform: str) -> dict[str, Any]:
    try:
        return cookie_store.status(platform)
    except CookieStoreError as exc:
        _raise(exc)


@router.post("/{platform}")
async def save_cookie_text(platform: str, payload: CookieTextPayload) -> dict[str, Any]:
    """粘贴保存：接受 Netscape / JSON / ``a=1; b=2`` 三种写法。"""

    try:
        return cookie_store.save_text(platform, payload.text)
    except CookieStoreError as exc:
        _raise(exc)


@router.post("/{platform}/upload")
async def upload_cookie_file(
    platform: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    """单平台上传：整份覆盖该平台的 ``<platform>.txt``。"""

    data = await _read_upload(file)
    try:
        return cookie_store.save_text(platform, data)
    except CookieStoreError as exc:
        _raise(exc)


@router.delete("/{platform}")
async def delete_cookie(platform: str) -> dict[str, Any]:
    try:
        return cookie_store.delete_cookies(platform)
    except CookieStoreError as exc:
        _raise(exc)


@router.get("/{platform}/download")
async def download_cookie(platform: str) -> FileResponse:
    """回原始文件：核对"库里存的"与"喂给 yt-dlp 的"是不是同一份。"""

    try:
        key = cookie_store.resolve_platform(platform)
    except CookieStoreError as exc:
        _raise(exc)
    path = cookie_store.cookie_file(key)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "cookie_missing", "message": "该平台还没有 Cookie 文件"},
        )
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=cookie_store.PLATFORMS[key].filename,
    )
