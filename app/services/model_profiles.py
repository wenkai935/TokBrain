"""Runtime model profiles (multi-provider) stored in the app_settings KV table.

Each profile describes one provider endpoint plus the model names used for
each processing role.  API keys are stored separately through DPAPI secrets
under ``model_profile:{id}:api_key`` so profiles never carry plaintext secrets.

When no profile is activated the application keeps its legacy behaviour:
``DashScopeProvider`` with the deployment config model names and the
``dashscope_api_key`` secret.  Activating a custom profile switches all
consuming call sites to the profile's models/endpoint/key (profile key falls
back to the shared dashscope key for ``dashscope``-type profiles that did not
store their own key).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

PROFILES_KEY = "model_profiles"

# Roles whose model name a profile can override.
MODEL_ROLE_FIELDS = (
    "ocr_model",
    "asr_model",
    "embedding_model",
    "enrichment_model",
    "chat_model",
    "rerank_model",
)

_PROVIDER_TYPES = ("dashscope", "openai")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_profile() -> dict[str, Any]:
    """Legacy-equivalent profile mirroring deployment config defaults.

    Honors the deployment ``settings.provider_type`` so users who configured an
    OpenAI-compatible provider via ``.env`` keep exactly that behaviour while no
    explicit profile has been activated.
    """
    provider_type = getattr(settings, "provider_type", "dashscope") or "dashscope"
    ocr_model = settings.ocr_model
    asr_model = settings.asr_model
    enrichment_model = settings.enrichment_model
    embedding_model = (
        settings.provider_embedding_model
        if provider_type == "openai" and settings.provider_embedding_model
        else settings.embedding_model
    )
    chat_model = (
        settings.provider_chat_model
        if provider_type == "openai" and settings.provider_chat_model
        else settings.chat_model
    )
    base_url = (
        settings.provider_base_url
        if provider_type == "openai" and settings.provider_base_url
        else settings.dashscope_base_url
    )
    return {
        "id": "dashscope-default",
        "name": "内置默认（跟随 .env 配置）",
        "provider_type": provider_type,
        "base_url": base_url,
        "ocr_model": ocr_model,
        "asr_model": asr_model,
        "embedding_model": embedding_model,
        "enrichment_model": enrichment_model,
        "chat_model": chat_model,
        "rerank_model": settings.rerank_model,
        "api_key_ref": "dashscope_api_key",
        "builtin": True,
        "created_at": "",
    }


def secret_name_for(profile_id: str) -> str:
    return f"model_profile:{profile_id}:api_key"


async def _load(session: AsyncSession) -> dict[str, Any]:
    record = await session.get(AppSetting, PROFILES_KEY)
    value = record.value if record and isinstance(record.value, dict) else {}
    items = value.get("items") if isinstance(value.get("items"), list) else []
    return {"active_id": value.get("active_id") or "dashscope-default", "items": items}


async def _save(session: AsyncSession, active_id: str, items: list[dict[str, Any]]) -> None:
    record = await session.get(AppSetting, PROFILES_KEY)
    stored = {"active_id": active_id, "items": items}
    if record:
        record.value = stored
    else:
        session.add(AppSetting(key=PROFILES_KEY, value=stored))
    await session.flush()


def _public_view(profile: dict[str, Any]) -> dict[str, Any]:
    """Strip nothing (api keys are never stored on the profile dict)."""
    return dict(profile)


async def list_profiles(session: AsyncSession) -> dict[str, Any]:
    """Return ``{active_id, items}`` where items exclude any secret values."""
    data = await _load(session)
    return {
        "active_id": data["active_id"],
        "items": [_public_view(p) for p in data["items"]],
    }


def _defaults_for(provider_type: str) -> dict[str, Any]:
    if provider_type == "openai":
        return {
            "ocr_model": settings.ocr_model,
            "asr_model": settings.asr_model,
            "embedding_model": settings.embedding_model,
            "enrichment_model": settings.enrichment_model,
            "chat_model": settings.chat_model,
            "rerank_model": settings.rerank_model,
        }
    return {
        "ocr_model": settings.ocr_model,
        "asr_model": settings.asr_model,
        "embedding_model": settings.embedding_model,
        "enrichment_model": settings.enrichment_model,
        "chat_model": settings.chat_model,
        "rerank_model": settings.rerank_model,
    }


def build_profile(payload: dict[str, Any], *, profile_id: str) -> dict[str, Any]:
    """Normalise an incoming profile payload into a stored profile dict."""
    provider_type = str(payload.get("provider_type") or "dashscope").lower()
    if provider_type not in _PROVIDER_TYPES:
        provider_type = "dashscope"
    base = _defaults_for(provider_type)
    profile: dict[str, Any] = {
        "id": profile_id,
        "name": str(payload.get("name") or "").strip() or profile_id,
        "provider_type": provider_type,
        "base_url": (str(payload.get("base_url") or "").strip() or None),
        "api_key_ref": secret_name_for(profile_id),
        "builtin": False,
        "created_at": _now(),
    }
    for field in MODEL_ROLE_FIELDS:
        raw = payload.get(field)
        value = str(raw).strip() if raw not in (None, "") else base[field]
        profile[field] = value
    if provider_type == "dashscope":
        profile["base_url"] = settings.dashscope_base_url
    return profile


async def create_profile(
    session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    profile_id = str(uuid.uuid4())[:12]
    profile = build_profile(payload, profile_id=profile_id)
    data = await _load(session)
    data["items"].append(profile)
    await _save(session, data["active_id"], data["items"])
    return profile


async def update_profile(
    session: AsyncSession, profile_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    data = await _load(session)
    for profile in data["items"]:
        if profile["id"] == profile_id:
            profile.update(build_profile(payload, profile_id=profile_id))
            # Preserve non-editable bookkeeping from the original.
            profile["builtin"] = profile.get("builtin", False)
            await _save(session, data["active_id"], data["items"])
            return profile
    return None


async def delete_profile(
    session: AsyncSession, profile_id: str
) -> dict[str, Any] | None:
    data = await _load(session)
    kept = [p for p in data["items"] if p["id"] != profile_id]
    if len(kept) == len(data["items"]):
        return None
    if data["active_id"] == profile_id:
        data["active_id"] = "dashscope-default"
    await _save(session, data["active_id"], kept)
    return {"id": profile_id}


async def activate_profile(session: AsyncSession, profile_id: str) -> bool:
    """Set the active profile id.  Any id is accepted (default is implicit)."""
    if profile_id == "dashscope-default":
        data = await _load(session)
        await _save(session, "dashscope-default", data["items"])
        return True
    data = await _load(session)
    exists = any(p["id"] == profile_id for p in data["items"])
    if not exists:
        return False
    await _save(session, profile_id, data["items"])
    return True


async def get_active_profile(session: AsyncSession) -> dict[str, Any]:
    """Resolve the currently active profile; falls back to legacy defaults."""
    try:
        data = await _load(session)
    except (AttributeError, TypeError):
        # Sessions without AppSetting access (e.g. unit-test doubles) fall back
        # to the deployment-config defaults, keeping legacy behaviour intact.
        return default_profile()
    for profile in data["items"]:
        if profile["id"] == data["active_id"]:
            return profile
    return default_profile()


async def resolve_provider_kwargs(
    session: AsyncSession, api_key: str | None = None
) -> dict[str, Any]:
    """Build provider-construction kwargs from the active profile.

    Returns ``{"provider_type", "api_key", "base_url", "ocr_model", ...}`` that
    call sites can unpack when constructing a provider.  API key resolution:
    explicit ``api_key`` > profile secret > shared ``dashscope_api_key`` secret
    (legacy fallback, keeps the default profile working without its own key).
    """
    from app.services.secrets import get_secret

    profile = await get_active_profile(session)
    provider_type = str(profile.get("provider_type") or "dashscope")
    key = api_key or await get_secret(session, profile["api_key_ref"])
    if not key:
        key = await get_secret(session, "dashscope_api_key")
    kwargs: dict[str, Any] = {
        "provider_type": provider_type,
        "api_key": key,
        "base_url": profile.get("base_url") or None,
        "ocr_model": profile.get("ocr_model"),
        "asr_model": profile.get("asr_model"),
        "embedding_model": profile.get("embedding_model"),
        "enrichment_model": profile.get("enrichment_model"),
        "chat_model": profile.get("chat_model"),
    }
    return kwargs


async def dashscope_model_overrides(session: AsyncSession) -> dict[str, Any]:
    """Return DashScope model overrides when a DashScope-type custom profile is
    active; otherwise an empty dict (legacy behaviour with config defaults).

    The media-processing chains (OCR/ASR/keyframe/summary) always use the
    DashScope implementation, so an OpenAI-compatible profile does not apply
    here — only an active ``dashscope`` profile can retarget those models.
    """
    profile = await get_active_profile(session)
    if str(profile.get("provider_type") or "") != "dashscope":
        return {}
    if profile.get("builtin"):
        return {}
    return {
        "base_url": profile.get("base_url") or None,
        "ocr_model": profile.get("ocr_model") or None,
        "asr_model": profile.get("asr_model") or None,
        "enrichment_model": profile.get("enrichment_model") or None,
        "embedding_model": profile.get("embedding_model") or None,
        "chat_model": profile.get("chat_model") or None,
    }
