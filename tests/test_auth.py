from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.security import create_oauth_state


def mock_google_client(monkeypatch, *, email="google@example.com", name="Google User", picture="https://example.com/google-avatar.png", subject="google-subject"):
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
            return FakeResponse({"sub": subject, "email": email, "name": name, "picture": picture})

    monkeypatch.setattr("app.auth.httpx.AsyncClient", FakeAsyncClient)


@pytest.mark.asyncio
async def test_google_login_sets_session_and_me(client, monkeypatch):
    mock_google_client(monkeypatch)
    state = create_oauth_state(Settings(secret_key="test-secret-key-that-is-long-enough"))

    callback = await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)
    assert callback.status_code == 307
    assert "teamy_session" in client.cookies

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == "google@example.com"


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    await client._login_user("jane@example.com", "Jane Doe")
    assert "teamy_session" in client.cookies

    response = await client.post("/auth/logout")
    assert response.status_code == 204
    client._logout_user()
    assert "teamy_session" not in client.cookies


@pytest.mark.asyncio
async def test_update_me_changes_profile_fields(client):
    await client._login_user("jane@example.com", "Jane Doe")

    response = await client.patch("/auth/me", json={"full_name": "Jane Rivera"})
    me_response = await client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Jane Rivera"
    assert response.json()["user"]["username"] is None
    assert me_response.json()["user"]["full_name"] == "Jane Rivera"
    assert me_response.json()["user"]["username"] is None


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

    await client._login_user("jane@example.com", "Jane Doe")

    upload = await client.post("/auth/me/avatar", files={"file": ("avatar.png", b"image-bytes", "image/png")})
    restore = await client.delete("/auth/me/avatar")

    assert upload.status_code == 200
    assert upload.json()["user"]["avatar_url"] == "https://res.cloudinary.test/avatar.jpg"
    assert restore.status_code == 200
    assert restore.json()["user"]["avatar_url"] is None
    assert deleted_public_ids == uploaded_public_ids


@pytest.mark.asyncio
async def test_google_login_preserves_custom_cloudinary_avatar(client, monkeypatch):
    mock_google_client(monkeypatch)

    async def fake_upload(settings, user_id, file):
        return "https://res.cloudinary.test/custom-avatar.jpg", f"teamy/avatars/{user_id}"

    monkeypatch.setattr("app.auth.upload_profile_avatar", fake_upload)

    state = create_oauth_state(Settings(secret_key="test-secret-key-that-is-long-enough"))
    assert (await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)).status_code == 307
    assert (await client.post("/auth/me/avatar", files={"file": ("avatar.png", b"image-bytes", "image/png")})).status_code == 200
    await client.post("/auth/logout")
    assert (await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)).status_code == 307

    me_response = await client.get("/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["user"]["avatar_url"] == "https://res.cloudinary.test/custom-avatar.jpg"
    assert me_response.json()["user"]["google_avatar_url"] == "https://example.com/google-avatar.png"


@pytest.mark.asyncio
async def test_restore_google_avatar_replaces_custom_cloudinary_avatar(client, monkeypatch):
    mock_google_client(monkeypatch)

    deleted_public_ids = []

    async def fake_upload(settings, user_id, file):
        return "https://res.cloudinary.test/custom-avatar.jpg", f"teamy/avatars/{user_id}"

    async def fake_delete(settings, public_id):
        deleted_public_ids.append(public_id)

    monkeypatch.setattr("app.auth.upload_profile_avatar", fake_upload)
    monkeypatch.setattr("app.auth.delete_profile_avatar", fake_delete)

    state = create_oauth_state(Settings(secret_key="test-secret-key-that-is-long-enough"))
    assert (await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)).status_code == 307
    assert (await client.post("/auth/me/avatar", files={"file": ("avatar.png", b"image-bytes", "image/png")})).status_code == 200
    restore_response = await client.post("/auth/me/avatar/google")

    assert restore_response.status_code == 200
    assert restore_response.json()["user"]["avatar_url"] == "https://example.com/google-avatar.png"
    assert deleted_public_ids


@pytest.mark.asyncio
async def test_google_login_redirects_to_frontend(client, monkeypatch):
    mock_google_client(monkeypatch)
    settings = Settings(secret_key="test-secret-key-that-is-long-enough", frontend_url="http://localhost:5173")
    state = create_oauth_state(settings, next_path="/projects")

    response = await client.get(f"/auth/google/callback?code=test-code&state={state}", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.netloc == "localhost:5173"
    assert parsed.path == "/projects"


@pytest.mark.asyncio
async def test_google_login_url_contains_state(client):
    response = await client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query.get("state")
