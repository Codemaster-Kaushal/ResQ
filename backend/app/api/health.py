"""Liveness endpoint with a real database probe."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core.runtime import uptime_seconds
from app.db import check_database

router = APIRouter(tags=["system"])


class DatabaseHealth(BaseModel):
    status: str = Field(examples=["ok"])
    dialect: str = Field(examples=["sqlite"])
    latency_ms: float = Field(examples=[0.42])
    error: str | None = None


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"], description="'ok' or 'degraded'")
    service: str
    version: str
    environment: str
    uptime_seconds: float
    database: DatabaseHealth


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and database status",
    responses={503: {"model": HealthResponse, "description": "Database unreachable"}},
)
def health() -> Any:
    db = check_database()
    healthy = db["status"] == "ok"

    body = HealthResponse(
        status="ok" if healthy else "degraded",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        uptime_seconds=uptime_seconds(),
        database=DatabaseHealth(**db),
    )

    # A liveness check that always answers 200 tells you nothing.
    if not healthy:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
