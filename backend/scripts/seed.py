"""Fill a database with the Atlas / Hermes / Orbit sample data from the mock.

Usage::

    uv run python -m scripts.seed
    uv run python -m scripts.seed --force --yes

This exists so that a fresh checkout has something to look at: three projects
with real boards, real stalled work, real files and real credentials, rather
than a home screen reading "no projects yet".

The sample itself — every person, card, file and secret — is data in
:mod:`scripts.seed_sample`; this module is what writes it.

Everything goes through :mod:`app.services`, never through the ORM directly.
That is not tidiness — it is the only way the seeded data obeys the same rules
the API does. A blocked task therefore carries a reason and a set of tagged
people because :func:`app.services.tasks.change_status` insists on them, not
because this file remembered to write them.

Four places where the mock and the database do not line up, and what is done
about each:

*People.* The mock keeps a copy of each person per project, with a different
job title on each. The database has one global directory, so each is written
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
from datetime import timedelta
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
from app.models.file import Blob, FileItem, Folder
from app.models.goal import GoalStatus
from app.models.person import Person
from app.models.project import Project
from app.models.task import ChecklistState, Task, TaskStatus
from app.models.vault import VaultNodeKind, VaultSecret, VaultTree
from app.schemas.columns import ColumnUpdate
from app.schemas.files import FolderCreate, LinkCreate
from app.schemas.goals import GoalCreate, GoalUpdate
from app.schemas.people import PersonCreate
from app.schemas.projects import ProjectCreate
from app.schemas.tasks import (
    ChecklistItemCreate,
    ChecklistItemUpdate,
    CommentCreate,
    PrRef,
    TaskCreate,
    TaskMove,
    TaskStatusChange,
)
from app.schemas.vault import SecretCreate, VaultNodeCreate, VaultTreeCreate
from app.services import blobs, columns, files, goals, people, projects, tasks, vault
from app.storage import BlobStore, LocalBlobStore
from scripts.seed_sample import (
    DIRECTORY,
    SAMPLE,
    FileSpec,
    FolderSpec,
    ProjectSpec,
    TaskSpec,
    VaultNodeSpec,
)

# --- Writing it ------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    """What a run wrote, for the line it prints at the end."""

    people: int = 0
    projects: int = 0
    columns: int = 0
    goals: int = 0
    tasks: int = 0
    folders: int = 0
    items: int = 0
    trees: int = 0
    secrets: int = 0

    def render(self) -> str:
        return (
            f"{self.projects} projects · {self.people} people · {self.columns} columns · "
            f"{self.goals} goals · {self.tasks} tasks · {self.folders} folders · "
            f"{self.items} files and links · "
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


async def wipe(session: AsyncSession) -> None:
    """Delete every project and person, and the blob rows that leaves orphaned.

    Not touched: ``api_token``, because deleting it would sign the operator out
    of the application they are seeding, and ``activity``, whose ``project_id``
    is ``SET NULL`` rather than ``CASCADE`` precisely so the record of what
    happened outlives the thing it happened to.

    The bytes on disk are not touched: the caller sweeps once this has
    committed, and the sweep keeps whatever the reseed has pointed a row at
    again — which, the sample being the same, is most of it.
    """
    await session.execute(delete(Project))
    await session.execute(delete(Person))
    await session.flush()

    still_used = select(FileItem.blob_id).where(FileItem.blob_id.is_not(None))
    await session.execute(delete(Blob).where(Blob.id.not_in(still_used)))
    await session.flush()


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
    owner_id = _owner_of(directory)

    for spec in SAMPLE:
        project = await projects.create(
            session,
            ProjectCreate(
                key=spec.key, name=spec.name, description=spec.description, colour=spec.colour
            ),
            creator_id=owner_id,
        )
        # The seed runs as the deployment's owner, who may hand out any role.
        await projects.set_members(
            session, project, [directory[h] for h in spec.members], fill_vacancy=True
        )

        board = await _shape_board(session, project, spec, summary)
        # Before the cards, because a card names the goal it is written under.
        goal_ids = await _write_goals(session, project, spec, directory, summary)
        await _write_tasks(session, project, spec, board, directory, goal_ids, summary)
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
                title=spec.title,
                responsibilities=spec.responsibilities,
                email=spec.email,
                colour=spec.colour,
            ),
        )
        directory[spec.handle] = person.id
        summary.people += 1
    return directory


def _owner_of(directory: dict[str, UUID]) -> UUID | None:
    """Whoever the sample data is created by, by handle."""
    return next(
        (directory[spec.handle] for spec in DIRECTORY if spec.is_owner),
        None,
    )


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


async def _write_goals(
    session: AsyncSession,
    project: Project,
    spec: ProjectSpec,
    directory: dict[str, UUID],
    summary: Summary,
) -> dict[str, UUID]:
    """Write the project's goals, and hand back their ids by name.

    A goal's status is set in a second pass rather than at creation, because
    creating one achieved is not a thing the API allows — a goal is reached,
    which is a thing that happens to it after its cards are done.
    """
    written: dict[str, UUID] = {}
    for goal_spec in spec.goals:
        goal = await goals.create(
            session,
            project,
            GoalCreate(
                name=goal_spec.name,
                description=goal_spec.description,
                colour=goal_spec.colour,
                target_date=goal_spec.target_date,
                owner_id=directory[goal_spec.owner],
            ),
        )
        if goal_spec.status is not GoalStatus.OPEN:
            await goals.update(session, goal, GoalUpdate(status=goal_spec.status))
        written[goal_spec.name] = goal.id
        summary.goals += 1
    return written


async def _write_tasks(
    session: AsyncSession,
    project: Project,
    spec: ProjectSpec,
    board: list[UUID],
    directory: dict[str, UUID],
    goal_ids: dict[str, UUID],
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
            session, project, task_spec, directory, goal_ids, summary
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

    # After the parents are placed, so a sub-task never lands on top of the
    # card it belongs to.
    for task_spec in spec.tasks:
        await _write_subtasks(
            session, project, created[task_spec.number], task_spec, directory, summary
        )


async def _write_task(
    session: AsyncSession,
    project: Project,
    spec: TaskSpec,
    directory: dict[str, UUID],
    goal_ids: dict[str, UUID],
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
            goal_id=goal_ids.get(spec.goal) if spec.goal else None,
            jira_ref=spec.jira_ref,
            pr_refs=[PrRef(url=url, state=state) for url, state in spec.pr_refs],
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

    for title, state in spec.checklist:
        item = await tasks.add_checklist_item(session, task, ChecklistItemCreate(title=title))
        if state is not ChecklistState.OPEN:
            await tasks.update_checklist_item(session, item, ChecklistItemUpdate(state=state))

    summary.tasks += 1
    return task


async def _write_subtasks(
    session: AsyncSession,
    project: Project,
    parent: Task,
    spec: TaskSpec,
    directory: dict[str, UUID],
    summary: Summary,
) -> None:
    """Split a card into sub-tasks, numbered ATL-35-1, ATL-35-2, …

    They go nowhere on the board — a sub-task has no column — so the only
    placing to do is whether each one is already ticked off.
    """
    for sub_spec in spec.subtasks:
        subtask = await tasks.create(
            session,
            project,
            TaskCreate(
                title=sub_spec.title,
                description=sub_spec.description,
                type=sub_spec.type,
                due_date=sub_spec.due_date,
                assignee_id=directory[sub_spec.assignee],
            ),
            parent=parent,
        )
        if sub_spec.status is not TaskStatus.ACTIVE:
            await tasks.change_status(
                session,
                subtask,
                TaskStatusChange(status=sub_spec.status, reason=sub_spec.reason),
            )
        if sub_spec.finished:
            await tasks.set_finished(session, subtask, finished=True)
        summary.tasks += 1


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


def _cleared_to_wipe(
    inventory: Inventory, database_name: str, *, force: bool, assume_yes: bool
) -> bool:
    """Whether the operator has said, in every way required, to empty a database.

    ``--force`` says it may be emptied at all; then either ``--yes`` or the
    database's name typed at a terminal says this run is the one meant to.
    Each refusal says which of the two is missing, on stderr.
    """
    if not force:
        print(
            f"\nThis database is not empty. Refusing to seed over it.\n"
            f"{inventory.render()}\n\n"
            f"Re-run with --force to delete all of that and start again.",
            file=sys.stderr,
        )
        return False

    print(f"\n--force will permanently delete:\n{inventory.render()}")
    print("\nIt will leave your API tokens and the activity log alone.")
    if assume_yes:
        return True

    if not sys.stdin.isatty():
        print(
            "\nRefusing to delete without confirmation. Pass --yes if you "
            "mean it and there is nobody here to ask.",
            file=sys.stderr,
        )
        return False
    if not _confirm(database_name):
        print("\nStopped. Nothing was deleted.", file=sys.stderr)
        return False
    return True


async def run(settings: Settings, *, force: bool, assume_yes: bool) -> int:
    """Seed the database named by ``settings``, or explain why not."""
    url = settings.database_url
    name = make_url(url).database or "?"
    print(f"Seeding {_redacted(url)}")

    if settings.is_deployed:
        print(
            f"\nThis is a deployed environment (CYLIST_ENVIRONMENT={settings.environment}). "
            "The seed writes fictional projects and credentials, so it refuses to "
            "run here.",
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

    database = Database(url)
    try:
        async with database.session() as session:
            inventory = await survey(session)

            if not inventory.is_empty:
                if not _cleared_to_wipe(inventory, name, force=force, assume_yes=assume_yes):
                    return 1
                await wipe(session)

            summary = await populate(
                session,
                cipher=cipher,
                store=store,
                max_upload_bytes=settings.max_upload_bytes,
            )
        # Bytes after rows, and only once the rows are committed: an
        # unreferenced file on disk is harmless, a row pointing at content
        # that is gone is not. No idle margin, since nothing else is uploading
        # into a deployment being seeded.
        await blobs.sweep(database, store, idle_for=timedelta(0))
    finally:
        await database.dispose()

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
