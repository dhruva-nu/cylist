"""Turning API responses into something readable in a terminal.

Two rules hold everywhere in here:

* Every command supports ``--json``, and the JSON is the API's own response,
  unshaped. A script that pipes ``cylist`` into ``jq`` should see exactly what
  it would have seen from ``curl``, so the CLI never becomes a second, subtly
  different API.
* The human rendering is plain ASCII with no colour and no box-drawing. It goes
  into logs, tickets and chat messages far more often than anyone expects.
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from collections.abc import Iterable, Sequence
from typing import Any

MIN_TERMINAL_WIDTH = 60
FALLBACK_WIDTH = 100


def terminal_width() -> int:
    """The usable width, clamped so a tiny window does not mangle a table."""
    width = shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns
    return max(MIN_TERMINAL_WIDTH, width)


def emit_json(payload: Any) -> None:
    """Print a response as JSON, one document, newline-terminated."""
    print(json.dumps(payload, indent=2, sort_keys=False, default=str))


def echo(line: str = "") -> None:
    print(line)


def warn(line: str) -> None:
    """Say something to the operator that must not land in a pipe."""
    print(line, file=sys.stderr)


# --- Tables ----------------------------------------------------------------


def table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], *, empty: str = "Nothing here."
) -> None:
    """Print a left-aligned table sized to its content.

    The last column is not padded, so copying a line out of the terminal does
    not bring a run of trailing spaces with it.
    """
    if not rows:
        echo(empty)
        return

    columns = len(headers)
    widths = [len(header) for header in headers]
    for row in rows:
        for index in range(columns):
            widths[index] = max(widths[index], len(row[index]))

    echo(_row(headers, widths))
    echo(_row(["-" * width for width in widths], widths))
    for row in rows:
        echo(_row(row, widths))


def _row(cells: Sequence[str], widths: Sequence[int]) -> str:
    padded = [cell.ljust(widths[index]) for index, cell in enumerate(cells[:-1])]
    padded.append(cells[-1])
    return "  ".join(padded).rstrip()


def fields(pairs: Iterable[tuple[str, str]]) -> None:
    """Print aligned ``label: value`` lines for a single record."""
    items = [(label, value) for label, value in pairs if value != ""]
    if not items:
        return
    width = max(len(label) for label, _ in items)
    for label, value in items:
        echo(f"{label.rjust(width)}  {value}")


def heading(text: str) -> None:
    echo(text)
    echo("=" * len(text))


def wrap(text: str, width: int, indent: str = "") -> list[str]:
    """Wrap a paragraph, preserving the blank lines between paragraphs."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(
            textwrap.wrap(
                paragraph,
                width=max(8, width),
                initial_indent=indent,
                subsequent_indent=indent,
            )
            or [indent]
        )
    return lines


def truncate(text: str, width: int) -> str:
    """Shorten to ``width`` characters, marking that something was cut."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"
