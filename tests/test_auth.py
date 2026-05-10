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
    assert response.headers["location"] == "http://localhost:5173/dashboard"
    assert "teamy_session" in client.cookies

    me_response = await client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == "google@example.com"
