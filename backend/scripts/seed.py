"""Fill a database with the Atlas / Hermes / Orbit sample data from the mock.

Usage::

    uv run python -m scripts.seed
    uv run python -m scripts.seed --force --yes

This exists so that a fresh checkout has something to look at: three projects
with real boards, real stalled work, real files and real credentials, rather
than a home screen reading "no projects yet".

Everything goes through :mod:`app.services`, never through the ORM directly.
That is not tidiness — it is the only way the seeded data obeys the same rules
the API does. A blocked task therefore carries a reason and a set of tagged
people because :func:`app.services.tasks.change_status` insists on them, not
because this file remembered to write them.

Four places where the mock and the database do not line up, and what is done
about each:

*People.* The mock keeps a copy of each person per project, with a different
role on each. The database has one global directory, so each person is written
once and takes their Atlas description, Atlas being the project the mock leads
with and the one where each of them is described most fully.

*Task numbers.* The mock's references are gapped — ATL-41, 38, 35, 33, 30, 27,
22, 19. Those gaps are the fingerprint of cards that existed and were deleted,
and ``project.task_counter`` is precisely the thing that remembers them. So the
counter is wound forward before each card rather than the numbers being
renumbered: ``ATL-41`` is the reference in the mock's screenshots, and a seed
that produced ``ATL-8`` would make every screenshot wrong.

*Uploads.* The seed writes readable placeholders — a couple of lines saying
what the file stands in for — under the mock's real names, types and
attribution. Fabricating an 812 MB archive or a byte-accurate ``.xlsx`` would
add nothing except the risk of somebody believing it. Anyone who downloads one
is told what it is by its own contents.

*Timestamps.* Comments are stamped when the seed runs, not on the mock's dates.
``created_at`` is a server default and forging it would mean reaching past the
services this script exists to go through.

The audit trail is deliberately left empty. :class:`~app.auth.principal.Principal`
requires a token id because every action through the API has a credential behind
it; a seed has none, and inventing one would either break the foreign key or
leave an unusable row in ``api_token``. More to the point, the seed is not a
change *to* a database — it is the database's starting state, and a hundred
entries all saying "this is seed data" would say less than an empty feed does.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.config import Settings, get_settings
from app.core.crypto import VaultCipher, cipher_for
from app.db import Database
from app.models.file import Blob, FileItem, Folder, ItemSource
from app.models.person import Person, PersonKind
from app.models.project import Project
from app.models.task import Task, TaskStatus, TaskType
from app.models.vault import VaultNodeKind, VaultSecret, VaultTree
from app.schemas.columns import ColumnCreate, ColumnUpdate
from app.schemas.files import FolderCreate, LinkCreate
from app.schemas.people import PersonCreate
from app.schemas.projects import ProjectCreate
from app.schemas.tasks import CommentCreate, TaskCreate, TaskMove, TaskStatusChange
from app.schemas.vault import SecretCreate, VaultNodeCreate, VaultTreeCreate
from app.services import columns, files, people, projects, tasks, vault
from app.storage import BlobStore, LocalBlobStore

# --- The mock, as data -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersonSpec:
    """Someone in the directory.

    ``handle`` is the mock's own short key. It never reaches the database — it
    is only what the tasks, files and memberships below use to point at a
    person before their row exists.
    """

    handle: str
    name: str
    kind: PersonKind
    role: str
    responsibilities: str
    email: str
    colour: str


@dataclass(frozen=True, slots=True)
class TaskSpec:
    number: int
    title: str
    description: str
    type: TaskType
    due_date: date
    assignee: str
    column: int
    jira_ref: str | None = None
    pr_ref: str | None = None
    status: TaskStatus = TaskStatus.ACTIVE
    reason: str = ""
    waiting_on: tuple[str, ...] = ()
    comments: tuple[tuple[str, str], ...] = ()
    """``(author handle, body)``, added after any status change, which is the
    order the mock shows them in."""


@dataclass(frozen=True, slots=True)
class FileSpec:
    """One row of a folder listing: an upload, or a link out."""

    name: str
    added_by: str
    source: ItemSource
    url: str | None = None
    mime: str = "application/octet-stream"

    @property
    def is_link(self) -> bool:
        return self.url is not None

    @property
    def placeholder(self) -> bytes:
        """Content for an upload, which says what it is standing in for."""
        return (
            f"Cylist sample data.\n\n"
            f"This file stands in for {self.name!r}, so the file tree has "
            f"something real to download. It is not the document itself.\n"
        ).encode()


@dataclass(frozen=True, slots=True)
class FolderSpec:
    name: str
    items: tuple[FileSpec, ...] = ()
    children: tuple[FolderSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class SecretSpec:
    value: str
    username: str | None = None
    url: str | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class VaultNodeSpec:
    """A branch, or a credential. ``secret`` is what tells them apart."""

    name: str
    secret: SecretSpec | None = None
    children: tuple[VaultNodeSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeSpec:
    name: str
    nodes: tuple[VaultNodeSpec, ...]


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    key: str
    name: str
    description: str
    colour: str
    members: tuple[str, ...]
    columns: tuple[ColumnCreate, ...]
    tasks: tuple[TaskSpec, ...]
    root_items: tuple[FileSpec, ...] = ()
    """Files and links that sit at the top of the project, in its root folder.
    The mock puts a README and a brief here; before the root became a real row
    there was nowhere to put them."""
    folders: tuple[FolderSpec, ...] = ()
    trees: tuple[TreeSpec, ...] = ()


NO_CREDENTIAL = "(no credential — this entry is a bookmark)"
"""What goes in the value of a vault entry the mock stores only a URL for.

A secret node must hold something — ``SecretCreate.value`` has a minimum
length, because a credential nobody can read is a branch with extra steps — so
the entry says plainly that there is nothing to read.
"""


DIRECTORY: tuple[PersonSpec, ...] = (
    PersonSpec(
        handle="dn",
        name="Dhruva N",
        kind=PersonKind.TEAM,
        role="Tech lead",
        responsibilities=(
            "Owns architecture and the cutover plan. Escalation point for anything blocked."
        ),
        email="dhruva@think41.com",
        colour="#1D7D46",
    ),
    PersonSpec(
        handle="ak",
        name="Aditi K",
        kind=PersonKind.TEAM,
        role="Backend engineer",
        responsibilities="Payments, Stripe integration and webhook reliability.",
        email="aditi@think41.com",
        colour="#3B6FC2",
    ),
    PersonSpec(
        handle="rs",
        name="Rohan S",
        kind=PersonKind.TEAM,
        role="Backend engineer",
        responsibilities="Data migration scripts, PDF rendering and reporting.",
        email="rohan@think41.com",
        colour="#C77D00",
    ),
    PersonSpec(
        handle="mp",
        name="Meera P",
        kind=PersonKind.TEAM,
        role="QA & release",
        responsibilities="Test plans, staging sign-off and release notes.",
        email="meera@think41.com",
        colour="#7A6B9E",
    ),
    PersonSpec(
        handle="sf",
        name="Sanjay F",
        kind=PersonKind.CLIENT,
        role="Finance controller, Atlas",
        responsibilities=(
            "Approves anything touching tax, invoicing rules or vendor accounts (Avalara, Stripe)."
        ),
        email="s.fernandes@atlas.example",
        colour="#8E6A3D",
    ),
    PersonSpec(
        handle="lw",
        name="Lena W",
        kind=PersonKind.CLIENT,
        role="Legal counsel, Atlas",
        responsibilities="Signs off licences and contracts. Slow to respond — chase via Sanjay.",
        email="l.wright@atlas.example",
        colour="#5C6B73",
    ),
    PersonSpec(
        handle="pt",
        name="Priya T",
        kind=PersonKind.CLIENT,
        role="Product owner, Hermes",
        responsibilities="Prioritises the backlog; approves template copy.",
        email="priya@hermes.example",
        colour="#8E6A3D",
    ),
)


ATLAS = ProjectSpec(
    key="ATL",
    name="Atlas Billing Migration",
    description=(
        "Move invoicing off the legacy Java service onto the new FastAPI billing core. "
        "Cutover target Q4."
    ),
    colour="#1D7D46",
    members=("dn", "ak", "rs", "mp", "sf", "lw"),
    columns=(
        ColumnCreate(name="Backlog", description="Everything agreed but not scheduled"),
        ColumnCreate(name="In progress", description="Actively being worked this sprint"),
        ColumnCreate(name="Review", description="Waiting on PR review or QA"),
        ColumnCreate(name="Done", description="Merged and deployed to staging"),
    ),
    tasks=(
        TaskSpec(
            number=41,
            title="Design ledger event schema",
            description=(
                "Define the append-only ledger events (invoice.created, payment.applied, "
                "credit.issued) and their JSON shape."
            ),
            type=TaskType.FEATURE,
            due_date=date(2026, 9, 8),
            assignee="dn",
            column=0,
            jira_ref="ATL-41",
            comments=(("ak", "Can we keep amounts as integer minor units?"),),
        ),
        TaskSpec(
            number=38,
            # The mock spells the range with an en dash, and it is a range.
            title="Back-fill 2019–2021 invoices",  # noqa: RUF001
            description="One-off script to import archived invoices from the S3 dump.",
            type=TaskType.CHORE,
            due_date=date(2026, 9, 20),
            assignee="rs",
            column=0,
            jira_ref="ATL-38",
        ),
        TaskSpec(
            number=35,
            title="Stripe webhook idempotency",
            description=(
                "Duplicate webhook deliveries create double payments. Store event ids and dedupe."
            ),
            type=TaskType.BUG,
            due_date=date(2026, 8, 29),
            assignee="ak",
            column=1,
            jira_ref="ATL-35",
            pr_ref="#212",
            comments=(("dn", "Repro is in the ticket, happens on retries after 5xx."),),
        ),
        TaskSpec(
            number=33,
            title="Tax rate lookup by region",
            description="Integrate the Avalara sandbox and cache rates for 24h.",
            type=TaskType.FEATURE,
            due_date=date(2026, 9, 3),
            assignee="mp",
            column=1,
            jira_ref="ATL-33",
            status=TaskStatus.HOLD,
            reason="Waiting for Avalara sandbox credentials from finance.",
            waiting_on=("sf",),
        ),
        TaskSpec(
            number=30,
            title="Invoice PDF rendering",
            description=(
                "Generate PDFs with WeasyPrint; match the current template pixel for pixel."
            ),
            type=TaskType.FEATURE,
            due_date=date(2026, 9, 1),
            assignee="rs",
            column=1,
            jira_ref="ATL-30",
            pr_ref="#207",
            status=TaskStatus.BLOCKED,
            reason="Font licence for the invoice template hasn't been approved by legal.",
            waiting_on=("lw", "sf"),
            comments=(("rs", "Fallback to Noto if this drags past Tuesday?"),),
        ),
        TaskSpec(
            number=27,
            title="Customer balance endpoint",
            description="GET /customers/{id}/balance with pagination on open invoices.",
            type=TaskType.FEATURE,
            due_date=date(2026, 8, 31),
            assignee="dn",
            column=2,
            jira_ref="ATL-27",
            pr_ref="#204",
        ),
        TaskSpec(
            number=22,
            title="Set up psql schema + alembic",
            description="Initial migrations, CI job to check for drift.",
            type=TaskType.CHORE,
            due_date=date(2026, 8, 20),
            assignee="dn",
            column=3,
            jira_ref="ATL-22",
            pr_ref="#198",
        ),
        TaskSpec(
            number=19,
            title="Auth middleware for billing routes",
            description="JWT verification and per-tenant scoping.",
            type=TaskType.FEATURE,
            due_date=date(2026, 8, 18),
            assignee="ak",
            column=3,
            jira_ref="ATL-19",
            pr_ref="#191",
        ),
    ),
    root_items=(
        FileSpec(name="README.md", added_by="dn", source=ItemSource.UPLOAD, mime="text/markdown"),
        FileSpec(
            name="Project brief",
            added_by="dn",
            source=ItemSource.GDRIVE,
            url="https://docs.google.com/document/d/atlas-billing-migration-brief",
        ),
    ),
    folders=(
        FolderSpec(
            name="Architecture",
            items=(
                FileSpec(
                    name="billing-core-hld.pdf",
                    added_by="dn",
                    source=ItemSource.UPLOAD,
                    mime="application/pdf",
                ),
                FileSpec(
                    name="Ledger event flow",
                    added_by="ak",
                    source=ItemSource.GDRIVE,
                    url="https://drive.google.com/file/d/atlas-ledger-event-flow",
                ),
                FileSpec(
                    name="sequence-cutover.png",
                    added_by="dn",
                    source=ItemSource.UPLOAD,
                    mime="image/png",
                ),
            ),
        ),
        FolderSpec(
            name="Finance inputs",
            items=(
                FileSpec(
                    name="Avalara onboarding",
                    added_by="mp",
                    source=ItemSource.SHAREPOINT,
                    url="https://think41.sharepoint.com/sites/atlas/finance/avalara-onboarding",
                ),
                FileSpec(
                    name="invoice-template-v3.docx",
                    added_by="rs",
                    source=ItemSource.UPLOAD,
                    mime=(
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                ),
            ),
            children=(
                FolderSpec(
                    name="2025",
                    items=(
                        FileSpec(
                            name="tax-regions-2025.xlsx",
                            added_by="mp",
                            source=ItemSource.UPLOAD,
                            mime=(
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        FolderSpec(
            name="Exports",
            items=(
                FileSpec(
                    name="legacy-invoices-2019-2021.zip",
                    added_by="rs",
                    source=ItemSource.UPLOAD,
                    mime="application/zip",
                ),
            ),
        ),
    ),
    trees=(
        TreeSpec(
            name="Logins",
            nodes=(
                VaultNodeSpec(
                    name="Stripe",
                    children=(
                        VaultNodeSpec(
                            name="Dashboard (test)",
                            secret=SecretSpec(
                                value="Qv7!mR2p#xL9",
                                username="billing@think41.com",
                                url="https://dashboard.stripe.com/test",
                                notes="2FA on the shared Authy.",
                            ),
                        ),
                        VaultNodeSpec(
                            name="Restricted API key",
                            secret=SecretSpec(
                                value="rk_test_51MxT9aB3cD4eF5gH6",
                                notes="Read-only on charges + customers. Rotates quarterly.",
                            ),
                        ),
                    ),
                ),
                VaultNodeSpec(
                    name="Avalara sandbox",
                    secret=SecretSpec(
                        value="pending",
                        username="atlas-sandbox",
                        url="https://sandbox-admin.avalara.com",
                        notes="Creds not yet issued — see ATL-33.",
                    ),
                ),
                VaultNodeSpec(
                    name="Staging DB",
                    children=(
                        VaultNodeSpec(
                            name="psql (read/write)",
                            secret=SecretSpec(
                                value="hZ4k$Wn8@pQe2Ls!",
                                username="atlas_rw",
                                url="postgres://staging-db.internal:5432/atlas",
                                notes="Only via the bastion.",
                            ),
                        ),
                        VaultNodeSpec(
                            name="psql (read-only)",
                            secret=SecretSpec(
                                value="rO9x#Lm2vB7q",
                                username="atlas_ro",
                                url="postgres://staging-db.internal:5432/atlas",
                            ),
                        ),
                    ),
                ),
            ),
        ),
        TreeSpec(
            name="Links",
            nodes=(
                VaultNodeSpec(
                    name="Jira board",
                    secret=SecretSpec(
                        value=NO_CREDENTIAL,
                        url="https://think41.atlassian.net/jira/software/projects/ATL/boards/12",
                    ),
                ),
                VaultNodeSpec(
                    name="Grafana — billing dashboards",
                    secret=SecretSpec(
                        value=NO_CREDENTIAL,
                        url="https://grafana.internal/d/billing",
                        notes="SSO",
                    ),
                ),
                VaultNodeSpec(
                    name="Runbooks",
                    children=(
                        VaultNodeSpec(
                            name="Cutover checklist",
                            secret=SecretSpec(
                                value=NO_CREDENTIAL,
                                url=(
                                    "https://think41.sharepoint.com/sites/atlas"
                                    "/runbooks/cutover.docx"
                                ),
                            ),
                        ),
                        VaultNodeSpec(
                            name="Rollback procedure",
                            secret=SecretSpec(
                                value=NO_CREDENTIAL,
                                url=(
                                    "https://think41.sharepoint.com/sites/atlas"
                                    "/runbooks/rollback.docx"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        TreeSpec(
            name="Certificates & keys",
            nodes=(
                VaultNodeSpec(
                    name="Webhook signing secret",
                    secret=SecretSpec(
                        value="whsec_8Kd2LmN9pQ4rS7tU1vW3",
                        notes="Set in STRIPE_WEBHOOK_SECRET on the API pods.",
                    ),
                ),
                VaultNodeSpec(
                    name="SSH — bastion",
                    secret=SecretSpec(
                        value="(key file: id_ed25519_atlas)",
                        username="deploy",
                        url="bastion.internal",
                        notes="Fingerprint SHA256:9f3a…",
                    ),
                ),
            ),
        ),
    ),
)


HERMES = ProjectSpec(
    key="HRM",
    name="Hermes Notifications",
    description="Unified email / SMS / push service with templates and per-tenant rate limits.",
    colour="#3B6FC2",
    members=("dn", "ak", "rs", "pt"),
    columns=(
        ColumnCreate(name="To do", description="Prioritised, ready to pick up"),
        ColumnCreate(name="Doing", description="In flight"),
    ),
    tasks=(
        TaskSpec(
            number=12,
            title="Template versioning",
            description="Keep old template versions so sent messages render as they did.",
            type=TaskType.FEATURE,
            due_date=date(2026, 9, 10),
            assignee="ak",
            column=0,
            jira_ref="HRM-12",
        ),
        TaskSpec(
            number=9,
            title="Twilio retry storm",
            description="Exponential backoff missing on 429s.",
            type=TaskType.BUG,
            due_date=date(2026, 8, 28),
            assignee="rs",
            column=1,
            jira_ref="HRM-9",
            pr_ref="#44",
        ),
        TaskSpec(
            number=7,
            title="Per-tenant rate limits",
            description="Token bucket in Redis keyed by tenant id.",
            type=TaskType.FEATURE,
            due_date=date(2026, 9, 5),
            assignee="dn",
            column=1,
            jira_ref="HRM-7",
            status=TaskStatus.HOLD,
            reason="Paused until Atlas cutover; same engineer.",
            waiting_on=("pt",),
        ),
    ),
    root_items=(
        FileSpec(
            name="Hermes spec",
            added_by="dn",
            source=ItemSource.GDRIVE,
            url="https://docs.google.com/document/d/hermes-notifications-spec",
        ),
    ),
    folders=(
        FolderSpec(
            name="Templates",
            items=(
                FileSpec(
                    name="welcome-email.html",
                    added_by="ak",
                    source=ItemSource.UPLOAD,
                    mime="text/html",
                ),
            ),
        ),
    ),
    trees=(
        TreeSpec(
            name="Logins",
            nodes=(
                VaultNodeSpec(
                    name="Twilio console",
                    secret=SecretSpec(
                        value="Tw!9xLp3#Qm",
                        username="ops@think41.com",
                        url="https://console.twilio.com",
                    ),
                ),
                VaultNodeSpec(
                    name="SendGrid API key",
                    secret=SecretSpec(
                        value="SG.k2Lm9PqR.sT7uV1wX3yZ",
                        notes="Full access; rotate on offboarding.",
                    ),
                ),
            ),
        ),
    ),
)


ORBIT = ProjectSpec(
    key="ORB",
    name="Orbit Internal Portal",
    description=(
        "Employee-facing portal: leave, expenses, asset requests. Low priority until October."
    ),
    colour="#C77D00",
    members=("dn", "mp"),
    columns=(
        ColumnCreate(name="Ideas", description="Unrefined"),
        ColumnCreate(name="Next", description="Refined and estimated"),
    ),
    tasks=(
        TaskSpec(
            number=3,
            title="Leave calendar view",
            description="Month grid showing team leave.",
            type=TaskType.FEATURE,
            due_date=date(2026, 10, 15),
            assignee="mp",
            column=0,
            jira_ref="ORB-3",
        ),
        TaskSpec(
            number=2,
            title="SSO with Google Workspace",
            # The mock leaves this one blank; the API requires a description,
            # for the reason the field exists — a card nobody can act on.
            description="Sign in with the company Google account instead of a portal password.",
            type=TaskType.CHORE,
            due_date=date(2026, 10, 1),
            assignee="dn",
            column=0,
            jira_ref="ORB-2",
        ),
    ),
    trees=(
        TreeSpec(
            name="Links",
            nodes=(
                VaultNodeSpec(
                    name="Figma mockups",
                    secret=SecretSpec(
                        value=NO_CREDENTIAL,
                        url="https://figma.com/file/orbit-internal-portal",
                    ),
                ),
            ),
        ),
    ),
)


SAMPLE: tuple[ProjectSpec, ...] = (ATLAS, HERMES, ORBIT)


# --- Writing it ------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    """What a run wrote, for the line it prints at the end."""

    people: int = 0
    projects: int = 0
    columns: int = 0
    tasks: int = 0
    folders: int = 0
    items: int = 0
    trees: int = 0
    secrets: int = 0

    def render(self) -> str:
        return (
            f"{self.projects} projects · {self.people} people · {self.columns} columns · "
            f"{self.tasks} tasks · {self.folders} folders · {self.items} files and links · "
            f"{self.trees} vault trees · {self.secrets} secrets"
        )


@dataclass(slots=True)
class Inventory:
    """What is in the database already, for the warning before a wipe."""

    projects: list[tuple[str, str]] = field(default_factory=list)
    people: int = 0
    tasks: int = 0
    folders: int = 0
    items: int = 0
    secrets: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.projects and not self.people

    def render(self) -> str:
        lines = [f"  {key}  {name}" for key, name in self.projects]
        if self.projects:
            lines.append(
                f"  and with them: {self.tasks} tasks, {self.folders} folders, "
                f"{self.items} files and links, {self.secrets} vault secrets"
            )
        if self.people:
            lines.append(f"  all {self.people} people in the directory")
        return "\n".join(lines)


async def survey(session: AsyncSession) -> Inventory:
    """Report what a wipe would destroy, so the warning can be specific."""
    rows = await session.execute(select(Project.key, Project.name).order_by(Project.key))

    # Root folders are excluded: nobody made them, and counting them would
    # overstate what is about to go.
    counts = (
        await session.execute(
            select(
                select(func.count()).select_from(Person).scalar_subquery(),
                select(func.count()).select_from(Task).scalar_subquery(),
                select(func.count())
                .select_from(Folder)
                .where(Folder.parent_id.is_not(None))
                .scalar_subquery(),
                select(func.count()).select_from(FileItem).scalar_subquery(),
                select(func.count()).select_from(VaultSecret).scalar_subquery(),
            )
        )
    ).one()

    return Inventory(
        projects=[(key, name) for key, name in rows],
        people=counts[0],
        tasks=counts[1],
        folders=counts[2],
        items=counts[3],
        secrets=counts[4],
    )


async def wipe(session: AsyncSession) -> list[str]:
    """Delete every project and person, and return the orphaned blob paths.

    Not touched: ``api_token``, because deleting it would sign the operator out
    of the application they are seeding, and ``activity``, whose ``project_id``
    is ``SET NULL`` rather than ``CASCADE`` precisely so the record of what
    happened outlives the thing it happened to.

    The bytes on disk are returned rather than removed, so the caller can
    unlink them only once the transaction that dropped the rows has committed.
    """
    await session.execute(delete(Project))
    await session.execute(delete(Person))
    await session.flush()

    still_used = select(FileItem.blob_id).where(FileItem.blob_id.is_not(None))
    orphaned = list(await session.scalars(select(Blob).where(Blob.id.not_in(still_used))))
    paths = [blob.path for blob in orphaned]
    if orphaned:
        await session.execute(delete(Blob).where(Blob.id.in_([blob.id for blob in orphaned])))
        await session.flush()
    return paths


async def populate(
    session: AsyncSession,
    *,
    cipher: VaultCipher,
    store: BlobStore,
    max_upload_bytes: int,
) -> Summary:
    """Write the whole sample into an empty database.

    Takes the cipher and the store rather than the settings, so that a caller
    testing this can point it at a temporary directory without the environment
    having a say.
    """
    summary = Summary()
    directory = await _write_directory(session, summary)

    for spec in SAMPLE:
        project = await projects.create(
            session,
            ProjectCreate(
                key=spec.key, name=spec.name, description=spec.description, colour=spec.colour
            ),
        )
        await projects.set_members(session, project, [directory[h] for h in spec.members])

        board = await _shape_board(session, project, spec, summary)
        await _write_tasks(session, project, spec, board, directory, summary)
        await _write_files(
            session,
            project,
            spec,
            directory,
            summary,
            store=store,
            max_upload_bytes=max_upload_bytes,
        )
        await _write_vault(session, project, spec, cipher, summary)
        summary.projects += 1

    return summary


async def _write_directory(session: AsyncSession, summary: Summary) -> dict[str, UUID]:
    """Create every person once, and return their ids by mock handle."""
    directory: dict[str, UUID] = {}
    for spec in DIRECTORY:
        person = await people.create(
            session,
            PersonCreate(
                name=spec.name,
                kind=spec.kind,
                role=spec.role,
                responsibilities=spec.responsibilities,
                email=spec.email,
                colour=spec.colour,
            ),
        )
        directory[spec.handle] = person.id
        summary.people += 1
    return directory


async def _shape_board(
    session: AsyncSession, project: Project, spec: ProjectSpec, summary: Summary
) -> list[UUID]:
    """Turn the two starter columns into the board the mock shows.

    Renamed in place rather than deleted and recreated: a column cannot be
    deleted if it would take the board below two, so replacing them would mean
    fighting a rule that is right.
    """
    existing = await columns.list_for_project(session, project)
    for column, wanted in zip(existing, spec.columns, strict=False):
        await columns.update(
            session, column, ColumnUpdate(name=wanted.name, description=wanted.description)
        )
    for wanted in spec.columns[len(existing) :]:
        await columns.create(session, project, wanted)

    board = await columns.list_for_project(session, project)
    summary.columns += len(board)
    return [column.id for column in board]


async def _write_tasks(
    session: AsyncSession,
    project: Project,
    spec: ProjectSpec,
    board: list[UUID],
    directory: dict[str, UUID],
    summary: Summary,
) -> None:
    """Create the cards, then put them where the mock has them.

    Created in ascending number order because the counter only goes up, and
    moved afterwards because a new task always lands in the first column —
    which is the rule that makes the board a record of progress.
    """
    created: dict[int, Task] = {}
    for task_spec in sorted(spec.tasks, key=lambda entry: entry.number):
        created[task_spec.number] = await _write_task(
            session, project, task_spec, directory, summary
        )

    filled: dict[int, int] = {}
    for task_spec in spec.tasks:  # the mock's order is the board's order
        position = filled.get(task_spec.column, 0)
        await tasks.move(
            session,
            created[task_spec.number],
            TaskMove(column_id=board[task_spec.column], position=position),
        )
        filled[task_spec.column] = position + 1


async def _write_task(
    session: AsyncSession,
    project: Project,
    spec: TaskSpec,
    directory: dict[str, UUID],
    summary: Summary,
) -> Task:
    # Wind the counter so the card comes out with the mock's own reference.
    # Flushed first because `tasks.create` reads the counter back from the row.
    project.task_counter = spec.number - 1
    await session.flush()

    task = await tasks.create(
        session,
        project,
        TaskCreate(
            title=spec.title,
            description=spec.description,
            type=spec.type,
            due_date=spec.due_date,
            assignee_id=directory[spec.assignee],
            jira_ref=spec.jira_ref,
            pr_ref=spec.pr_ref,
        ),
    )
    if task.number != spec.number:
        raise RuntimeError(
            f"Expected {project.key}-{spec.number} but the counter produced {task.number}."
        )

    if spec.status is not TaskStatus.ACTIVE:
        await tasks.change_status(
            session,
            task,
            TaskStatusChange(
                status=spec.status,
                reason=spec.reason,
                waiting_on=[directory[handle] for handle in spec.waiting_on],
            ),
        )

    for author, body in spec.comments:
        await tasks.comment(session, task, CommentCreate(body=body, author_id=directory[author]))

    summary.tasks += 1
    return task


async def _write_files(
    session: AsyncSession,
    project: Project,
    spec: ProjectSpec,
    directory: dict[str, UUID],
    summary: Summary,
    *,
    store: BlobStore,
    max_upload_bytes: int,
) -> None:
    root = await files.root_of(session, project.id)
    for item in spec.root_items:
        await _write_item(
            session, root, item, directory, summary, store=store, max_upload_bytes=max_upload_bytes
        )

    async def branch(parent: Folder | None, folder_specs: Sequence[FolderSpec]) -> None:
        for folder_spec in folder_specs:
            folder = await files.create_folder(
                session,
                project,
                FolderCreate(
                    name=folder_spec.name, parent_id=parent.id if parent is not None else None
                ),
            )
            summary.folders += 1
            for item in folder_spec.items:
                await _write_item(
                    session,
                    folder,
                    item,
                    directory,
                    summary,
                    store=store,
                    max_upload_bytes=max_upload_bytes,
                )
            await branch(folder, folder_spec.children)

    await branch(None, spec.folders)


async def _write_item(
    session: AsyncSession,
    folder: Folder,
    spec: FileSpec,
    directory: dict[str, UUID],
    summary: Summary,
    *,
    store: BlobStore,
    max_upload_bytes: int,
) -> None:
    added_by = directory[spec.added_by]
    if spec.url is not None:
        await files.add_link(
            session,
            folder,
            LinkCreate(name=spec.name, url=spec.url, source=spec.source, added_by=added_by),
        )
    else:
        content = spec.placeholder
        upload = UploadFile(
            file=io.BytesIO(content),
            size=len(content),
            filename=spec.name,
            headers=Headers({"content-type": spec.mime}),
        )
        await files.upload(
            session, store, folder, upload, max_bytes=max_upload_bytes, added_by=added_by
        )
    summary.items += 1


async def _write_vault(
    session: AsyncSession,
    project: Project,
    spec: ProjectSpec,
    cipher: VaultCipher,
    summary: Summary,
) -> None:
    async def branch(
        tree: VaultTree, parent_id: UUID | None, nodes: Sequence[VaultNodeSpec]
    ) -> None:
        for node_spec in nodes:
            secret = node_spec.secret
            node = await vault.create_node(
                session,
                cipher,
                VaultNodeCreate(
                    tree_id=tree.id,
                    parent_id=parent_id,
                    name=node_spec.name,
                    kind=VaultNodeKind.SECRET if secret is not None else VaultNodeKind.BRANCH,
                    secret=(
                        SecretCreate(
                            value=secret.value,
                            username=secret.username,
                            url=secret.url,
                            notes=secret.notes,
                        )
                        if secret is not None
                        else None
                    ),
                ),
            )
            if secret is not None:
                summary.secrets += 1
            await branch(tree, node.id, node_spec.children)

    for tree_spec in spec.trees:
        tree = await vault.create_tree(session, project, VaultTreeCreate(name=tree_spec.name))
        summary.trees += 1
        await branch(tree, None, tree_spec.nodes)


# --- Running it ------------------------------------------------------------


def _redacted(url: str) -> str:
    """The database being written to, without its password."""
    parsed = make_url(url)
    return parsed.render_as_string(hide_password=True)


def _confirm(database_name: str) -> bool:
    """Make the operator type the database's name before it is emptied.

    A ``y/N`` prompt is answered by reflex. Typing the name is answered by
    reading the question, which is the point of asking at all.
    """
    print(f"\nType the database name ({database_name}) to confirm, or anything else to stop.")
    try:
        return input("> ").strip() == database_name
    except EOFError:
        return False


async def run(settings: Settings, *, force: bool, assume_yes: bool) -> int:
    """Seed the database named by ``settings``, or explain why not."""
    url = settings.database_url
    name = make_url(url).database or "?"
    print(f"Seeding {_redacted(url)}")

    if settings.is_production:
        print(
            "\nThis is a production environment (CYLIST_ENVIRONMENT=prod). The seed "
            "writes fictional projects and credentials, so it refuses to run here.",
            file=sys.stderr,
        )
        return 1

    if not settings.vault_key:
        print(
            "\nCYLIST_VAULT_KEY is not set, and the sample data includes vault secrets, "
            "which are encrypted before they are stored.\n"
            "Generate one with:  uv run python -m app.cli generate-vault-key",
            file=sys.stderr,
        )
        return 1
    cipher = cipher_for(settings.vault_key)
    store = LocalBlobStore(settings.blob_dir)

    removable: list[str] = []
    database = Database(url)
    try:
        async with database.session() as session:
            inventory = await survey(session)

            if not inventory.is_empty and not force:
                print(
                    f"\nThis database is not empty. Refusing to seed over it.\n"
                    f"{inventory.render()}\n\n"
                    f"Re-run with --force to delete all of that and start again.",
                    file=sys.stderr,
                )
                return 1

            if not inventory.is_empty:
                print(f"\n--force will permanently delete:\n{inventory.render()}")
                print("\nIt will leave your API tokens and the activity log alone.")

                if not assume_yes:
                    if not sys.stdin.isatty():
                        print(
                            "\nRefusing to delete without confirmation. Pass --yes if you "
                            "mean it and there is nobody here to ask.",
                            file=sys.stderr,
                        )
                        return 1
                    if not _confirm(name):
                        print("\nStopped. Nothing was deleted.", file=sys.stderr)
                        return 1

                removable = await wipe(session)

            summary = await populate(
                session,
                cipher=cipher,
                store=store,
                max_upload_bytes=settings.max_upload_bytes,
            )
    finally:
        await database.dispose()

    # Bytes after rows, and only once the rows are committed: an unreferenced
    # file on disk is harmless, a row pointing at content that is gone is not.
    for path in removable:
        await store.remove(path)

    print(f"\nSeeded {summary.render()}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.seed",
        description=(
            "Fill a database with the three sample projects from the design mock — "
            "Atlas, Hermes and Orbit — with their people, boards, files and vaults."
        ),
        epilog=(
            "Refuses to touch a database that already holds projects unless --force is "
            "given, and --force says what it is about to delete first."
        ),
    )
    parser.add_argument(
        "--url",
        help="Database to seed. Defaults to CYLIST_DATABASE_URL, as the application reads it.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete every existing project and person first, then seed.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt. Required for --force without a terminal.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.url:
        # Re-validated rather than copied over: `database_url` has a rule about
        # the driver, and an override that skipped it would fail later and less
        # clearly.
        try:
            settings = Settings.model_validate({**settings.model_dump(), "database_url": args.url})
        except ValidationError as exc:
            parser.error(f"--url is not usable: {exc.errors()[0]['msg']}")

    return asyncio.run(run(settings, force=args.force, assume_yes=args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
