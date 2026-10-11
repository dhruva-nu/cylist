"""A pull request on a card says how far it got, not only where it is.

``task.pr_refs`` held a URL per pull request and nothing else, so a card could
say that three pull requests carried its work and not which of them had landed
— the one thing a reader of the board actually wants from them. Each entry
becomes an object, ``{"url": …, "state": …}``, where the state is ``raised``,
``approved`` or ``merged``.

So the column changes type: a ``varchar[]`` of references becomes a ``jsonb``
array of those objects. The pair is one value on purpose — kept as two
parallel arrays, a writer could change the state array and forget the URL
array, and the card would report that the wrong pull request had landed. The
column stays on the row for the reason ``0034`` gave when it put the list
there: it is read on every board query and pointed at from nowhere, and rows
of its own would buy another SELECT on the board's hottest path.

Nothing is lost on the way up. Every card that named pull requests keeps all of
them, in the order it named them, each at ``raised`` — which is true of a
reference nobody has said anything else about, and the state every new one
starts in. A card that named none keeps the empty list.

``ck_task_pr_ref_max_count`` still caps a card at 20, counted now with
``jsonb_array_length``, and ``ck_task_pr_refs_array`` is new: it holds that
this column is an array even when it is empty, so every reader can loop over
it. What is inside an entry is held by the schema rather than by a CHECK — a
CHECK may not contain the subquery that reaching into a document element by
element would need.

The downgrade keeps every URL and drops the states, because ``0034``'s shape
has nowhere to put them. That is unlike ``0034``'s own downgrade, which refuses
a card naming several pull requests rather than choose one to keep: there,
reversing would have had to pick which pull request a card was carried by and
throw the rest away. Here no pull request is lost and no choice is invented —
the card goes back to saying where each one is, which is all it could say
before this revision.

Revision ID: 0036
Revises: 0034
Created: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_PR_REFS = 20
"""Kept here rather than imported from the model, as ``0034`` kept it: a
revision has to keep meaning what it meant, and the model's ceiling may move."""

REF_MAX_LENGTH = 200
"""The width the references were stored at, for the column the downgrade
restores. Same reason to spell it out here."""


def upgrade() -> None:
    # Written beside the old column rather than over it: an `ALTER COLUMN …
    # USING` cannot build these objects, because turning an array into one JSON
    # document per row needs an aggregate over `unnest`, and a transform
    # expression may not contain a subquery.
    op.add_column(
        "task",
        sa.Column(
            "pr_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # `WITH ORDINALITY` is what keeps the order a card named them in: without
    # it the aggregate is free to return the objects in any order, and a card's
    # first pull request is usually its main one. The counter is called
    # `ordinal` rather than `position` because `task.position` is a column of
    # its own, and an unqualified `position` is ambiguous between the two.
    op.execute(
        sa.text(
            """
            UPDATE task
               SET pr_refs_json = named.refs
              FROM (
                    SELECT t.id,
                           jsonb_agg(
                               jsonb_build_object('url', ref, 'state', 'raised')
                               ORDER BY ordinal
                           ) AS refs
                      FROM task AS t,
                           unnest(t.pr_refs) WITH ORDINALITY AS entry(ref, ordinal)
                     GROUP BY t.id
                   ) AS named
             WHERE task.id = named.id
            """
        )
    )

    # The bare name, not the stored `ck_task_pr_ref_max_count`: NAMING_CONVENTION
    # is applied on the way out, so spelling the prefix here produces
    # `ck_task_ck_task_pr_ref_max_count` and finds nothing.
    op.drop_constraint("pr_ref_max_count", "task", type_="check")
    op.drop_column("task", "pr_refs")
    op.alter_column("task", "pr_refs_json", new_column_name="pr_refs")

    op.create_check_constraint("pr_refs_array", "task", "jsonb_typeof(pr_refs) = 'array'")
    # The CASE keeps this constraint from being the one that reports a document
    # which is not a list: `jsonb_array_length` errors on anything but an
    # array, an error beats a violation, and Postgres may check two constraints
    # in either order. `pr_refs_array` is what refuses that document, by name.
    op.create_check_constraint(
        "pr_ref_max_count",
        "task",
        "jsonb_array_length("
        "CASE WHEN jsonb_typeof(pr_refs) = 'array' THEN pr_refs ELSE '[]'::jsonb END"
        f") <= {MAX_PR_REFS}",
    )


def downgrade() -> None:
    op.add_column(
        "task",
        sa.Column(
            "pr_refs_text",
            postgresql.ARRAY(sa.String(length=REF_MAX_LENGTH)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # `->> 'url'` rather than `#>> '{url}'` for the same reason as above: one
    # text value out of each object, in the order the document lists them,
    # which `WITH ORDINALITY` is again what fixes.
    op.execute(
        sa.text(
            """
            UPDATE task
               SET pr_refs_text = named.refs
              FROM (
                    SELECT t.id,
                           array_agg(entry.ref ->> 'url' ORDER BY ordinal) AS refs
                      FROM task AS t,
                           jsonb_array_elements(t.pr_refs)
                               WITH ORDINALITY AS entry(ref, ordinal)
                     GROUP BY t.id
                   ) AS named
             WHERE task.id = named.id
            """
        )
    )

    op.drop_constraint("pr_ref_max_count", "task", type_="check")
    op.drop_constraint("pr_refs_array", "task", type_="check")
    op.drop_column("task", "pr_refs")
    op.alter_column("task", "pr_refs_text", new_column_name="pr_refs")

    op.create_check_constraint(
        "pr_ref_max_count",
        "task",
        f"cardinality(pr_refs) <= {MAX_PR_REFS}",
    )
