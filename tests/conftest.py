from collections.abc import AsyncGenerator, Callable
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models import Base, User
from app.security import create_session_token


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-secret-key-that-is-long-enough",
        google_client_id="google-client",
        google_client_secret="google-secret",
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
        test_client._sessionmaker = sessionmaker

        async def get_or_create_user(email: str, full_name: str = "Test User") -> User:
            normalized_email = email.lower()
            async with sessionmaker() as session:
                result = await session.execute(select(User).where(User.email == normalized_email))
                user = result.scalar_one_or_none()
                if user is None:
                    user = User(
                        email=normalized_email,
                        full_name=full_name,
                        auth_provider="google",
                        provider_subject=str(uuid4()),
                        password_hash=None,
                        avatar_url=None,
                        google_avatar_url=None,
                    )
                    session.add(user)
                    await session.commit()
                    await session.refresh(user)
                return user

        async def login_user(email: str, full_name: str = "Test User") -> User:
            user = await get_or_create_user(email, full_name)
            token = create_session_token(user.id, settings)
            test_client.cookies.set(settings.session_cookie_name, token)
            return user

        def logout_user() -> None:
            if settings.session_cookie_name in test_client.cookies:
                test_client.cookies.pop(settings.session_cookie_name)

        test_client._get_or_create_user = get_or_create_user
        test_client._login_user = login_user
        test_client._logout_user = logout_user

        yield test_client

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
