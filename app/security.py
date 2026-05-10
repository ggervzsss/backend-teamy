from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import HTTPException, Response, status
from passlib.context import CryptContext

from app.config import Settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_session_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.session_max_age_seconds)).timestamp()),
        "typ": "session",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_session_token(token: str, settings: Settings) -> UUID:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc

    if payload.get("typ") != "session" or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return UUID(payload["sub"])


def create_task_socket_ticket(user_id: UUID, project_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "project_id": str(project_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "typ": "task_socket",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_task_socket_ticket(ticket: str, settings: Settings) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=[ALGORITHM])
        user_id = UUID(payload["sub"])
        project_id = UUID(payload["project_id"])
    except (KeyError, ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid task socket ticket") from exc

    if payload.get("typ") != "task_socket":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid task socket ticket")
    return user_id, project_id


def create_announcement_socket_ticket(user_id: UUID, project_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "project_id": str(project_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "typ": "announcement_socket",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_announcement_socket_ticket(ticket: str, settings: Settings) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=[ALGORITHM])
        user_id = UUID(payload["sub"])
        project_id = UUID(payload["project_id"])
    except (KeyError, ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid announcement socket ticket") from exc

    if payload.get("typ") != "announcement_socket":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid announcement socket ticket")
    return user_id, project_id


def create_team_socket_ticket(user_id: UUID, project_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "project_id": str(project_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "typ": "team_socket",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_team_socket_ticket(ticket: str, settings: Settings) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=[ALGORITHM])
        user_id = UUID(payload["sub"])
        project_id = UUID(payload["project_id"])
    except (KeyError, ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid team socket ticket") from exc

    if payload.get("typ") != "team_socket":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid team socket ticket")
    return user_id, project_id


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def create_oauth_state(settings: Settings, next_path: str = "/projects") -> str:
    now = datetime.now(UTC)
    payload = {
        "typ": "google_oauth_state",
        "next": next_path,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_oauth_state(state: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state") from exc
    if payload.get("typ") != "google_oauth_state":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    return str(payload.get("next") or "/dashboard")
