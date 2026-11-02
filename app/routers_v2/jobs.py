from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Job
from app.schemas import JobView
from app.services.jobs import cancel_job, job_to_dict, retry_failed_imports


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobView])
async def list_jobs(session: AsyncSession = Depends(get_db)):
    rows = (
        await session.execute(select(Job).where(
            func.coalesce(Job.progress["panel_hidden"].as_boolean(), False) == False
        ).order_by(desc(Job.created_at)).limit(50))
    ).scalars().all()
    return [job_to_dict(row) for row in rows]


@router.post("/retry", response_model=dict)
async def retry_failed_import_jobs(session: AsyncSession = Depends(get_db)):
    """一键重试所有失败的导入：把 processing_state=failed 的作品重新排入入库队列。"""
    return await retry_failed_imports(session)


@router.delete("/completed", response_model=dict)
async def clear_completed_jobs(session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(Job).where(
        Job.state.in_(["succeeded", "partial"]),
        func.coalesce(Job.progress["panel_hidden"].as_boolean(), False) == False,
    ))).scalars().all()
    # Jobs own import batches through cascading foreign keys; retain business data.
    for row in rows:
        row.progress = {**(row.progress or {}), "panel_hidden": True}
    await session.commit()
    return {"deleted": len(rows)}


@router.get("/{job_id}", response_model=JobView)
async def get_job(job_id: str, session: AsyncSession = Depends(get_db)):
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_to_dict(job)


@router.post("/{job_id}/cancel", response_model=JobView)
async def stop_job(job_id: str, session: AsyncSession = Depends(get_db)):
    try:
        return job_to_dict(await cancel_job(session, job_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
