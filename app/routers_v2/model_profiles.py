"""Model-profile management endpoints (multi-provider model configuration).

Profiles let the user keep several named provider/model sets and switch the
active one without editing deployment config.  API keys are stored DPAPI-
encrypted under ``profile:{id}:api_key`` via the shared secrets service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import model_profiles
from app.services.secrets import get_secret, has_readable_secret, set_secret

router = APIRouter(prefix="/api/model-profiles", tags=["model-profiles"])


class ModelProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", min_length=0, max_length=80)
    provider_type: str = Field(default="dashscope", max_length=20)
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    ocr_model: str = Field(default="", max_length=200)
    asr_model: str = Field(default="", max_length=200)
    embedding_model: str = Field(default="", max_length=200)
    enrichment_model: str = Field(default="", max_length=200)
    chat_model: str = Field(default="", max_length=200)
    rerank_model: str = Field(default="", max_length=200)


class ModelProfileView(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str | None = None
    builtin: bool = False
    ocr_model: str
    asr_model: str
    embedding_model: str
    enrichment_model: str
    chat_model: str
    rerank_model: str
    has_api_key: bool = False
    is_active: bool = False
    created_at: str = ""


class ModelProfilesView(BaseModel):
    active_id: str
    items: list[ModelProfileView]
    default_profile: ModelProfileView


def _to_view(
    profile: dict[str, Any], *, active_id: str, has_key: bool
) -> ModelProfileView:
    return ModelProfileView(
        id=str(profile.get("id") or ""),
        name=str(profile.get("name") or profile.get("id") or ""),
        provider_type=str(profile.get("provider_type") or "dashscope"),
        base_url=(profile.get("base_url") or None),
        builtin=bool(profile.get("builtin")),
        ocr_model=str(profile.get("ocr_model") or ""),
        asr_model=str(profile.get("asr_model") or ""),
        embedding_model=str(profile.get("embedding_model") or ""),
        enrichment_model=str(profile.get("enrichment_model") or ""),
        chat_model=str(profile.get("chat_model") or ""),
        rerank_model=str(profile.get("rerank_model") or ""),
        has_api_key=has_key,
        is_active=str(profile.get("id")) == active_id,
        created_at=str(profile.get("created_at") or ""),
    )


async def _profiles_view(session: AsyncSession) -> ModelProfilesView:
    data = await model_profiles.list_profiles(session)
    active_id = data["active_id"]
    default = model_profiles.default_profile()
    items: list[ModelProfileView] = []
    for profile in data["items"]:
        items.append(
            _to_view(
                profile,
                active_id=active_id,
                has_key=await has_readable_secret(session, profile["api_key_ref"]),
            )
        )
    # If the active id does not belong to a saved profile, point it at default.
    if active_id != "dashscope-default" and not any(
        item.id == active_id for item in items
    ):
        active_id = "dashscope-default"
    return ModelProfilesView(
        active_id=active_id,
        items=items,
        default_profile=_to_view(
            default, active_id=active_id, has_key=False
        ),
    )


@router.get("", response_model=ModelProfilesView)
async def get_model_profiles(session: AsyncSession = Depends(get_db)):
    return await _profiles_view(session)


@router.post("", response_model=ModelProfilesView)
async def create_model_profile(
    payload: ModelProfilePayload, session: AsyncSession = Depends(get_db)
):
    profile = await model_profiles.create_profile(
        session, payload.model_dump()
    )
    if payload.api_key.strip():
        await set_secret(session, profile["api_key_ref"], payload.api_key.strip())
    await session.commit()
    return await _profiles_view(session)


@router.put("/{profile_id}", response_model=ModelProfilesView)
async def update_model_profile(
    profile_id: str,
    payload: ModelProfilePayload,
    session: AsyncSession = Depends(get_db),
):
    if profile_id == "dashscope-default":
        raise HTTPException(status_code=400, detail="内置默认配置不可编辑")
    updated = await model_profiles.update_profile(
        session, profile_id, payload.model_dump()
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    if payload.api_key.strip():
        await set_secret(session, updated["api_key_ref"], payload.api_key.strip())
    await session.commit()
    return await _profiles_view(session)


@router.delete("/{profile_id}", response_model=ModelProfilesView)
async def delete_model_profile(
    profile_id: str, session: AsyncSession = Depends(get_db)
):
    if profile_id == "dashscope-default":
        raise HTTPException(status_code=400, detail="内置默认配置不可删除")
    result = await model_profiles.delete_profile(session, profile_id)
    if result is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    # Best-effort cleanup of the DPAPI secret tied to the deleted profile.
    try:
        await set_secret(session, model_profiles.secret_name_for(profile_id), None)
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        pass
    await session.commit()
    return await _profiles_view(session)


@router.post("/{profile_id}/activate", response_model=ModelProfilesView)
async def activate_model_profile(
    profile_id: str, session: AsyncSession = Depends(get_db)
):
    ok = await model_profiles.activate_profile(session, profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="配置不存在")
    await session.commit()
    return await _profiles_view(session)
