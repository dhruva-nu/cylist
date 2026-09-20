"""A name on the board that belongs to a machine.

CYLIST-47 asks for two things, and only one of them needs the database. A card
now lands on whoever wrote it unless it says otherwise, which is a service
rule; this is the other half — somebody to hand the card to when the work is
meant for a machine.

**What arrives.** ``person.is_agent``, and one row with it set: ``Agent``. It
is an ordinary directory entry in every other respect, ``team`` because it does
the work, and it is put on every existing project so that a board can name it
the moment this lands. New projects get it from
:func:`app.services.projects.create`, which joins it the way it joins whoever
started the project.

**Why a flag rather than a name.** The name is editable — a board that prefers
"Claude" should be able to say so — and a lookup that matched on it would
either quietly stop finding the row or find a second one somebody typed. The
index is partial and unique, so "there is one agent" is the table's answer
rather than a rule every caller has to remember. It is deliberately not a
:class:`~app.models.person.PersonKind`: the agent is on the team, and what this
says is whether there is anybody behind the name.

**It has no email and no password**, so it cannot sign in and cannot be
invited — :func:`app.services.people.invite` refuses it in as many words. A
machine reaches Cylist with an API token minted by whoever runs it, which is
how it reached Cylist before this revision too.

**The downgrade drops the column and keeps the row.** Deleting it would take
with it every card assigned to it, every comment it wrote and every upload it
attributed — and a card cannot be assigned to nobody. So what comes back
through the downgrade is a directory entry called ``Agent``, on the boards it
was on, indistinguishable from a colleague who never signs in. That is what it
was before this revision named the difference.

Revision ID: 0027
Revises: 0026
Created: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.ids import uuid7

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_NAME = "Agent"
"""Mirrors ``app.models.person.AGENT_NAME``."""

_AGENT_TITLE = "Machine, worked through the API"
"""Mirrors ``app.models.person.AGENT_TITLE``."""

_AGENT_RESPONSIBILITIES = (
    "Works the cards it is given, through the API. It signs in as nobody: it "
    "carries a token minted by whoever runs it."
)
"""Mirrors ``app.models.person.AGENT_RESPONSIBILITIES``."""

_AGENT_COLOUR = "#4A7B8C"
"""What ``app.core.palette.colour_for("Agent")`` picks — the slate blue.

Written out rather than imported, for the reason 0024 writes its palette out:
a migration describes the database as it was at the moment it ran, and a
palette that gains a ninth colour later must not change what this revision did.
"""


def upgrade() -> None:
    op.add_column(
        "person",
        sa.Column("is_agent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_person_is_agent",
        "person",
        ["is_agent"],
        unique=True,
        postgresql_where=sa.text("is_agent"),
    )

    _put_an_agent_on_every_board()


def _put_an_agent_on_every_board() -> None:
    """Write the agent into the directory and onto every project.

    Onto every project, because membership is what makes a name assignable: an
    entry that every board had to be told about by hand would do nothing until
    somebody found the People screen, and this revision exists precisely so
    that there is somebody to hand a card to. It joins with no role, which is
    what everybody added to a board after its admin starts out as.
    """
    connection = op.get_bind()

    agent_id = uuid7()
    connection.execute(
        sa.text(
            """
            INSERT INTO person
                (id, name, kind, title, responsibilities, colour, is_agent)
            VALUES
                (:id, :name, 'team', :title, :responsibilities, :colour, true)
            """
        ),
        {
            "id": agent_id,
            "name": _AGENT_NAME,
            "title": _AGENT_TITLE,
            "responsibilities": _AGENT_RESPONSIBILITIES,
            "colour": _AGENT_COLOUR,
        },
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO project_member (project_id, person_id)
            SELECT id, :person_id FROM project
            """
        ),
        {"person_id": agent_id},
    )


def downgrade() -> None:
    op.drop_index("ix_person_is_agent", table_name="person")
    op.drop_column("person", "is_agent")
