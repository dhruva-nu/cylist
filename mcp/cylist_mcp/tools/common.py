"""What every tool module shares: the error-to-result wrapper and the small checks.

**Errors are results, not exceptions.** A tool that raises gives the model an
opaque "error executing tool". Every tool body runs inside
:func:`as_tool_result`, which catches :class:`CylistError` and returns a
``CallToolResult`` with ``is_error`` set, a sentence saying what the server
refused and why, and the API's own error envelope as structured content. The
model can then fix its arguments and try again, which is the whole difference
between a recoverable and an unrecoverable failure.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import CallToolResult, TextContent
from pydantic import Field

from cylist_mcp.errors import CylistError

ProjectRef = Annotated[str, Field(description="Project key such as 'ATL', or its id.")]
"""The ``project`` argument most tools take, described the same way everywhere."""

TaskRef = Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")]
"""The ``task`` argument most task tools take, described the same way everywhere."""

TASK_STATUSES = ("active", "hold", "blocked", "cancelled")


async def as_tool_result(call: Callable[[], Awaitable[dict[str, Any]]]) -> CallToolResult:
    """Run a tool body, turning a failure into a result the model can read."""
    try:
        payload = await call()
    except CylistError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=exc.message)],
            structured_content=exc.envelope(),
            is_error=True,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, default=str))],
        structured_content=payload,
        is_error=False,
    )


def check_choice(field: str, value: str, allowed: tuple[str, ...]) -> None:
    """Reject a bad enum locally, naming the alternatives.

    The server would reject it too, but its validation error is a list of
    Pydantic field errors. A sentence naming the three options is something a
    model can act on in one turn.
    """
    if value not in allowed:
        raise CylistError(
            f"{field} must be one of {', '.join(repr(item) for item in allowed)}, not {value!r}.",
            code="validation_failed",
            details={"field": field, "allowed": list(allowed)},
        )


def check_status(status: str) -> None:
    check_choice("status", status, TASK_STATUSES)


def project_of(task: dict[str, Any]) -> str:
    """The project key behind a task, from its reference: 'ATL-41' -> 'ATL'.

    Used in place of ``project_id`` wherever the value may reach the model in
    a message: "no person called 'Le' in ATL's members" is actionable, the
    same sentence with a UUID in it is not.

    Split from the front rather than the back, because a sub-task's reference
    carries two numbers — ``ATL-41-2`` — and only the key is wanted. Keys
    cannot contain a dash, so the first segment is always it.
    """
    key = str(task.get("reference", "")).split("-", 1)[0]
    return key or str(task["project_id"])
