"""Domain errors and their HTTP translation.

Services raise these; routers stay free of status codes and error shaping.
Every error reaches the client as::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, client-facing failures."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    @property
    def headers(self) -> dict[str, str]:
        """Response headers this refusal carries, beyond the envelope.

        Empty for nearly every error. The ones with something to say — a
        ``Retry-After``, a ``WWW-Authenticate`` — override this rather than
        teaching the handler about each class.
        """
        return {}


class UnauthorizedError(AppError):
    """No valid credentials were presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"

    @property
    def headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}


class TooManyRequestsError(AppError):
    """The caller has tried this too often, too quickly (CYLIST-121).

    Carries ``Retry-After`` in whole seconds, both as a header and in
    ``details.retry_after``, so a client can wait exactly as long as it must.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(message, details={"retry_after": self.retry_after})

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


class ForbiddenError(AppError):
    """Valid credentials, but *who is asking* may not do this.

    The rule (CYLIST-146): 403 is "not allowed by role" — a missing scope, a
    role that does not carry the permission, not being the project's admin,
    being nobody where a person is needed. Change the caller and the same
    request would pass. A request that no caller could make, because it would
    break an invariant, is :class:`UnprocessableRequestError` instead.
    """

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
    """Well-formed, but it breaks a business rule.

    Whoever sent it: leaving a project with no admin, archiving the last
    person who can sign in, naming a column on another board. A refusal that
    depends on the caller is :class:`ForbiddenError`.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unprocessable"


def not_on_this_project(noun: str, **details: object) -> UnprocessableRequestError:
    """The one refusal for a request that names something not on its project.

    One status and one sentence, whether the thing is on another project or
    does not exist at all (CYLIST-146). Clients branch on ``code``, so two
    answers for one mistake meant every caller special-cased each endpoint;
    and a refusal that told the two apart would say which ids exist on
    projects the caller cannot see.

    Args:
        noun: What was named — ``column``, ``template``, ``goal``, ``task``.
        details: The offending ids, stringified, under the field they came in.
    """
    return UnprocessableRequestError(
        f"That {noun} is not on this project.",
        details={field: str(value) for field, value in details.items()},
    )


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


INTERNAL_ERROR_MESSAGE = "Something went wrong on our side. Quote the request id if you report it."


def internal_error_response() -> JSONResponse:
    """The 500 every unexpected exception becomes, in the usual envelope.

    Built here and sent from two places: the ``Exception`` handler below, and
    :class:`app.core.request_log.RequestLogMiddleware`, which is the only
    layer that can still put the request id on it — Starlette runs an
    ``Exception`` handler outside every user middleware, so a response sent
    from there would leave without the header.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("internal_error", INTERNAL_ERROR_MESSAGE),
    )


_SAFE_ERROR_KEYS = ("type", "loc", "msg")
"""What of a Pydantic error reaches the client.

Not ``input``: for a model-level validator that is the whole body, so a
vault node refused for its kind would echo its plaintext secret back. Not
``ctx`` either: it can carry the rejected value, or the exception object a
custom validator raised, whose message may quote the input. ``url`` is
Pydantic's documentation link and says nothing a client needs.
"""


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [{key: error[key] for key in _SAFE_ERROR_KEYS if key in error} for error in exc.errors()]


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render every failure in the same envelope."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        _log_app_error(request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
            headers=exc.headers or None,
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
                {"fields": jsonable_encoder(_safe_validation_errors(exc))},
            ),
        )

    # The database's own refusals, for when a pre-check lost a race or a
    # value slipped past the schema. Each of these is a Cylist bug to fix where
    # it happened, so it is logged with its traceback; but the client still
    # gets the status the request deserved rather than a 500. The driver's
    # message is never echoed: it quotes the offending row's values.

    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        _log_unhandled(request, exc, "Integrity error reached the handler")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_error_body(
                "conflict", "That clashes with a change made at the same moment. Try again."
            ),
        )

    @app.exception_handler(DataError)
    async def _data_error(request: Request, exc: DataError) -> JSONResponse:
        _log_unhandled(request, exc, "Data error reached the handler")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("unprocessable", "A value is too long or out of range."),
        )

    @app.exception_handler(OSError)
    async def _os_error(request: Request, exc: OSError) -> JSONResponse:
        # The blob store's disk, or the database's socket: either way the
        # request was fine and the server could not get at what it needed.
        _log_unhandled(request, exc, "Storage unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_body(
                "service_unavailable", "Storage is unavailable right now. Try again shortly."
            ),
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, __: Exception) -> JSONResponse:
        # A backstop only. RequestLogMiddleware catches, logs and answers
        # everything raised inside it; this sees only what escapes the base
        # path middleware outside it, so logging here would be a second line.
        return internal_error_response()


def _log_unhandled(request: Request, exc: Exception, message: str) -> None:
    logger.error(
        message,
        exc_info=exc,
        extra={"context": {"method": request.method, "path": request.url.path}},
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
