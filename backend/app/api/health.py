"""Service-level endpoints: root info and health check."""

import time

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.database.database import check_db

router = APIRouter(tags=["system"])


class DatabaseHealth(BaseModel):
    ok: bool
    schema_version: int | None
    sqlite_version: str | None
    path: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str            # "ok" or "degraded"
    app: str
    version: str
    environment: str
    uptime_seconds: float
    database: DatabaseHealth


@router.get("/")
def root(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "message": "PySearch private search engine API",
        "docs": "/docs",
        "health": "/health",
    }


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    try:
        db = DatabaseHealth(**check_db(settings.DATABASE_PATH))
    except Exception as exc:  # report the failure instead of returning a 500
        db = DatabaseHealth(ok=False, schema_version=None, sqlite_version=None, error=str(exc))

    # Only show the full DB path outside production (don't leak filesystem layout).
    if settings.ENVIRONMENT == "production":
        db.path = None

    return HealthResponse(
        status="ok" if db.ok else "degraded",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        uptime_seconds=round(time.monotonic() - request.app.state.started_at, 2),
        database=db,
    )
