"""飞书同步 / Obsidian 连接状态接口。

GET  /api/sync/overview
    每次调用都会真实查询一次飞书表（用户所选方案 A），并把查询时间落盘
    data/feishu_last_query.json；返回 cli 授权状态、Obsidian Vault 可写状态、
    前端默认轮询间隔。供前端左下角状态面板定时刷新（默认 10min）。

POST /api/sync/feishu/relogin
    一键续期：先重测授权（多数红点是缓存或 token 已被 CLI 自动刷新，重测即好），
    仍失效才发起 lark-cli device flow 并返回授权链接 / 二维码。

GET  /api/sync/feishu/relogin
    查询续期流程进度（前端每 2s 轮询；CLI 授权成功后会自动落到 success）。

GET  /api/sync/discovery
    飞书发现批次：bridge 静默补齐期间的条目对前端不可见（state=filling），
    补齐全部落终态后一次性返回清单，保证用户看到的数字不再跳动。

POST /api/sync/discovery/confirm
    用户点了「解析」：逐批 confirm + 建 ingest job，并把 dispatch 推进 ingested。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.discovery import approve_discovery, discovery_view
from app.services.feishu_auth import login_state, start_login
from app.services.sync_status import cli_state, overview

router = APIRouter(prefix="/api/sync", tags=["sync"])


class DiscoveryConfirm(BaseModel):
    item_ids: list[int] = Field(default_factory=list)


@router.get("/overview")
def get_sync_overview() -> dict:
    # 同步（阻塞）实现：lark-cli 子进程会阻塞事件循环，故用 def 而非 async def，
    # FastAPI 会自动放进线程池执行，不影响其它请求。
    return overview()


@router.get("/discovery")
async def get_discovery(session: AsyncSession = Depends(get_db)) -> dict:
    return await discovery_view(session)


@router.post("/discovery/confirm")
async def post_discovery_confirm(
    payload: DiscoveryConfirm, session: AsyncSession = Depends(get_db)
) -> dict:
    try:
        return await approve_discovery(session, item_ids=payload.item_ids)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/feishu/relogin")
def post_feishu_relogin() -> dict:
    # 同样用 def：内部会跑 lark-cli 子进程，交给线程池
    current = cli_state()
    if current.get("connected"):
        return {
            "state": "success",
            "url": "",
            "user_code": "",
            "qrcode_b64": "",
            "message": "授权仍然有效，已重新检测通过",
            "started_at": "",
            "finished_at": "",
        }
    return start_login()


@router.get("/feishu/relogin")
def get_feishu_relogin() -> dict:
    return login_state()
