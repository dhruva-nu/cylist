"""Token buckets, for the endpoints that can be hammered (CYLIST-121).

A bucket per key — a client address, an email — holds up to ``burst`` tokens
and refills at ``per_minute``. Each attempt takes one; an empty bucket says how
long until the next token, which is what ``Retry-After`` carries.

In-process. Each environment runs one process, so a dictionary here is the
whole truth about who has been knocking. A deployment may run more (DEPLOY.md,
"More than one worker"), and then each process counts only what it saw —
N workers let N times as much through. This is the class to back with
Postgres when that matters; its callers will not change.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_KEYS = 10_000
"""How many buckets to hold before sweeping the full ones out.

A bucket that has refilled to the brim is indistinguishable from one that was
never made, so dropping it loses nothing; keeping every address that ever
tried once would be a slow leak over a public endpoint.
"""


@dataclass(slots=True)
class _Bucket:
    tokens: float
    at: float


class TokenBuckets:
    """One bucket per key, made on first sight."""

    def __init__(
        self,
        *,
        burst: int,
        per_minute: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if burst < 1 or per_minute <= 0:
            raise ValueError("a bucket needs a positive burst and refill rate")
        self.burst = burst
        self.per_second = per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def take(self, key: str) -> float | None:
        """Spend one token for ``key``.

        Returns ``None`` when there was one to spend, otherwise the seconds
        until there will be — never less than a whole second, so a caller can
        put it straight on a ``Retry-After`` header.
        """
        moment = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= MAX_KEYS:
                self._sweep(moment)
            bucket = _Bucket(tokens=float(self.burst), at=moment)
            self._buckets[key] = bucket
        else:
            self._refill(bucket, moment)

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return max(1.0, math.ceil((1.0 - bucket.tokens) / self.per_second))

    def _refill(self, bucket: _Bucket, moment: float) -> None:
        elapsed = max(0.0, moment - bucket.at)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.per_second)
        bucket.at = moment

    def _sweep(self, moment: float) -> None:
        """Forget every bucket that would be full if asked."""
        for key, bucket in list(self._buckets.items()):
            self._refill(bucket, moment)
            if bucket.tokens >= self.burst:
                del self._buckets[key]
