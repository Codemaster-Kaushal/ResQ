"""FastAPI application factory.

Run with: uvicorn app.main:app
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import debug, health, reports
from app.config import settings
from app.core.errors import DEFAULT_ERROR_RESPONSES, register_exception_handlers
from app.core.logging import (
    REQUEST_ID_HEADER,
    configure_logging,
    get_logger,
    request_id_ctx,
)
from app.db import ensure_storage_paths, init_db

logger = get_logger(__name__)

DESCRIPTION = """
Severity-ordered, authenticity-verified emergency dispatch.

Incoming reports are ranked by life-risk rather than arrival time, screened for
duplicates and implausible geo-time signals, matched to the best-fit responder, and
recorded as a process event log so response bottlenecks are measurable.
"""


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request ID, time the request, and log one structured line per call."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        # Left set on purpose — see the note on request_id_ctx. The exception handler
        # runs after this frame unwinds and still needs the ID.
        request_id_ctx.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The exception handler builds the client response; log the timing here
            # so failed requests are as observable as successful ones.
            logger.warning(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise

        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_format)
    logger.info("starting RescueNet backend", extra={"config": settings.redacted_summary()})

    ensure_storage_paths()
    init_db()

    logger.info(
        "startup complete",
        extra={"version": settings.app_version, "environment": settings.environment},
    )
    yield
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
        responses=DEFAULT_ERROR_RESPONSES,
    )

    # Order matters: CORS is added last so it wraps outermost and still applies
    # to error responses produced further in.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(reports.router)
    if settings.enable_debug_routes:
        app.include_router(debug.router)

    @app.get("/", tags=["system"], summary="Service metadata")
    def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
