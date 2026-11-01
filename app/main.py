"""Construct and run the loopback-only TokBrain HTTP application."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import DATA_DIR, ensure_directories, settings
from app.database import database, init_db
from app.routers_v2 import (
    cookies,
    health,
    imports,
    inbox,
    integrations,
    jobs,
    library,
    model_profiles,
    package_imports,
    platforms,
    sync,
)
from app.routers_v2 import settings as settings_routes
from app.services.import_queue import coordinator as import_coordinator
from app.services.jobs import coordinator as job_coordinator
from app.services.package_imports import coordinator as package_import_coordinator
from app.services.providers import close_provider_clients
from app.services.temp_files import cleanup_stale_temp_media


def _configure_safe_logging(sink=None) -> int:
    """Keep tracebacks useful without serializing credentials from local variables."""

    logger.remove()
    return logger.add(
        sink or sys.stderr,
        backtrace=False,
        diagnose=False,
    )


_configure_safe_logging()


APP_VERSION = "1.0.0"
API_CONTRACT_VERSION = 7
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]
_ROUTERS = (
    imports.router,
    integrations.router,
    inbox.router,
    package_imports.router,
    jobs.router,
    health.router,
    library.router,
    settings_routes.router,
    platforms.router,
    cookies.router,
    model_profiles.router,
    sync.router,
)


async def _remove_abandoned_media() -> None:
    removed = await asyncio.to_thread(cleanup_stale_temp_media, DATA_DIR / "tmp")
    if removed:
        logger.info("已清理 {} 个上次遗留的临时媒体文件", removed)


async def _stop_coordinator(name: str, coordinator: object) -> None:
    stop = coordinator.stop
    try:
        await asyncio.wait_for(stop(), timeout=45)
    except TimeoutError:
        logger.error("{} 协调器未能在 45 秒内停止，已继续执行关闭流程", name)
    except Exception:
        logger.exception("停止 {} 协调器时发生异常", name)


def _ffmpeg_bin_candidates() -> list[str]:
    """Ordered list of ffmpeg ``bin`` directories to probe, best first.

    No absolute path is hard-coded to this machine: the list is built from an
    explicit override, the checkout itself, and glob patterns over the places
    ffmpeg builds usually land. Missing entries are simply skipped.
    """
    import glob
    import os
    from pathlib import Path

    candidates: list[str] = []
    for variable in ("FFMPEG_BIN", "TOKBRAIN_FFMPEG_BIN", "FFMPEG_HOME"):
        value = os.environ.get(variable)
        if not value:
            continue
        entry = value.strip().strip('"')
        candidates.append(entry)
        # FFMPEG_HOME usually points one level above bin/.
        candidates.append(str(Path(entry) / "bin"))

    project_root = Path(__file__).resolve().parent.parent
    # FFMPEG_BIN in .env lands on the settings object, not on os.environ.
    configured = getattr(settings, "ffmpeg_bin", None)
    if configured:
        candidates.append(str(configured).strip().strip('"'))
    candidates.extend([
        str(project_root / "tools" / "ffmpeg" / "bin"),
        str(project_root / "ffmpeg" / "bin"),
    ])

    # Portable/unpacked builds: <anywhere>\ffmpeg*\bin, newest name first.
    patterns = [
        str(project_root.parent / "ffmpeg*" / "bin"),
        r"C:\ffmpeg\bin",
        r"C:\ffmpeg*\bin",
        r"D:\ffmpeg*\bin",
        r"D:\code\ffmpeg*\bin",
        r"D:\tools\ffmpeg*\bin",
        r"D:\tokbrain-release\ffmpeg\bin",
        r"D:\workbuddy\frames2\ffbin",
    ]
    for pattern in patterns:
        try:
            hits = sorted(glob.glob(pattern), reverse=True)
        except OSError:
            continue
        candidates.extend(hits)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        key = os.path.normcase(os.path.normpath(candidate))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _ensure_media_runtime_on_path() -> None:
    """Make ffmpeg/ffprobe resolvable via shutil.which regardless of how the
    process was launched.

    Some launchers (e.g. Git Bash) omit PATHEXT or mangle the Windows PATH with
    POSIX-style ':' separators, so shutil.which(\"ffmpeg\") cannot match
    ffmpeg.exe and the media_runtime health probe reports degraded even though
    ffmpeg is installed. Patch the process environment once at startup:
      - prepend the first discovered ffmpeg bin dir to PATH
      - export FFMPEG_BIN so child processes resolve the same binary
      - guarantee PATHEXT lists .EXE so shutil.which recognises ffmpeg.exe
    """
    import os

    path = os.environ.get("PATH", "")
    for cand in _ffmpeg_bin_candidates():
        if os.path.isfile(os.path.join(cand, "ffmpeg.exe")):
            if cand not in path:
                path = f"{cand};{path}"
            os.environ.setdefault("FFMPEG_BIN", cand)
            break
    os.environ["PATH"] = path

    pathext = os.environ.get("PATHEXT", "")
    if ".EXE" not in pathext.upper():
        os.environ["PATHEXT"] = (f"{pathext};" if pathext else "") + ".COM;.EXE;.BAT;.CMD"


async def _migrate_legacy_cookie_secrets() -> None:
    """把旧版 DPAPI SecretRecord 里的 Cookie 落成统一目录下的文件。

    只做一次性搬运：文件已存在就跳过。失败不阻断启动——Cookie 缺失只影响
    需要登录态的功能，不能让整个本地服务起不来。
    """

    from app.database import async_session_factory
    from app.services.cookie_store import migrate_legacy_secrets

    try:
        async with async_session_factory() as session:
            migrated = await migrate_legacy_secrets(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("旧版 Cookie 迁移跳过：{}", exc)
        return
    if migrated:
        logger.info("已把旧版 Cookie 迁入统一目录 data/cookies：{}", ", ".join(migrated))


@asynccontextmanager
async def _application_lifetime(_application: FastAPI) -> AsyncIterator[None]:
    started: list[tuple[str, object]] = []
    try:
        _ensure_media_runtime_on_path()
        ensure_directories()
        await _remove_abandoned_media()
        await init_db()
        await _migrate_legacy_cookie_secrets()
        await import_coordinator.start()
        started.append(("链接预检", import_coordinator))
        await package_import_coordinator.start()
        started.append(("数据包", package_import_coordinator))
        await job_coordinator.start()
        started.append(("任务", job_coordinator))
        logger.info("TokBrain 已启动；平台访问只会由用户明确操作触发")
        yield
    finally:
        for name, coordinator in reversed(started):
            await _stop_coordinator(name, coordinator)
        await close_provider_clients()
        await database.dispose()


async def _enforce_local_origin(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    origin = request.headers.get("origin")
    if (
        request.method not in _SAFE_METHODS
        and origin is not None
        and origin not in settings.allowed_origins
    ):
        return JSONResponse(
            content={"detail": "拒绝非本机页面发起的修改请求"},
            status_code=403,
        )
    return await call_next(request)


async def _service_index() -> dict[str, str]:
    return {"name": settings.app_name, "version": APP_VERSION, "docs": "/docs"}


async def _process_status() -> dict[str, str | int]:
    return {
        "status": "healthy",
        "scope": "process_only",
        "api_contract": API_CONTRACT_VERSION,
    }


async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    is_new_import_api = (
        request.url.path.startswith("/api/integrations/v1/")
        or request.url.path.startswith("/api/local-import-batches")
        or request.url.path.startswith("/api/package-import-batches")
        or (
            request.method == "PATCH"
            and request.url.path.startswith("/api/import-items/")
        )
    )
    if not is_new_import_api:
        return await request_validation_exception_handler(request, exc)
    first = exc.errors()[0] if exc.errors() else {}
    location = [
        str(part)
        for part in first.get("loc") or []
        if str(part) not in {"body", "path", "query", "header"}
    ]
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "invalid_request",
                "message": str(first.get("msg") or "请求参数无效"),
                "retryable": False,
                "field": ".".join(location) or None,
            }
        },
    )


def create_application() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        description=(
            "把用户主动提交的公开作品链接和有权处理的本地视频，"
            "转换为带来源的本地多模态知识库。"
        ),
        version=APP_VERSION,
        lifespan=_application_lifetime,
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=_LOCAL_HOSTS)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Requested-With",
        ],
    )
    application.middleware("http")(_enforce_local_origin)
    application.add_exception_handler(RequestValidationError, _validation_error)
    for router in _ROUTERS:
        application.include_router(router)
    application.add_api_route("/", _service_index, methods=["GET"])
    application.add_api_route("/health", _process_status, methods=["GET"])
    return application


app = create_application()


def run_local_server() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run_local_server()
