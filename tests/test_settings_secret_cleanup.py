from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers_v2.settings as settings_router
from app.models import Base, SecretRecord


async def test_clear_credentials_removes_profile_keys_but_keeps_platform_cookie(
    tmp_path, monkeypatch
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'secrets.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def view(_session):
        return {"ok": True}

    monkeypatch.setattr(settings_router, "_view", view)
    async with factory() as session:
        for name in (
            "dashscope_api_key",
            "bss_access_key_id",
            "bss_access_key_secret",
            "model_profile:first:api_key",
            "model_profile:orphaned:api_key",
            "xhs_cookie",
        ):
            session.add(SecretRecord(name=name, encrypted_value=b"test"))
        await session.commit()
        await settings_router.clear_api_credentials(session)

        assert await session.get(SecretRecord, "dashscope_api_key") is None
        assert await session.get(SecretRecord, "model_profile:first:api_key") is None
        assert await session.get(SecretRecord, "model_profile:orphaned:api_key") is None
        assert await session.get(SecretRecord, "xhs_cookie") is not None

    await engine.dispose()
