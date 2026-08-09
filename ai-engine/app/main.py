"""
RescueNet AI Engine — FastAPI application entry point.
"""

import logging
import logging.config

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_engine.config import APP_LOG_LEVEL, APP_ENV
from ai_engine.exceptions import AIEngineError
from app.routers import health, ai
from shared.schemas.incident_ai import AIErrorDetail, AIErrorResponse

# ── Logging configuration ─────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, APP_LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    _app = FastAPI(
        title="RescueNet AI Engine",
        description=(
            "Offline-first AI triage engine for disaster-response coordination. "
            "Provides incident classification (FR-6), severity scoring (FR-7/FR-8), "
            "and structured risk-factor extraction using IBM Granite via Ollama."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Global exception handler (NFR-5) ──────────────────────────────────────
    @_app.exception_handler(AIEngineError)
    async def ai_engine_error_handler(request: Request, exc: AIEngineError):
        logger.error("AIEngineError [%s]: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=503,
            content=AIErrorResponse(
                error=AIErrorDetail(
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                )
            ).model_dump(),
        )

    @_app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content=AIErrorResponse(
                error=AIErrorDetail(
                    code="INTERNAL_ERROR",
                    message="An unexpected internal error occurred.",
                    retryable=False,
                )
            ).model_dump(),
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    _app.include_router(health.router)
    _app.include_router(ai.router)

    logger.info("RescueNet AI Engine started (env=%s)", APP_ENV)
    return _app


app = create_app()
