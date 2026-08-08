"""Typed error envelope and global exception handlers.

Every failure leaving this API has the shape defined in TRD §6:

    {"error": {"code": "REPORT_NOT_FOUND", "message": "...", "detail": {}}}

NFR-5: no unhandled exception may reach the client. The bare-Exception handler is the
backstop — it logs the traceback server-side and returns a generic message.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import REQUEST_ID_HEADER, get_logger, request_id_ctx

logger = get_logger(__name__)


class ErrorCode:
    """Stable machine-readable error codes. Later phases append their own."""

    # Phase 1 — foundations
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFLICT = "CONFLICT"


# Starlette raises bare HTTPExceptions for routing failures; map them onto our codes.
_STATUS_TO_CODE = {
    400: ErrorCode.VALIDATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


class AppError(Exception):
    """Base class for every deliberately raised API error."""

    status_code: int = 500
    code: str = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.detail: dict[str, Any] = detail or {}


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND


class ValidationFailedError(AppError):
    status_code = 422
    code = ErrorCode.VALIDATION_ERROR


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.CONFLICT


class ServiceUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE


class ErrorBody(BaseModel):
    code: str = Field(examples=["REPORT_NOT_FOUND"])
    message: str = Field(examples=["Report 7f3c… does not exist"])
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """The only failure shape this API emits."""

    error: ErrorBody


# Attached to the FastAPI app so /docs documents the failure shape on every route.
DEFAULT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorEnvelope, "description": "Validation error"},
    500: {"model": ErrorEnvelope, "description": "Internal error"},
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build the envelope, tagging it with the request ID for support and log lookup.

    A 500 produced by the bare-Exception handler bypasses the request middleware
    entirely, so the header is attached here rather than there.
    """
    payload: dict[str, Any] = dict(detail or {})
    request_id = request_id_ctx.get()
    if request_id:
        payload.setdefault("request_id", request_id)

    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message, detail=payload))
    response = JSONResponse(status_code=status_code, content=envelope.model_dump())
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the four handlers that guarantee a typed envelope on every failure path."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "handled application error",
            extra={
                "error_code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
            },
        )
        return error_response(exc.status_code, exc.code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "location": ".".join(str(part) for part in err.get("loc", ())),
                "message": err.get("msg", "invalid value"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        logger.info(
            "request validation failed",
            extra={"path": request.url.path, "field_count": len(fields)},
        )
        return error_response(
            422,
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = str(exc.detail) if exc.detail else "Request failed"
        return error_response(exc.status_code, code, message, {"path": request.url.path})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the log, never to the client (NFR-5).
        logger.exception(
            "unhandled exception",
            extra={
                "path": request.url.path,
                "method": request.method,
                "exception_type": type(exc).__name__,
            },
        )
        return error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "An internal error occurred. The incident has been logged.",
            {"path": request.url.path},
        )
