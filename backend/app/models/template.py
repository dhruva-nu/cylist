"""Task templates: what kind of card a task is, which columns it may sit in,
and the sub-stages it passes through in each before it can move on.

A **template** is a kind of card — "Hotfix", "Design task". It carries no
fields onto the task beyond the rule itself: a **stage** names one column its
cards may sit in, and the sub-stages — "Drafted", "Reviewed", "Merged" — a
card of this template passes through while it is there.

Two consequences worth stating, because they are choices:

* a template with no stages is unrestricted — silence is not a ban, so a
  project can name its templates today and shape their rules next week;
* a card created from a templated stage gets that stage's labels loaded into
  its own ``sub_statuses`` — the same click-through progress bar every card
  carries — the moment it lands in the column, and cannot leave until it is on
  the last one. See :func:`app.services.templates.landing_sub_stages` for
  where that happens and :func:`app.services.tasks._refuse_stage_incomplete`
  for where leaving early is refused.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.board import MAX_OUTCOMES, OUTCOME_LABEL_MAX_LENGTH, BoardColumn

NAME_MAX_LENGTH = 80
SUB_STAGE_LABEL_MAX_LENGTH = 60
SUB_STAGE_MAX_COUNT = 4
"""Matches ``Task.sub_statuses``: a template's stage labels are loaded onto the
card's own progress bar, which holds at most this many."""


class TaskTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A kind of card, and the rule for where its cards go."""

    __tablename__ = "task_template"
    __table_args__ = (Index("ix_task_template_project_id_name", "project_id", "name", unique=True),)

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    """Unique on the project: a card names its template by sight, and two
    templates called "Hotfix" make every rule ambiguous to read."""

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """What kind of card this is. Optional: "Hotfix" already explains itself,
    and a required second box between wanting one and having one buys
    nothing."""

    stages: Mapped[list[TemplateStage]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TemplateStage.created_at",
        lazy="selectin",
    )


class TemplateStage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One column a template's cards may sit in, and the sub-stages a card
    passes through there.

    A card created from the template — or arriving at this column by a later
    move — has these labels loaded onto its own ``sub_statuses``, the same
    click-through progress bar every card carries; see
    :func:`app.services.templates.landing_sub_stages`. The card cannot leave
    the column until it is on the last label, whichever column it is headed to
    next — see :func:`app.services.tasks._refuse_stage_incomplete`.
    """

    __tablename__ = "template_stage"
    __table_args__ = (
        # One stage per column per template: two would be two answers to "what
        # does this column require", and a card is only ever in one column.
        Index("ix_template_stage_template_id_column_id", "template_id", "column_id", unique=True),
        # Mirrors Task.sub_statuses: these labels are loaded onto that field
        # verbatim, so a stage cannot promise more than it can hold.
        CheckConstraint(
            f"cardinality(sub_stage_labels) <= {SUB_STAGE_MAX_COUNT}",
            name="sub_stage_max_count",
        ),
        # Mirrors BoardColumn.outcomes for the same reason: this list names a
        # subset of that one, and a subset cannot be longer than the set.
        CheckConstraint(
            f"cardinality(allowed_outcomes) <= {MAX_OUTCOMES}",
            name="allowed_outcome_max_count",
        ),
    )

    template_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task_template.id", ondelete="CASCADE"),
        nullable=False,
    )

    column_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("board_column.id", ondelete="CASCADE"),
        nullable=False,
    )
    """``ON DELETE CASCADE``: a board no longer has this column, so a template
    can no longer say anything about it."""

    sub_stage_labels: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(SUB_STAGE_LABEL_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'"),
    )
    """The sub-stages a card of this template passes through in this column,
    left to right — loaded onto the card's own ``sub_statuses`` the moment it
    lands here. May be empty: a template can name a column its cards are
    allowed in without asking anything of them there."""

    allowed_outcomes: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(OUTCOME_LABEL_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'"),
    )
    """Which of the column's outcomes this template's cards may end on.

    Names rather than indices, unlike the index a card carries: this is written
    by hand against a list the author is reading, and a template that survived
    a reordering of the column by pointing at the wrong section would be worse
    than one that survived a rename by no longer matching.

    Empty is unrestricted, the same silence ``stages`` itself keeps: a template
    may say where its cards go without saying how they are allowed to end. Only
    meaningful on the stage for a column that has outcomes at all — every other
    stage's list is empty and stays that way. See
    :func:`app.services.templates.require_outcome_permitted`."""

    template: Mapped[TaskTemplate] = relationship(back_populates="stages")
    column: Mapped[BoardColumn] = relationship(lazy="selectin")
