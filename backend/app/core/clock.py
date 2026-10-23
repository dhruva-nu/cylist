"""Time helpers.

Centralised so that every timestamp is timezone-aware UTC, and so tests have a
single place to freeze if they ever need to.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def today(zone: ZoneInfo) -> date:
    """The date it is right now, where the caller is."""
    return datetime.now(zone).date()


def epoch() -> float:
    """Seconds since the Unix epoch, for the places that sign or compare raw timestamps."""
    return time.time()
