"""The Atlas / Hermes / Orbit sample, as data.

Kept apart from :mod:`scripts.seed`, which writes it, because the two change
for different reasons: this file changes when the mock does, and that one when
the services it writes through do. Nothing here touches a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.file import ItemSource
from app.models.goal import GoalStatus
from app.models.person import PersonKind
from app.models.task import ChecklistState, PrState, TaskStatus, TaskType
from app.schemas.columns import ColumnCreate


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
    title: str
    responsibilities: str
    email: str
    colour: str
    is_owner: bool = False
    """Whether this is the person the sample projects are created by.

    Exactly one spec sets it. It no longer marks them as "you" — with
    accounts, who you are is whoever signed in — but somebody has to be the
    creator the seeded projects name, and the tech lead is the obvious one.

    No password is seeded. A freshly seeded deployment has no accounts, which
    is the state the bootstrap login exists for: sign in with
    ``CYLIST_PASSWORD_HASH``, then invite this person like any other. That way
    the sample data exercises the real path in rather than shipping a
    credential nobody chose.
    """


@dataclass(frozen=True, slots=True)
class SubtaskSpec:
    """A sub-task with a reference of its own, numbered under its parent.

    No column: a sub-task is not on the board. ``finished`` is the whole of
    where it has got to.
    """

    title: str
    description: str
    type: TaskType
    due_date: date
    assignee: str
    finished: bool = False
    status: TaskStatus = TaskStatus.ACTIVE
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GoalSpec:
    """One goal, and the colour its cards wear on the board."""

    name: str
    description: str
    owner: str
    colour: str
    target_date: date | None = None
    status: GoalStatus = GoalStatus.OPEN


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
    pr_refs: tuple[tuple[str, PrState], ...] = ()
    """The pull requests this card landed as, each with how far it got. A tuple
    rather than a list so the spec stays hashable and no two cards can share
    one by accident.

    The states are picked to match where each card sits: a card in Done was
    carried by pull requests that merged, one in Review by one somebody has
    approved. A board seeded entirely at `raised` would show one colour and
    teach a reader nothing about the other two."""

    goal: str | None = None
    """The goal this card is written under, by name. None stands on its own —
    which most cards do, and the sample keeps it that way so the board shows
    both kinds of rail."""

    status: TaskStatus = TaskStatus.ACTIVE
    reason: str = ""
    waiting_on: tuple[str, ...] = ()
    comments: tuple[tuple[str, str], ...] = ()
    """``(author handle, body)``, added after any status change, which is the
    order the mock shows them in."""

    subtasks: tuple[SubtaskSpec, ...] = ()
    """Sub-tasks with their own references, in the order they are numbered."""

    checklist: tuple[tuple[str, ChecklistState], ...] = ()
    """``(title, state)`` tick boxes, top to bottom."""


@dataclass(frozen=True, slots=True)
class FileSpec:
    """One row of a folder listing: an upload, or a link out."""

    name: str
    added_by: str
    source: ItemSource
    url: str | None = None
    mime: str = "application/octet-stream"

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
    goals: tuple[GoalSpec, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
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
        is_owner=True,
        kind=PersonKind.TEAM,
        title="Tech lead",
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
        title="Backend engineer",
        responsibilities="Payments, Stripe integration and webhook reliability.",
        email="aditi@think41.com",
        colour="#3B6FC2",
    ),
    PersonSpec(
        handle="rs",
        name="Rohan S",
        kind=PersonKind.TEAM,
        title="Backend engineer",
        responsibilities="Data migration scripts, PDF rendering and reporting.",
        email="rohan@think41.com",
        colour="#C77D00",
    ),
    PersonSpec(
        handle="mp",
        name="Meera P",
        kind=PersonKind.TEAM,
        title="QA & release",
        responsibilities="Test plans, staging sign-off and release notes.",
        email="meera@think41.com",
        colour="#7A6B9E",
    ),
    PersonSpec(
        handle="sf",
        name="Sanjay F",
        kind=PersonKind.CLIENT,
        title="Finance controller, Atlas",
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
        title="Legal counsel, Atlas",
        responsibilities="Signs off licences and contracts. Slow to respond — chase via Sanjay.",
        email="l.wright@atlas.example",
        colour="#5C6B73",
    ),
    PersonSpec(
        handle="pt",
        name="Priya T",
        kind=PersonKind.CLIENT,
        title="Product owner, Hermes",
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
    # Three goals rather than one per card: the sample has to show a board
    # where some cards are written under an epic and some stand on their own,
    # because that is the board most projects actually have.
    goals=(
        GoalSpec(
            name="Ledger cutover",
            description=(
                "Every invoice raised through the new ledger, with the legacy Java service "
                "reading from it rather than writing to it."
            ),
            owner="dn",
            colour="#2F6FEB",
            target_date=date(2026, 11, 28),
        ),
        GoalSpec(
            name="Payments hardening",
            description="No double charges, no lost webhooks, and a number on the board for both.",
            owner="ak",
            colour="#E5484D",
            target_date=date(2026, 9, 30),
        ),
        GoalSpec(
            name="Billing service foundations",
            description="The schema, the migrations and the auth the rest of it is built on.",
            owner="dn",
            colour="#0D9488",
            status=GoalStatus.ACHIEVED,
        ),
    ),
    tasks=(
        TaskSpec(
            goal="Ledger cutover",
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
            checklist=(
                ("Agree the amount representation with finance", ChecklistState.OPEN),
                ("Write the schema down in the runbook", ChecklistState.OPEN),
            ),
        ),
        TaskSpec(
            goal="Ledger cutover",
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
            goal="Payments hardening",
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
            pr_refs=(("#212", PrState.APPROVED), ("#219", PrState.RAISED)),
            comments=(("dn", "Repro is in the ticket, happens on retries after 5xx."),),
            # Two kinds of sub-task on one card, which is the point of them:
            # the dedupe store needs an owner and a date, telling support does
            # not. Neither lets ATL-35 reach Done until it is settled.
            subtasks=(
                SubtaskSpec(
                    title="Event id store with a 30-day window",
                    description="Persist delivered event ids and reject a repeat inside 30 days.",
                    type=TaskType.FEATURE,
                    due_date=date(2026, 8, 26),
                    assignee="ak",
                ),
                SubtaskSpec(
                    title="Replay the four double-charged invoices",
                    description="Credit the duplicates found in the August reconciliation.",
                    type=TaskType.CHORE,
                    due_date=date(2026, 8, 28),
                    assignee="rs",
                    finished=True,
                ),
            ),
            checklist=(
                ("Tell support before the fix ships", ChecklistState.DONE),
                ("Add the dedupe counter to the Grafana board", ChecklistState.OPEN),
                ("Backport to the 2.3 branch", ChecklistState.CANCELLED),
            ),
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
            goal="Ledger cutover",
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
            pr_refs=(("#207", PrState.RAISED),),
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
            pr_refs=(("#204", PrState.APPROVED),),
        ),
        TaskSpec(
            goal="Billing service foundations",
            number=22,
            title="Set up psql schema + alembic",
            description="Initial migrations, CI job to check for drift.",
            type=TaskType.CHORE,
            due_date=date(2026, 8, 20),
            assignee="dn",
            column=3,
            jira_ref="ATL-22",
            pr_refs=(("#198", PrState.MERGED),),
        ),
        TaskSpec(
            goal="Billing service foundations",
            number=19,
            title="Auth middleware for billing routes",
            description="JWT verification and per-tenant scoping.",
            type=TaskType.FEATURE,
            due_date=date(2026, 8, 18),
            assignee="ak",
            column=3,
            jira_ref="ATL-19",
            pr_refs=(("#191", PrState.MERGED),),
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
            pr_refs=(("#44", PrState.RAISED),),
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
