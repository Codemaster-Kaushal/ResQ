"""Routes that deliberately fail, to prove the error envelope holds.

Mounted only when ENABLE_DEBUG_ROUTES is true. Phase 1 acceptance requires showing
that an unhandled exception returns the envelope rather than a stack trace.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import AppError, ErrorCode, ErrorEnvelope, NotFoundError

router = APIRouter(prefix="/api/_debug", tags=["debug"])


@router.get(
    "/boom",
    summary="Raise an unhandled exception",
    responses={500: {"model": ErrorEnvelope}},
)
def boom() -> None:
    """Uncaught error — must surface as INTERNAL_ERROR with no traceback."""
    raise RuntimeError("deliberate unhandled exception for envelope verification")


@router.get(
    "/app-error",
    summary="Raise a typed application error",
    responses={404: {"model": ErrorEnvelope}},
)
def app_error() -> None:
    """The handled counterpart: a typed error carrying its own code and detail."""
    raise NotFoundError(
        "This resource does not exist",
        detail={"hint": "typed errors carry structured detail"},
    )


@router.get(
    "/service-unavailable",
    summary="Raise a dependency-unavailable error",
    responses={503: {"model": ErrorEnvelope}},
)
def service_unavailable() -> None:
    raise AppError(
        "A downstream dependency is unavailable",
        code=ErrorCode.DATABASE_UNAVAILABLE,
        status_code=503,
    )
