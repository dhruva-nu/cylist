"""Failures, in a shape a tool can hand back to a model."""

from __future__ import annotations

from typing import Any


class CylistError(Exception):
    """Something went wrong that the agent should be told about plainly."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "cylist_error",
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def envelope(self) -> dict[str, Any]:
        """The error as structured content, mirroring the API's own envelope."""
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.status_code is not None:
            error["status"] = self.status_code
        return {"error": error}
