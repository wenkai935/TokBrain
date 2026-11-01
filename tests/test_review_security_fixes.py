import httpx
import pytest

import app.services.platforms.xiaohongshu as xhs
import app.services.content_pipeline as content_pipeline
from app.services.platforms.errors import PlatformPullError
from app.services.f2_links import PublicLinkError


async def test_xhs_rejects_foreign_profile_before_sending_cookie():
    with pytest.raises(PlatformPullError) as captured:
        await xhs.collect_xiaohongshu(
            "web_session=secret", profile_url="https://attacker.example/profile"
        )
    assert captured.value.code == "xhs_invalid_url"


async def test_xhs_checks_redirect_before_following_with_cookie(monkeypatch):
    requests: list[httpx.Request] = []

    async def public_dns(_url):
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302, headers={"location": "https://attacker.example/steal"}
        )

    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(xhs, "ensure_public_dns", public_dns)
    monkeypatch.setattr(xhs.httpx, "AsyncClient", client_factory)
    with pytest.raises(PlatformPullError) as captured:
        await xhs.collect_xiaohongshu(
            "web_session=secret",
            profile_url="https://www.xiaohongshu.com/user/profile/test",
        )

    assert captured.value.code == "xhs_invalid_url"
    assert len(requests) == 1
    assert requests[0].headers["cookie"] == "web_session=secret"


async def test_xhs_rejects_private_dns_before_request(monkeypatch):
    async def private_dns(_url):
        raise PublicLinkError("redirect_blocked")

    monkeypatch.setattr(xhs, "ensure_public_dns", private_dns)
    with pytest.raises(PlatformPullError) as captured:
        await xhs.collect_xiaohongshu(
            "web_session=secret",
            profile_url="https://www.xiaohongshu.com/user/profile/test",
        )
    assert captured.value.code == "xhs_invalid_url"


async def test_openai_profile_is_rejected_for_dashscope_only_media(monkeypatch):
    async def active_profile(_session):
        return {
            "provider_type": "openai",
            "api_key_ref": "model_profile:openai:api_key",
            "builtin": False,
        }

    monkeypatch.setattr(content_pipeline, "get_active_profile", active_profile)
    with pytest.raises(RuntimeError, match="不支持 ASR、OCR"):
        await content_pipeline.process_work(object(), object(), "job")
