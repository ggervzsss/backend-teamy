import re
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloudinary import delete_profile_avatar, upload_profile_avatar
from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import AuthResponse, LoginRequest, PasswordChangeRequest, SignupRequest, UserProfileUpdateRequest, UserResponse
from app.security import (
    clear_session_cookie,
    create_oauth_state,
    create_session_token,
    decode_oauth_state,
    hash_password,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
USERNAME_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_.-]{1,38}[a-z0-9_]$")


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username.lower()))
    return result.scalar_one_or_none()


def issue_session(response: Response, user: User, settings: Settings) -> None:
    set_session_cookie(response, create_session_token(user.id, settings), settings)


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, response: Response, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AuthResponse:
    email = payload.email.lower()
    existing_user = await get_user_by_email(db, email)
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        auth_provider="local",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    issue_session(response, user, settings)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AuthResponse:
    user = await get_user_by_email(db, payload.email)
    if user is None or user.password_hash is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    issue_session(response, user, settings)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: Settings = Depends(get_settings)) -> None:
    clear_session_cookie(response, settings)


@router.get("/me", response_model=AuthResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> AuthResponse:
    return AuthResponse(user=UserResponse.model_validate(user))


@router.patch("/me", response_model=AuthResponse)
async def update_me(
    payload: UserProfileUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    if payload.full_name is not None:
        full_name = payload.full_name.strip()
        if not full_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Full name is required")
        user.full_name = full_name
    if payload.username is not None:
        username = payload.username.strip().lower()
        if not username:
            user.username = None
        else:
            if not USERNAME_PATTERN.fullmatch(username):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username must be 3-40 characters and use letters, numbers, dots, dashes, or underscores")
            existing_user = await get_user_by_username(db, username)
            if existing_user is not None and existing_user.id != user.id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already taken")
            user.username = username

    await db.commit()
    await db.refresh(user)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/me/avatar", response_model=AuthResponse)
async def upload_my_avatar(
    user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    old_public_id = user.cloudinary_avatar_public_id
    avatar_url, public_id = await upload_profile_avatar(settings, user.id, file)
    user.avatar_url = avatar_url
    user.cloudinary_avatar_public_id = public_id
    await db.commit()
    await db.refresh(user)

    if old_public_id and old_public_id != public_id:
        await delete_profile_avatar(settings, old_public_id)

    return AuthResponse(user=UserResponse.model_validate(user))


@router.delete("/me/avatar", response_model=AuthResponse)
async def delete_my_avatar(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    public_id = user.cloudinary_avatar_public_id
    if public_id:
        await delete_profile_avatar(settings, public_id)

    user.avatar_url = None
    user.cloudinary_avatar_public_id = None
    await db.commit()
    await db.refresh(user)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.patch("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    payload: PasswordChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    if user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password changes are unavailable for this account")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be different from the current password")

    user.password_hash = hash_password(payload.new_password)
    await db.commit()


@router.get("/google/login")
async def google_login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google authentication is not configured")

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": create_oauth_state(settings),
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/google/callback")
async def google_callback(
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    next_path = decode_oauth_state(state, settings)

    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if token_response.status_code >= 400:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google token exchange failed")

        access_token = token_response.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google did not return an access token")

        userinfo_response = await client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if userinfo_response.status_code >= 400:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google profile lookup failed")

    profile = userinfo_response.json()
    email = str(profile.get("email") or "").lower()
    subject = str(profile.get("sub") or "")
    full_name = str(profile.get("name") or email.split("@")[0])
    avatar_url = profile.get("picture")

    if not email or not subject:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google profile is missing required fields")

    user = await get_user_by_email(db, email)
    if user is None:
        user = User(
            email=email,
            full_name=full_name,
            password_hash=None,
            auth_provider="google",
            provider_subject=subject,
            avatar_url=avatar_url,
        )
        db.add(user)
    else:
        user.full_name = user.full_name or full_name
        user.provider_subject = user.provider_subject or subject
        user.avatar_url = avatar_url or user.avatar_url
        if user.auth_provider == "local":
            user.auth_provider = "local_google"

    await db.commit()
    await db.refresh(user)

    redirect = RedirectResponse(f"{str(settings.frontend_url).rstrip('/')}{next_path}")
    issue_session(redirect, user, settings)
    return redirect
