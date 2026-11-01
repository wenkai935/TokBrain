"""get_provider factory: dashscope default, OpenAI-compatible override."""

from app.config import settings
from app.models import AppSetting
from app.services import knowledge
from app.services.providers import (
    DashScopeProvider,
    OpenAICompatibleProvider,
    get_provider,
)


class FakeSession:
    def __init__(self, record=None):
        self.record = record

    async def get(self, _model, key):
        return self.record if key == "model_profiles" else None


def _patch_secret(monkeypatch, secret: str):
    async def fake_get_secret(_session, _name):
        return secret

    monkeypatch.setattr("app.services.secrets.get_secret", fake_get_secret)


async def test_default_provider_is_dashscope(monkeypatch):
    _patch_secret(monkeypatch, "test-key")
    provider = await get_provider(FakeSession())
    assert isinstance(provider, DashScopeProvider)
    assert provider.base_url == settings.dashscope_base_url
    assert provider.api_key == "test-key"


async def test_api_key_argument_skips_secret_lookup(monkeypatch):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("get_secret should not be called when api_key is passed")

    monkeypatch.setattr("app.services.secrets.get_secret", unexpected)
    provider = await get_provider(FakeSession(), api_key="explicit-key")
    assert isinstance(provider, DashScopeProvider)
    assert provider.api_key == "explicit-key"


async def test_openai_provider_type_uses_configured_base_url_and_models(monkeypatch):
    _patch_secret(monkeypatch, "key")
    monkeypatch.setattr(settings, "provider_type", "openai")
    monkeypatch.setattr(settings, "provider_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "provider_embedding_model", "custom-embed")
    monkeypatch.setattr(settings, "provider_chat_model", "custom-chat")

    provider = await get_provider(FakeSession())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert not isinstance(provider, DashScopeProvider)
    assert provider.base_url == "https://api.example.com/v1"
    assert provider.embedding_model == "custom-embed"
    assert provider.chat_model == "custom-chat"


async def test_openai_provider_type_falls_back_to_dashscope_url(monkeypatch):
    _patch_secret(monkeypatch, "key")
    monkeypatch.setattr(settings, "provider_type", "openai")
    monkeypatch.setattr(settings, "provider_base_url", None)

    provider = await get_provider(FakeSession())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == settings.dashscope_base_url


async def test_active_profile_uses_its_own_key_and_endpoint(monkeypatch):
    profile = {
        "id": "custom-openai",
        "name": "custom",
        "provider_type": "openai",
        "base_url": "https://api.example.com/v1",
        "embedding_model": "embed-custom",
        "chat_model": "chat-custom",
        "api_key_ref": "model_profile:custom-openai:api_key",
        "builtin": False,
    }
    session = FakeSession(
        AppSetting(
            key="model_profiles",
            value={"active_id": profile["id"], "items": [profile]},
        )
    )
    requested = []

    async def secret(_session, name):
        requested.append(name)
        return "profile-key" if name == profile["api_key_ref"] else "shared-key"

    monkeypatch.setattr("app.services.secrets.get_secret", secret)
    provider = await get_provider(session)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.api_key == "profile-key"
    assert provider.base_url == profile["base_url"]
    assert requested == [profile["api_key_ref"]]

    retrieval_provider = knowledge._build_provider("profile-key", profile)
    assert isinstance(retrieval_provider, OpenAICompatibleProvider)
    assert retrieval_provider.base_url == profile["base_url"]
    assert retrieval_provider.embedding_model == profile["embedding_model"]
