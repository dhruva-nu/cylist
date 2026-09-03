"""Reports read off the audit trail.

One endpoint so far: what a project's day amounted to. It reads the trail and
writes nothing, so it needs no more than the ``read`` scope — the same scope
that can already see every entry it is built from.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.project import Project
from app.routers.projects import resolved_project
from app.schemas.reports import DayReport
from app.services import reports

router = APIRouter(prefix="/projects", tags=["reports"])


@router.get(
    "/{project_ref}/reports/day",
    response_model=DayReport,
    summary="A day's report",
    responses={422: {"description": "That is not a time zone this server knows."}},
)
async def day_report(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    day: date | None = Query(
        default=None,
        alias="date",
        description="Which day, as `YYYY-MM-DD`. Defaults to today in `timezone`.",
        examples=["2026-09-02"],
    ),
    timezone: str | None = Query(
        default=None,
        description=(
            "IANA zone the day is cut by, e.g. `Asia/Kolkata`. Defaults to UTC — "
            "pass the caller's own zone, or a 9pm change lands in tomorrow's report."
        ),
        examples=["Asia/Kolkata"],
    ),
) -> DayReport:
    """What happened on this project on one day, grouped by card.

    Everything here is already in `/activity`; this is that trail cut at the
    boundaries of one local day and turned into an account of the work — each
    card that was touched, what happened to it in order, and which cards
    ended the day in the board's last column.

    A day means midnight to midnight in `timezone`, not in UTC, because the day
    being asked about is the one the asker just lived. The window it settled on
    comes back as `starts_at` and `ends_at` so there is no doubt about what was
    covered.

    `markdown` is the whole report as a paste-ready note. It is written here
    rather than by each client so a stand-up note copied out of the browser and
    one written by an agent say the same thing.

    A card's moves are reported as the one move they amounted to: a card
    dragged To do → In progress → Dev in a day got from To do to Dev, and the
    columns in between are where it was passing through. `moves` in that
    entry's payload says how many drags it stands for, and the card's own
    `/history` still holds every one of them.

    Changes that altered nothing — a card dragged within its own column, a form
    saved without an edit — are left out, exactly as they are in a card's
    history. `/activity` remains the place that answers "who touched what".
    """
    zone = reports.zone_for(timezone)
    return await reports.for_day(session, project, day=day or reports.today_in(zone), zone=zone)
