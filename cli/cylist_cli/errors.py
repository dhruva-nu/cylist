"""The two failures a command can end with.

Both are caught in :mod:`cylist_cli.main`, printed as one line on stderr and
turned into a non-zero exit status. Nothing else is allowed to reach the
terminal: a traceback tells the user about our call stack when what they need
to know is which name was ambiguous.
"""

from __future__ import annotations

from typing import Any


class CylistError(Exception):
    """Something the user can act on. Rendered as ``error: <message>``."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    @property
    def code(self) -> str:
        """The machine-readable code, mirroring the API's error envelope."""
        return "cli_error"


class ApiError(CylistError):
    """The server refused the request.

    Carries the server's own ``error.code`` and ``error.message`` so the CLI
    repeats what the API said rather than inventing its own wording for a
    condition only the API can judge.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str = "error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.status_code = status_code
        self._code = code

    @property
    def code(self) -> str:
        return self._code
