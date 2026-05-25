from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models import Base


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-secret-key-that-is-long-enough",
        google_client_id="google-client",
        google_client_secret="google-secret",
        signup_email_verification_required=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_db() -> AsyncGenerator:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as test_client:
        test_client._teamy_settings = settings
        yield test_client

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
