import pytest
from pydantic import ValidationError

from app.config import Settings


def test_accepts_sqlite_for_tests() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-secret-key-that-is-long-enough",
    )

    assert settings.database_url == "sqlite+aiosqlite:///:memory:"


def test_rejects_render_postgres_url() -> None:
    with pytest.raises(ValidationError, match="Render PostgreSQL URLs are not supported"):
        Settings(
            database_url="postgresql://user:password@localhost:5432/teamy",
            secret_key="test-secret-key-that-is-long-enough",
        )
