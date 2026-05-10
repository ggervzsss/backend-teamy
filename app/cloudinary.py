from hashlib import sha1
from time import time
from uuid import UUID

import httpx
from fastapi import HTTPException, UploadFile, status

from app.config import Settings

MAX_AVATAR_BYTES = 5 * 1024 * 1024


def ensure_cloudinary_configured(settings: Settings) -> None:
    if not settings.cloudinary_cloud_name or not settings.cloudinary_api_key or not settings.cloudinary_api_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cloudinary is not configured")


def sign_cloudinary_params(params: dict[str, str], api_secret: str) -> str:
    serialized = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if value != "")
    return sha1(f"{serialized}{api_secret}".encode("utf-8")).hexdigest()


async def read_avatar_file(file: UploadFile) -> bytes:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile photo must be an image")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Profile photo is empty")
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Profile photo must be 5MB or smaller")
    return content


async def upload_profile_avatar(settings: Settings, user_id: UUID, file: UploadFile) -> tuple[str, str]:
    ensure_cloudinary_configured(settings)
    content = await read_avatar_file(file)
    timestamp = str(int(time()))
    public_id = f"teamy/avatars/{user_id}"
    params = {
        "invalidate": "true",
        "overwrite": "true",
        "public_id": public_id,
        "timestamp": timestamp,
    }
    signature = sign_cloudinary_params(params, settings.cloudinary_api_secret)
    url = f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name}/image/upload"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            data={
                **params,
                "api_key": settings.cloudinary_api_key,
                "signature": signature,
            },
            files={"file": (file.filename or "avatar", content, file.content_type)},
        )

    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Cloudinary upload failed")

    payload = response.json()
    secure_url = payload.get("secure_url") or payload.get("url")
    uploaded_public_id = payload.get("public_id")
    if not secure_url or not uploaded_public_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Cloudinary upload response was incomplete")
    return str(secure_url), str(uploaded_public_id)


async def delete_profile_avatar(settings: Settings, public_id: str) -> None:
    ensure_cloudinary_configured(settings)
    timestamp = str(int(time()))
    params = {
        "invalidate": "true",
        "public_id": public_id,
        "timestamp": timestamp,
    }
    signature = sign_cloudinary_params(params, settings.cloudinary_api_secret)
    url = f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name}/image/destroy"

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            data={
                **params,
                "api_key": settings.cloudinary_api_key,
                "signature": signature,
            },
        )

    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Cloudinary delete failed")
