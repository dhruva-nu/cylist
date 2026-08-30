"""UUIDv7 generation."""

from __future__ import annotations

import time
from uuid import UUID

from app.core.ids import uuid7


def test_has_version_and_variant_bits() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_embeds_the_current_timestamp() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000

    embedded_ms = value.int >> 80
    assert before <= embedded_ms <= after


def test_ids_sort_chronologically() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second


def test_ids_are_unique() -> None:
    values: set[UUID] = {uuid7() for _ in range(10_000)}
    assert len(values) == 10_000
