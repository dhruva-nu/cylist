"""Time helpers.

Centralised so that every timestamp is timezone-aware UTC, and so tests have a
single place to freeze if they ever need to.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)
