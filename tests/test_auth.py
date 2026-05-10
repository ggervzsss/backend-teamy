from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.security import create_oauth_state


@pytest.mark.asyncio
async def test_signup_login_me_logout(client):
    signup_response = await client.post(
        "/auth/signup",
        json={"full_name": "Jane Doe", "email": "Jane@Example.com", "password": "password123"},
    )
    assert signup_response.status_code == 201
    assert signup_response.json()["user"]["email"] == "jane@example.com"
    assert "teamy_session" in client.cookies

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["full_name"] == "Jane Doe"

    logout_response = await client.post("/auth/logout")
    assert logout_response.status_code == 204
    assert "teamy_session" not in client.cookies

    login_response = await client.post("/auth/login", json={"email": "jane@example.com", "password": "password123"})
    assert login_response.status_code == 200
    assert "teamy_session" in client.cookies


@pytest.mark.asyncio
async def test_duplicate_signup_is_rejected(client):
    payload = {"full_name": "Jane Doe", "email": "jane@example.com", "password": "password123"}
    assert (await client.post("/auth/signup", json=payload)).status_code == 201
    response = await client.post("/auth/signup", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_invalid_login_is_rejected(client):
    response = await client.post("/auth/login", json={"email": "missing@example.com", "password": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_update_me_changes_profile_fields(client):
    await client.post(
        "/auth/signup",
        json={"full_name": "Jane Doe", "email": "jane@example.com", "password": "password123"},
    )

    response = await client.patch("/auth/me", json={"full_name": "Jane Rivera", "username": "jane.rivera"})
    me_response = await client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Jane Rivera"
    assert response.json()["user"]["username"] == "jane.rivera"
    assert me_response.json()["user"]["full_name"] == "Jane Rivera"


@pytest.mark.asyncio
async def test_duplicate_username_is_rejected(client):
    await client.post(
        "/auth/signup",
        json={"full_name": "Jane Doe", "email": "jane@example.com", "password": "password123"},
    )
    assert (await client.patch("/auth/me", json={"username": "shared.name"})).status_code == 200
    await client.post("/auth/logout")

    await client.post(
        "/auth/signup",
        json={"full_name": "John Doe", "email": "john@example.com", "password": "password123"},
    )
    response = await client.patch("/auth/me", json={"username": "shared.name"})

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_avatar_upload_and_restore_delete_cloudinary_asset(client, monkeypatch):
    uploaded_public_ids = []
    deleted_public_ids = []

    async def fake_upload(settings, user_id, file):
        uploaded_public_ids.append(f"teamy/avatars/{user_id}")
        return "https://res.cloudinary.test/avatar.jpg", uploaded_public_ids[-1]

    async def fake_delete(settings, public_id):
        deleted_public_ids.append(public_id)

    monkeypatch.setattr("app.auth.upload_profile_avatar", fake_upload)
    monkeypatch.setattr("app.auth.delete_profile_avatar", fake_delete)

    await client.post(
        "/auth/signup",
        json={"full_name": "Jane Doe", "email": "jane@example.com", "password": "password123"},
    )

    upload = await client.post("/auth/me/avatar", files={"file": ("avatar.png", b"image-bytes", "image/png")})
    restore = await client.delete("/auth/me/avatar")

    assert upload.status_code == 200
    assert upload.json()["user"]["avatar_url"] == "https://res.cloudinary.test/avatar.jpg"
    assert restore.status_code == 200
    assert restore.json()["user"]["avatar_url"] is None
    assert deleted_public_ids == uploaded_public_ids


@pytest.mark.asyncio
async def test_change_password_requires_current_password(client):
    await client.post(
        "/auth/signup",
        json={"full_name": "Jane Doe", "email": "jane@example.com", "password": "password123"},
    )

    wrong_current = await client.patch("/auth/me/password", json={"current_password": "wrong-password", "new_password": "new-password123"})
    changed = await client.patch("/auth/me/password", json={"current_password": "password123", "new_password": "new-password123"})
    await client.post("/auth/logout")
    old_login = await client.post("/auth/login", json={"email": "jane@example.com", "password": "password123"})
    new_login = await client.post("/auth/login", json={"email": "jane@example.com", "password": "new-password123"})

    assert wrong_current.status_code == 400
    assert changed.status_code == 204
    assert old_login.status_code == 401
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_google_login_redirects_to_google(client):
    response = await client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["google-client"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"][0]


@pytest.mark.asyncio
async def test_google_callback_creates_user(client, monkeypatch):
    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, data=None, headers=None):
            return FakeResponse({"access_token": "google-access-token"})

        async def get(self, url, headers=None):
            return FakeResponse(
                {
                    "sub": "google-subject",
                    "email": "google@example.com",
                    "name": "Google User",
                    "picture": "https://example.com/avatar.png",
                }
            )

    monkeypatch.setattr("app.auth.httpx.AsyncClient", FakeAsyncClient)
    state = create_oauth_state(Settings(secret_key="test-secret-key-that-is-long-enough"))

    response = await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:5173/projects"
    assert "teamy_session" in client.cookies

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == "google@example.com"
