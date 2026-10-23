"""The query parameters every list endpoint pages by.

One dependency, so that `after` and `limit` read the same, are documented the
same and are capped the same on every list — a list that forgot its cap is the
bug CYLIST-130 was. See :mod:`app.services.paging` for how a page is read.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Query

from app.services.paging import DEFAULT_LIMIT, MAX_LIMIT, PageRequest


def page_request(
    after: UUID | None = Query(
        default=None,
        description=(
            "The `next` of the previous page: the id of the last row it held. "
            "Omit it for the first page."
        ),
    ),
    limit: int = Query(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"How many rows a page holds, at most {MAX_LIMIT}.",
    ),
) -> PageRequest:
    return PageRequest(after=after, limit=limit)


PageQuery = Depends(page_request)
"""``page: PageRequest = PageQuery`` on a list endpoint's signature."""
