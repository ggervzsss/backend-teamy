from datetime import UTC, datetime, timedelta
import html
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cloudinary import delete_profile_avatar, upload_profile_avatar
from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import EmailVerificationCode, User
from app.notifications import EmailRecipient, build_email_shell, send_email
from app.schemas import (
    AuthResponse,
    LoginRequest,
    PasswordChangeRequest,
    SignupRequest,
    SignupVerificationCodeRequest,
    SignupVerificationCodeResponse,
    UserProfileUpdateRequest,
)
from app.security import (
    clear_session_cookie,
    create_email_verification_code,
    create_oauth_state,
    create_session_token,
    decode_oauth_state,
    hash_email_verification_code,
    hash_password,
    set_session_cookie,
    verify_email_verification_code,
    verify_password,
)
from app.user_responses import serialize_account_user

router = APIRouter(prefix="/auth", tags=["auth"])
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


def issue_session(response: Response, user: User, settings: Settings) -> None:
    set_session_cookie(response, create_session_token(user.id, settings), settings)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def send_signup_verification_email(settings: Settings, email: str, code: str) -> None:
    safe_code = html.escape(code)
    expires_in_minutes = max(1, settings.signup_verification_code_ttl_seconds // 60)
    body = f"<p>Use this code to finish creating your Teamy account:</p><p style=\"font-size:24px;font-weight:700;letter-spacing:4px\">{safe_code}</p><p>This code expires in {expires_in_minutes} minutes.</p>"
    text = f"Use this code to finish creating your Teamy account: {code}\n\nThis code expires in {expires_in_minutes} minutes."
    await send_email(
        settings,
        [EmailRecipient(email=email, full_name=email)],
        "Your Teamy verification code",
        build_email_shell("Verify your email", body, str(settings.frontend_url).rstrip("/"), "Open Teamy"),
        text,
        raise_on_error=True,
    )


async def require_valid_signup_verification(db: AsyncSession, email: str, code: str | None, settings: Settings) -> None:
    if not settings.signup_email_verification_required:
        return
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email verification code is required")

    result = await db.execute(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.email == email, EmailVerificationCode.consumed_at.is_(None))
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    verification = result.scalar_one_or_none()
    now = datetime.now(UTC)

    if verification is None or ensure_aware_utc(verification.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification code")
    if verification.attempts >= settings.signup_verification_max_attempts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many incorrect verification attempts")
    if not verify_email_verification_code(email, code, verification.code_hash, settings):
        verification.attempts += 1
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification code")

    verification.consumed_at = now


@router.post("/signup/verification", response_model=SignupVerificationCodeResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_signup_verification_code(
    payload: SignupVerificationCodeRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SignupVerificationCodeResponse:
    email = payload.email.lower()
    existing_user = await get_user_by_email(db, email)
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    if settings.signup_email_verification_required and not settings.resend_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email verification is not configured")

    now = datetime.now(UTC)
    code = create_email_verification_code(settings.signup_verification_code_length)
    await db.execute(
        update(EmailVerificationCode)
        .where(EmailVerificationCode.email == email, EmailVerificationCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    db.add(
        EmailVerificationCode(
            email=email,
            code_hash=hash_email_verification_code(email, code, settings),
            expires_at=now + timedelta(seconds=settings.signup_verification_code_ttl_seconds),
        )
    )

    try:
        await send_signup_verification_email(settings, email, code)
    except HTTPException:
        await db.rollback()
        raise

    await db.commit()
    return SignupVerificationCodeResponse(detail="Verification code sent", expires_in_seconds=settings.signup_verification_code_ttl_seconds)


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, response: Response, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AuthResponse:
    email = payload.email.lower()
    existing_user = await get_user_by_email(db, email)
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    await require_valid_signup_verification(db, email, payload.verification_code, settings)

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
    return AuthResponse(user=serialize_account_user(user))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)) -> AuthResponse:
    user = await get_user_by_email(db, payload.email)
    if user is None or user.password_hash is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    issue_session(response, user, settings)
    return AuthResponse(user=serialize_account_user(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: Settings = Depends(get_settings)) -> None:
    clear_session_cookie(response, settings)


@router.get("/me", response_model=AuthResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> AuthResponse:
    return AuthResponse(user=serialize_account_user(user))


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

    await db.commit()
    await db.refresh(user)
    return AuthResponse(user=serialize_account_user(user))


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

    return AuthResponse(user=serialize_account_user(user))


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
    return AuthResponse(user=serialize_account_user(user))


@router.post("/me/avatar/google", response_model=AuthResponse)
async def restore_my_google_avatar(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    if not user.google_avatar_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Google profile picture is available for this account")

    public_id = user.cloudinary_avatar_public_id
    if public_id:
        await delete_profile_avatar(settings, public_id)

    user.avatar_url = user.google_avatar_url
    user.cloudinary_avatar_public_id = None
    await db.commit()
    await db.refresh(user)
    return AuthResponse(user=serialize_account_user(user))


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
    google_avatar_url = profile.get("picture")

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
            avatar_url=google_avatar_url,
            google_avatar_url=google_avatar_url,
        )
        db.add(user)
    else:
        user.full_name = user.full_name or full_name
        user.provider_subject = user.provider_subject or subject
        user.google_avatar_url = google_avatar_url or user.google_avatar_url
        if user.auth_provider == "local":
            user.auth_provider = "local_google"

    await db.commit()
    await db.refresh(user)

    redirect = RedirectResponse(f"{str(settings.frontend_url).rstrip('/')}{next_path}")
    issue_session(redirect, user, settings)
    return redirect
