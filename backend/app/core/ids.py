"""Identifier generation.

Cylist uses UUID version 7 for every primary key. Unlike UUID4 it embeds a
millisecond timestamp in its leading bits, so rows sort chronologically by id
and B-tree inserts stay at the right-hand edge of the index instead of
scattering across it.

Python 3.14 added ``uuid.uuid7`` to the standard library; we target 3.12, so
the 20 lines below implement RFC 9562 §5.7 directly rather than taking a
dependency for it.
"""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_UNIX_TS_MS_BITS = 48
_RAND_A_BITS = 12
_RAND_B_BITS = 62


def uuid7() -> UUID:
    """Return a new UUID version 7.

    Layout (128 bits, most significant first):

    ==========  =======  =========================================
    Field       Bits     Content
    ==========  =======  =========================================
    unix_ts_ms  48       Milliseconds since the Unix epoch
    ver         4        Constant ``0b0111``
    rand_a      12       Random
    var         2        Constant ``0b10``
    rand_b      62       Random
    ==========  =======  =========================================

    Ordering is guaranteed across milliseconds. Two ids minted within the same
    millisecond are ordered arbitrarily, which is the behaviour of the RFC's
    basic form and is sufficient for database keys.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(_RAND_A_BITS)
    rand_b = secrets.randbits(_RAND_B_BITS)

    value = timestamp_ms << (128 - _UNIX_TS_MS_BITS)
    value |= 0x7 << 76  # version
    value |= rand_a << 64
    value |= 0b10 << 62  # variant
    value |= rand_b
    return UUID(int=value)
