"""Exceptions. Everything the SDK raises on purpose derives from `JevError`.

    JevError
    ├── JevConnectionError            network failure after all retries
    │   └── JevTimeoutError
    ├── JevResponseError              2xx whose body is not what the API documents
    └── JevAPIError                   non-2xx; .status_code, .body, .request_id
        ├── BadRequestError           400
        ├── AuthenticationError       401  bad or missing key
        ├── PermissionDeniedError     403
        ├── NotFoundError             404
        ├── UnprocessableEntityError  422  invalid request; .errors lists each problem
        ├── RateLimitError            429
        └── InternalServerError       5xx  (529 = overloaded)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class JevError(Exception):
    """Base class of every error the SDK raises."""


class JevConnectionError(JevError):
    """The request never got an HTTP response (DNS, TLS, connection reset, ...)."""


class JevTimeoutError(JevConnectionError):
    """The request timed out."""


class JevResponseError(JevError):
    """The API answered 2xx but the body does not have the documented shape."""

    def __init__(self, message: str, body: Any = None):
        self.body = body
        super().__init__(message)


class JevAPIError(JevError):
    """A non-2xx response from the API."""

    def __init__(self, status_code: int, body: Any, request_id: Optional[str] = None):
        self.status_code = status_code
        self.body = body
        self.request_id = request_id
        self.error_type, self.message = _describe(body)
        suffix = " (request %s)" % request_id if request_id else ""
        super().__init__("Jev API returned %d: %s%s" % (status_code, self.message, suffix))


class BadRequestError(JevAPIError):
    pass


class AuthenticationError(JevAPIError):
    pass


class PermissionDeniedError(JevAPIError):
    pass


class NotFoundError(JevAPIError):
    pass


class UnprocessableEntityError(JevAPIError):
    """The request failed validation. `errors` is the API's list of {loc, msg, type, ...}."""

    @property
    def errors(self) -> List[Dict[str, Any]]:
        detail = self.body.get("detail") if isinstance(self.body, dict) else None
        return detail if isinstance(detail, list) else []


class RateLimitError(JevAPIError):
    pass


class InternalServerError(JevAPIError):
    pass


_BY_STATUS = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def error_for_status(status_code: int, body: Any, request_id: Optional[str] = None) -> JevAPIError:
    cls = _BY_STATUS.get(status_code)
    if cls is None:
        cls = InternalServerError if status_code >= 500 else JevAPIError
    return cls(status_code, body, request_id)


def _describe(body: Any):
    """(error_type, message) from the API's two error shapes:

        {"detail": {"error_type": "authentication_error", "message": "..."}}
        {"detail": [{"loc": ["body", "questions", "q", "choice", "criteria"], "msg": "...", ...}]}
    """
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return detail.get("error_type"), str(detail.get("message") or detail)
    if isinstance(detail, list) and detail and all(isinstance(e, dict) for e in detail):
        parts = []
        for e in detail:
            loc = ".".join(str(p) for p in e.get("loc", ()) if p != "body")
            parts.append("%s: %s" % (loc, e.get("msg")) if loc else str(e.get("msg")))
        return "validation_error", "; ".join(parts)
    return None, str(detail)
