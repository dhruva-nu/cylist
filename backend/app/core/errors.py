"""Domain errors and their HTTP translation.

Services raise these; routers stay free of status codes and error shaping.
Every error reaches the client as::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, client-facing failures."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class UnauthorizedError(AppError):
    """No valid credentials were presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    """Valid credentials, but the principal lacks the required scope."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFoundError(AppError):
    """The addressed resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    """The request contradicts the current state (duplicate, limit reached)."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class PayloadTooLargeError(AppError):
    """The request body is bigger than the configured limit allows."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"


class UnprocessableRequestError(AppError):
    """Well-formed, but it breaks a business rule."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unprocessable"


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render every failure in the same envelope."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        _log_app_error(request, exc)
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthorizedError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Which fields, never their values: a rejected body is exactly where a
        # mistyped password or a vault secret is most likely to be sitting.
        logger.info(
            "Request failed validation",
            extra={"context": {"fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body(
                "validation_failed",
                "The request body or parameters are invalid.",
                # jsonable_encoder is required, not decorative: for a custom
                # field validator Pydantic puts the original exception object
                # in ctx, which JSONResponse cannot serialise.
                {"fields": jsonable_encoder(exc.errors())},
            ),
        )


def _log_app_error(request: Request, exc: AppError) -> None:
    """Record why a request was refused.

    The access line in :mod:`app.core.request_log` already carries the status
    and grades the level by it; what it cannot carry is the reason. This adds
    that, at INFO, and leaves the grading alone — so ``grep WARNING`` still
    lists exactly the refused requests, one line each, and the request id on
    that line leads to this one for the explanation.

    A 5xx is different in kind: an :class:`AppError` that renders as a server
    error is a bug in Cylist rather than a client's mistake, so it is logged
    at ERROR with the traceback that produced it.
    """
    context = {
        "code": exc.code,
        "status": exc.status_code,
        "method": request.method,
        "path": request.url.path,
    }
    if exc.status_code >= 500:
        logger.error("Request failed: %s", exc.message, exc_info=exc, extra={"context": context})
    else:
        logger.info("Request refused: %s", exc.message, extra={"context": context})
