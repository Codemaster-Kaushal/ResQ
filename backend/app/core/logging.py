"""Structured logging with per-request correlation IDs.

One stdout handler on the root logger, one format for everything including uvicorn,
so the demo terminal and any log aggregator see the same shape.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

REQUEST_ID_HEADER = "X-Request-ID"

# Bound by RequestContextMiddleware; every log line emitted while handling a request
# carries the same request_id, which is also returned in the X-Request-ID header.
#
# Deliberately never reset once set: Starlette's ServerErrorMiddleware runs *outside*
# the request middleware, so resetting on the way out would strip the correlation ID
# from the very log line describing an unhandled exception. Each request is served in
# its own task with its own context, so there is nothing to leak into.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Standard LogRecord attributes; anything else on the record came from `extra=`.
_STANDARD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}


def _timestamp(record: logging.LogRecord) -> str:
    return (
        datetime.fromtimestamp(record.created, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id

        payload.update(_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable alternative, selected with LOG_FORMAT=console."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_ctx.get()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        extras = _extra_fields(record)
        suffix = " " + " ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
        line = (
            f"{_timestamp(record)} {record.levelname:<8} {record.name} "
            f"{prefix}{record.getMessage()}{suffix}"
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root stdout handler. Idempotent — safe to call on every startup."""
    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn's own loggers through our handler instead of its defaults.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Our middleware already logs every request with timing and status.
    logging.getLogger("uvicorn.access").disabled = True


class SafeLogger(logging.LoggerAdapter):
    """A logger whose ``extra=`` can never take a request down.

    ``logging`` *raises* when an extra key collides with a built-in LogRecord attribute
    — ``extra={"created": 3}`` is a KeyError, not a shadowed field. That turns a
    logging statement into a 500, and only at a log level where the call actually runs,
    so it can sail through a test suite that runs quieter than production does.
    Colliding keys are renamed rather than dropped: observability should degrade, not
    disappear, and never at the cost of the response.
    """

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.get("extra")
        if extra:
            kwargs["extra"] = {
                (f"extra_{key}" if key in _STANDARD_ATTRS else key): value
                for key, value in extra.items()
            }
        return msg, kwargs


def get_logger(name: str) -> SafeLogger:
    return SafeLogger(logging.getLogger(name), {})
