"""Reading a list a page at a time, by cursor.

Every list Cylist serves is read this way (CYLIST-130): a page of at most
:data:`MAX_LIMIT` rows, and the id of the last of them to ask for the next.
Before it, a board answered with every card it held, descriptions and all —
at tens of thousands of cards, one fetch was a hundred megabytes of JSON.

Keyset rather than offset. An offset counts rows from the top every time, so
page 200 costs two hundred pages of reading, and a card added above page 3
while you are on it shows you one row twice. A cursor says *where* the last
page ended, so the next one starts there however much has changed above it.

The cursor is the last row's id, as ``after=<uuid>``, rather than an opaque
token: an id is something a client already has, can log, and can hand to any
other endpoint. The order a list is read in is its own business — a board by
position, cold storage by when the work finished, a directory by name — so the
cursor row's sort key is looked up from the id, rather than the id having to
*be* the sort key. That costs one indexed lookup per page after the first.

The rule every caller has to keep: the last of ``keys`` is unique (the id), so
two rows never tie and no row falls between two pages. The keys must also be
non-null, because a row comparison with a NULL in it is neither true nor false
and would drop the row from every page — coalesce a nullable key first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import ColumnElement, Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute
from sqlalchemy.orm.interfaces import ORMOption

from app.core.errors import UnprocessableRequestError

DEFAULT_LIMIT = 50
"""What a page holds when the caller does not say."""

MAX_LIMIT = 100
"""The most a page may hold, whatever the caller asks for. A list is read a
page at a time precisely so that no single response is unbounded."""


type SortKey = ColumnElement[Any] | QueryableAttribute[Any]
"""A column, or an expression over columns, that a list is ordered by."""


class Identified(Protocol):
    """A row with an id, which is what a cursor names."""

    @property
    def id(self) -> UUID: ...


@dataclass(frozen=True)
class PageRequest:
    """Where a page starts and how much of it to read."""

    after: UUID | None = None
    """The last row of the previous page. None for the first page."""
    limit: int = DEFAULT_LIMIT


FIRST_PAGE = PageRequest()


@dataclass(frozen=True)
class Window[RowT]:
    """One page of rows, and the cursor for the next — None on the last page."""

    rows: list[RowT]
    next: UUID | None


def cursor_gone(after: UUID) -> UnprocessableRequestError:
    """The refusal for a cursor whose row has left the list it was read from.

    Deleted, moved to another column, archived — whichever, there is no longer
    a place in this list to continue from, and guessing one would skip or
    repeat rows without saying so. A client reads the list again from the top.
    """
    return UnprocessableRequestError(
        "The row `after` names is no longer on this list; read it again from the first page.",
        details={"after": str(after)},
    )


async def keyset[RowT: Identified](
    session: AsyncSession,
    statement: Select[tuple[RowT]],
    *,
    keys: Sequence[SortKey],
    page: PageRequest,
    descending: bool = False,
    options: Sequence[ORMOption] = (),
) -> Window[RowT]:
    """Read one page of ``statement``, ordered by ``keys``.

    ``statement`` is the whole list, filtered but unordered; this orders it,
    starts it after ``page.after`` and cuts it at ``page.limit``. One row more
    than the page holds is read, so whether there is a next page is known
    without a count.

    ``keys`` all run in one direction — ascending, or with ``descending`` all
    descending — because a row comparison has a single direction. A list that
    wants "open first, then newest" says so with a key that is already the
    right way round, a boolean or a negated value.

    ``options`` are loader options for the rows themselves — deferring a
    column a list leaves out, say. They are kept off the statement passed in
    because the cursor lookup reads the keys alone, and an option about an
    entity the lookup does not select is an error rather than a no-op.

    Raises:
        UnprocessableRequestError: ``page.after`` names no row on this list.
    """
    columns = [key.expression if isinstance(key, QueryableAttribute) else key for key in keys]
    if page.after is not None:
        found = (
            await session.execute(
                statement.with_only_columns(*columns, maintain_column_froms=True)
                .where(columns[-1] == page.after)
                .limit(1)
            )
        ).first()
        if found is None:
            raise cursor_gone(page.after)
        bound = tuple_(
            *(literal(value, type_=key.type) for key, value in zip(columns, found, strict=True))
        )
        statement = statement.where(
            tuple_(*columns) < bound if descending else tuple_(*columns) > bound
        )
    ordering = [key.desc() if descending else key.asc() for key in columns]
    rows = list(
        await session.scalars(statement.options(*options).order_by(*ordering).limit(page.limit + 1))
    )
    if len(rows) <= page.limit:
        return Window(rows=rows, next=None)
    shown = rows[: page.limit]
    return Window(rows=shown, next=shown[-1].id)
