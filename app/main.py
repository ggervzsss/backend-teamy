import logging

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from app.announcements import router as announcements_router
from app.auth import router as auth_router
from app.config import get_settings
from app.filehub import router as filehub_router
from app.notifications import router as notifications_router
from app.projects import router as projects_router
from app.team import router as team_router
from app.tasks import router as tasks_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Teamy API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)

    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(filehub_router)
    app.include_router(tasks_router)
    app.include_router(announcements_router)
    app.include_router(notifications_router)
    app.include_router(team_router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error on %s %s", request.method, request.url.path, exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
