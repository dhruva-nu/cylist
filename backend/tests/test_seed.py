"""The sample data script: what it writes, and what it refuses to do.

The refusals matter more than the content. A seed is a tool that empties a
database, so the tests that count are the ones proving it will not do that by
accident.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.crypto import VaultCipher, cipher_for
from app.models.activity import Activity
from app.models.api_token import ApiToken, TokenKind
from app.models.board import BoardColumn
from app.models.file import Blob, FileItem, Folder, ItemKind
from app.models.goal import Goal, GoalStatus
from app.models.person import Person
from app.models.project import Project
from app.models.task import (
    ChecklistState,
    CommentKind,
    Task,
    TaskChecklistItem,
    TaskComment,
    TaskStatus,
)
from app.models.vault import VaultNode, VaultNodeKind
from app.services import vault
from app.storage import BlobStore, LocalBlobStore
from scripts import seed
from tests.conftest import VAULT_KEY


@pytest.fixture
def cipher() -> VaultCipher:
    return cipher_for(VAULT_KEY)


@pytest.fixture
def store(settings: Settings) -> BlobStore:
    return LocalBlobStore(settings.blob_dir)


@pytest.fixture
async def seeded(
    session: AsyncSession, cipher: VaultCipher, store: BlobStore, settings: Settings
) -> AsyncIterator[seed.Summary]:
    """A database holding the whole sample.

    Committed, not merely flushed: the tests below reach the script through
    :func:`seed.run`, which opens its own connection the way the command line
    does, and an uncommitted transaction would be both invisible to it and in
    its way.
    """
    summary = await seed.populate(
        session, cipher=cipher, store=store, max_upload_bytes=settings.max_upload_bytes
    )
    await session.commit()
    yield summary


async def project_by(session: AsyncSession, key: str) -> Project:
    project = await session.scalar(select(Project).where(Project.key == key))
    assert project is not None, f"no project {key}"
    return project


async def task_by(session: AsyncSession, key: str, number: int) -> Task:
    project = await project_by(session, key)
    task = await session.scalar(
        select(Task).where(Task.project_id == project.id, Task.number == number)
    )
    assert task is not None, f"no task {key}-{number}"
    return task


async def node_by(session: AsyncSession, name: str) -> VaultNode:
    node = await session.scalar(select(VaultNode).where(VaultNode.name == name))
    assert node is not None, f"no vault node {name!r}"
    return node


async def count_of(session: AsyncSession, model: Any) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


class TestWhatItWrites:
    async def test_creates_the_three_projects_from_the_mock(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        keys = await session.scalars(select(Project.key).order_by(Project.key))

        assert list(keys) == ["ATL", "HRM", "ORB"]
        assert seeded.projects == 3

    async def test_writes_each_person_once(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """The mock repeats people per project; the directory is global."""
        names = list(await session.scalars(select(Person.name)))

        assert sorted(names) == [
            "Aditi K",
            "Dhruva N",
            "Lena W",
            "Meera P",
            "Priya T",
            "Rohan S",
            "Sanjay F",
        ]

    async def test_puts_people_only_on_the_projects_they_are_on(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        atlas = await project_by(session, "ATL")
        orbit = await project_by(session, "ORB")
        await session.refresh(atlas, ["members"])
        await session.refresh(orbit, ["members"])

        assert len(atlas.members) == 6
        assert sorted(person.name for person in orbit.members) == ["Dhruva N", "Meera P"]

    async def test_reproduces_the_mocks_task_numbers(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """ATL-41 is the reference in the screenshots; a renumbered board is a wrong one."""
        atlas = await project_by(session, "ATL")
        # Top-level cards only: a sub-task holds no project number, which is
        # exactly why splitting ATL-35 did not push the next card off 42.
        numbers = await session.scalars(
            select(Task.number)
            .where(Task.project_id == atlas.id, Task.parent_id.is_(None))
            .order_by(Task.number.desc())
        )

        assert list(numbers) == [41, 38, 35, 33, 30, 27, 22, 19]

    async def test_leaves_the_counter_where_the_numbers_end(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """So the next task made by hand is ATL-42, not a repeat of an old one."""
        atlas = await project_by(session, "ATL")

        assert atlas.task_counter == 41

    async def test_builds_the_board_the_mock_shows(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        atlas = await project_by(session, "ATL")
        names = await session.scalars(
            select(BoardColumn.name)
            .where(BoardColumn.project_id == atlas.id)
            .order_by(BoardColumn.position)
        )

        assert list(names) == ["Backlog", "In progress", "Review", "Done"]

    async def test_puts_each_card_in_its_column(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        rows = await session.execute(
            select(Task.number, BoardColumn.name)
            .join(BoardColumn, BoardColumn.id == Task.column_id)
            .join(Project, Project.id == Task.project_id)
            .where(Project.key == "ATL", Task.parent_id.is_(None))
            .order_by(BoardColumn.position, Task.position)
        )

        assert list(rows) == [
            (41, "Backlog"),
            (38, "Backlog"),
            (35, "In progress"),
            (33, "In progress"),
            (30, "In progress"),
            (27, "Review"),
            (22, "Done"),
            (19, "Done"),
        ]

    async def test_writes_the_goals_the_board_is_coloured_by(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        atlas = await project_by(session, "ATL")
        rows = await session.execute(
            select(Goal.number, Goal.name, Goal.status)
            .where(Goal.project_id == atlas.id)
            .order_by(Goal.number)
        )

        assert list(rows) == [
            (1, "Ledger cutover", GoalStatus.OPEN),
            (2, "Payments hardening", GoalStatus.OPEN),
            (3, "Billing service foundations", GoalStatus.ACHIEVED),
        ]

    async def test_leaves_some_cards_on_no_goal_at_all(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """Which is the board most projects have, and the one worth sampling:
        every card under an epic would make the rail say nothing."""
        rows = await session.execute(
            select(Task.number, Goal.name)
            .join(Project, Project.id == Task.project_id)
            .outerjoin(Goal, Goal.id == Task.goal_id)
            .where(Project.key == "ATL", Task.parent_id.is_(None))
            .order_by(Task.number)
        )
        by_number = dict(rows.tuples().all())

        assert by_number[41] == "Ledger cutover"
        assert by_number[35] == "Payments hardening"
        assert by_number[19] == "Billing service foundations"
        assert by_number[33] is None
        assert by_number[27] is None

    async def test_splits_a_card_into_sub_tasks(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """ATL-35 shows both kinds at once, which is the whole point of them."""
        atlas = await project_by(session, "ATL")
        parent = await session.scalar(
            select(Task).where(Task.project_id == atlas.id, Task.number == 35)
        )
        assert parent is not None

        children = list(
            await session.scalars(
                select(Task).where(Task.parent_id == parent.id).order_by(Task.sub_number)
            )
        )
        boxes = list(
            await session.scalars(
                select(TaskChecklistItem)
                .where(TaskChecklistItem.task_id == parent.id)
                .order_by(TaskChecklistItem.position)
            )
        )

        assert [child.reference for child in children] == ["ATL-35-1", "ATL-35-2"]
        assert [box.state for box in boxes] == [
            ChecklistState.DONE,
            ChecklistState.OPEN,
            ChecklistState.CANCELLED,
        ]

    async def test_a_blocked_task_carries_its_reason_and_its_tags(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """Written by the status-change rule, not by the seed remembering to."""
        task = await task_by(session, "ATL", 30)
        await session.refresh(task, ["waiting_on"])

        assert task.status is TaskStatus.BLOCKED
        assert sorted(person.name for person in task.waiting_on) == ["Lena W", "Sanjay F"]

        entry = await session.scalar(
            select(TaskComment).where(
                TaskComment.task_id == task.id, TaskComment.kind == CommentKind.STATUS_CHANGE
            )
        )
        assert entry is not None
        assert entry.body == (
            "Blocked — Font licence for the invoice template hasn't been approved by legal."
        )
        assert entry.meta["from"] == "active"
        assert entry.meta["to"] == "blocked"
        assert len(entry.meta["tagged"]) == 2

    async def test_an_on_hold_task_carries_its_reason(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        task = await task_by(session, "ATL", 33)
        await session.refresh(task, ["waiting_on"])

        assert task.status is TaskStatus.HOLD
        assert [person.name for person in task.waiting_on] == ["Sanjay F"]

    async def test_keeps_the_mocks_comments(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        task = await task_by(session, "ATL", 41)
        bodies = await session.scalars(
            select(TaskComment.body).where(
                TaskComment.task_id == task.id, TaskComment.kind == CommentKind.COMMENT
            )
        )

        assert list(bodies) == ["Can we keep amounts as integer minor units?"]

    async def test_files_sit_in_the_project_root(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """The mock's README lives beside the tree, which needed the root to be a real row."""
        atlas = await project_by(session, "ATL")
        root = await session.scalar(
            select(Folder).where(Folder.project_id == atlas.id, Folder.parent_id.is_(None))
        )
        assert root is not None
        assert root.name == "Atlas Billing Migration"

        items = list(await session.scalars(select(FileItem).where(FileItem.folder_id == root.id)))

        assert sorted(item.name for item in items) == ["Project brief", "README.md"]
        assert {item.kind for item in items} == {ItemKind.FILE, ItemKind.LINK}

    async def test_an_uploaded_file_has_content_on_disk(
        self, session: AsyncSession, seeded: seed.Summary, store: BlobStore
    ) -> None:
        item = await session.scalar(select(FileItem).where(FileItem.name == "README.md"))
        assert item is not None
        assert item.blob is not None

        assert await store.exists(item.blob.path)
        assert item.size == item.blob.size

    async def test_builds_the_nested_folder_tree(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        year = await session.scalar(select(Folder).where(Folder.name == "2025"))
        assert year is not None
        assert year.parent_id is not None

        parent = await session.get(Folder, year.parent_id)
        assert parent is not None
        assert parent.name == "Finance inputs"

    async def test_a_vault_secret_decrypts_to_the_mocks_value(
        self, session: AsyncSession, seeded: seed.Summary, cipher: VaultCipher
    ) -> None:
        node = await node_by(session, "Dashboard (test)")

        assert node.kind is VaultNodeKind.SECRET
        assert await vault.reveal(session, cipher, node) == "Qv7!mR2p#xL9"
        assert node.secret is not None
        assert node.secret.username == "billing@think41.com"
        assert node.secret.notes == "2FA on the shared Authy."

    async def test_nests_the_vault_the_way_the_mock_does(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        stripe = await node_by(session, "Stripe")
        assert stripe.kind is VaultNodeKind.BRANCH

        children = await session.scalars(
            select(VaultNode.name).where(VaultNode.parent_id == stripe.id)
        )
        assert sorted(children) == ["Dashboard (test)", "Restricted API key"]

    async def test_writes_nothing_to_the_audit_trail(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """A seed is a starting state, not a change somebody made."""
        assert await count_of(session, Activity) == 0

    async def test_reports_what_it_wrote(self, seeded: seed.Summary) -> None:
        assert seeded.tasks == 15
        assert seeded.people == 7
        assert seeded.secrets == 14
        assert "15 tasks" in seeded.render()


class TestSurveying:
    async def test_an_empty_database_is_empty(self, session: AsyncSession) -> None:
        assert (await seed.survey(session)).is_empty

    async def test_lists_the_projects_it_would_delete(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        inventory = await seed.survey(session)

        assert not inventory.is_empty
        assert inventory.projects == [
            ("ATL", "Atlas Billing Migration"),
            ("HRM", "Hermes Notifications"),
            ("ORB", "Orbit Internal Portal"),
        ]
        assert inventory.tasks == 15
        assert inventory.people == 7

    async def test_does_not_count_the_root_folders(
        self, session: AsyncSession, seeded: seed.Summary
    ) -> None:
        """Nobody made them, so reporting them would overstate the damage."""
        inventory = await seed.survey(session)

        assert inventory.folders == 5
        assert await count_of(session, Folder) == 8  # five, plus a root per project


class TestRefusing:
    async def test_will_not_seed_over_existing_projects(
        self, settings: Settings, seeded: seed.Summary, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = await seed.run(settings, force=False, assume_yes=False)

        assert code == 1
        assert "Refusing to seed over it" in capsys.readouterr().err

    async def test_says_what_is_in_the_way(
        self, settings: Settings, seeded: seed.Summary, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await seed.run(settings, force=False, assume_yes=False)

        message = capsys.readouterr().err
        assert "ATL  Atlas Billing Migration" in message
        assert "15 tasks" in message
        assert "--force" in message

    async def test_leaves_the_data_alone_when_it_refuses(
        self, session: AsyncSession, settings: Settings, seeded: seed.Summary
    ) -> None:
        await seed.run(settings, force=False, assume_yes=False)

        assert await count_of(session, Project) == 3

    async def test_will_not_delete_unattended_without_yes(
        self, settings: Settings, seeded: seed.Summary, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """pytest gives no terminal, which is exactly the case this guards."""
        code = await seed.run(settings, force=True, assume_yes=False)

        assert code == 1
        assert "Refusing to delete without confirmation" in capsys.readouterr().err

    async def test_the_refused_force_deletes_nothing(
        self, session: AsyncSession, settings: Settings, seeded: seed.Summary
    ) -> None:
        await seed.run(settings, force=True, assume_yes=False)

        assert await count_of(session, Project) == 3
        assert await count_of(session, Task) == 15

    @pytest.mark.parametrize("environment", ["prod", "staging", "preview"])
    async def test_will_not_run_in_a_deployed_environment(
        self,
        settings: Settings,
        capsys: pytest.CaptureFixture[str],
        environment: str,
    ) -> None:
        # Staging is refused for the same reason production is: it is restored
        # from a production dump, so seeding it writes fiction over real rows.
        # Preview holds no such dump, but it is still a deployment rather than
        # someone's machine, and that is what is_deployed actually gates on.
        deployed = settings.model_copy(update={"environment": environment})

        code = await seed.run(deployed, force=True, assume_yes=True)

        assert code == 1
        error = capsys.readouterr().err
        assert "deployed environment" in error
        assert f"CYLIST_ENVIRONMENT={environment}" in error

    @pytest.mark.parametrize("environment", ["prod", "staging", "preview"])
    async def test_a_deployed_environment_is_refused_before_anything_is_written(
        self, session: AsyncSession, settings: Settings, environment: str
    ) -> None:
        deployed = settings.model_copy(update={"environment": environment})

        await seed.run(deployed, force=True, assume_yes=True)

        assert await count_of(session, Project) == 0

    async def test_says_how_to_get_a_vault_key(
        self, settings: Settings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        keyless = settings.model_copy(update={"vault_key": ""})

        code = await seed.run(keyless, force=False, assume_yes=False)

        assert code == 1
        assert "generate-vault-key" in capsys.readouterr().err

    async def test_names_the_database_it_is_about_to_write_to(
        self, settings: Settings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await seed.run(settings, force=False, assume_yes=True)

        assert "Seeding postgresql+asyncpg://" in capsys.readouterr().out


class TestForcing:
    async def test_seeds_an_empty_database(self, session: AsyncSession, settings: Settings) -> None:
        code = await seed.run(settings, force=False, assume_yes=True)

        assert code == 0
        assert await count_of(session, Project) == 3

    async def test_says_what_it_will_delete_before_deleting_it(
        self, settings: Settings, seeded: seed.Summary, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await seed.run(settings, force=True, assume_yes=True)

        printed = capsys.readouterr().out
        warning = printed.index("permanently delete")
        assert printed.index("ATL  Atlas Billing Migration") > warning
        assert printed.index("Seeded 3 projects") > warning

    async def test_reseeds_rather_than_duplicating(
        self, session: AsyncSession, settings: Settings, seeded: seed.Summary
    ) -> None:
        code = await seed.run(settings, force=True, assume_yes=True)

        assert code == 0
        assert await count_of(session, Project) == 3
        assert await count_of(session, Person) == 7
        assert await count_of(session, Task) == 15

    async def test_leaves_the_audit_trail_and_the_tokens_alone(
        self, session: AsyncSession, settings: Settings, seeded: seed.Summary
    ) -> None:
        """Both outlive the data they describe, which is why they are not cascaded."""
        session.add(
            Activity(
                actor_label="someone",
                channel="web",  # type: ignore[arg-type]
                verb="project.created",
                entity_type="project",
            )
        )
        session.add(
            ApiToken(
                name="a script",
                token_hash="0" * 64,
                kind=TokenKind.API,
                scopes=["read"],
            )
        )
        await session.commit()

        await seed.run(settings, force=True, assume_yes=True)

        assert await count_of(session, Activity) == 1
        assert await count_of(session, ApiToken) == 1

    async def test_clears_the_blobs_nothing_points_at_any_more(
        self, session: AsyncSession, settings: Settings, seeded: seed.Summary
    ) -> None:
        before = await count_of(session, Blob)
        assert before > 0

        await seed.run(settings, force=True, assume_yes=True)

        # The same placeholders go back in, so the count returns to where it
        # was rather than doubling.
        assert await count_of(session, Blob) == before


class TestFailingPartWay:
    @staticmethod
    def _explode(monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the vault refuse to write, once the rest of a project is in place."""

        async def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("the disk fell over")

        monkeypatch.setattr(seed.vault, "create_node", boom)

    async def test_leaves_an_empty_database_empty(
        self, session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._explode(monkeypatch)

        with pytest.raises(RuntimeError, match="the disk fell over"):
            await seed.run(settings, force=False, assume_yes=True)

        assert await count_of(session, Project) == 0
        assert await count_of(session, Person) == 0

    async def test_a_failed_force_puts_nothing_at_risk(
        self,
        session: AsyncSession,
        settings: Settings,
        seeded: seed.Summary,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The wipe and the reseed are one transaction, so a bad reseed undoes the wipe."""
        self._explode(monkeypatch)

        with pytest.raises(RuntimeError, match="the disk fell over"):
            await seed.run(settings, force=True, assume_yes=True)

        assert await count_of(session, Project) == 3
        assert await count_of(session, Task) == 15
        assert await count_of(session, Person) == 7

    async def test_the_bytes_survive_a_failed_force(
        self,
        session: AsyncSession,
        settings: Settings,
        store: BlobStore,
        seeded: seed.Summary,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Blobs are unlinked only after the transaction that dropped them commits."""
        item = await session.scalar(select(FileItem).where(FileItem.name == "README.md"))
        assert item is not None and item.blob is not None
        path = item.blob.path

        self._explode(monkeypatch)
        with pytest.raises(RuntimeError):
            await seed.run(settings, force=True, assume_yes=True)

        assert await store.exists(path)


class TestTheCommandLine:
    def test_rejects_a_url_with_the_wrong_driver(self) -> None:
        """The sync driver would block the event loop, so it is refused here too."""
        with pytest.raises(SystemExit):
            seed.main(["--url", "postgresql://cylist@localhost/cylist"])

    def test_handles_being_asked_for_help(self) -> None:
        with pytest.raises(SystemExit) as exit_info:
            seed.main(["--help"])

        assert exit_info.value.code == 0


class TestTheSampleItself:
    """Checks on the data, so a typo in it fails here rather than at runtime."""

    def test_every_handle_a_project_uses_is_in_the_directory(self) -> None:
        known = {person.handle for person in seed.DIRECTORY}

        for spec in seed.SAMPLE:
            used = {*spec.members}
            for task in spec.tasks:
                used |= {task.assignee, *task.waiting_on, *(author for author, _ in task.comments)}
            for item in (*spec.root_items, *_all_items(spec)):
                used.add(item.added_by)

            assert used <= known, f"{spec.key} names people who are not in the directory"

    def test_everyone_a_task_names_is_on_that_project(self) -> None:
        """Otherwise the seed would trip the membership rule at runtime."""
        for spec in seed.SAMPLE:
            members = set(spec.members)
            for task in spec.tasks:
                assert {task.assignee, *task.waiting_on} <= members, f"{spec.key}-{task.number}"

    def test_a_stalled_task_always_has_a_reason(self) -> None:
        for spec in seed.SAMPLE:
            for task in spec.tasks:
                if task.status is not TaskStatus.ACTIVE:
                    assert task.reason, f"{spec.key}-{task.number} is {task.status} with no reason"

    def test_no_two_people_share_a_handle(self) -> None:
        handles = [person.handle for person in seed.DIRECTORY]

        assert len(handles) == len(set(handles))

    def test_every_board_keeps_at_least_two_columns(self) -> None:
        for spec in seed.SAMPLE:
            assert len(spec.columns) >= 2, spec.key

    def test_every_task_names_a_column_the_board_has(self) -> None:
        for spec in seed.SAMPLE:
            for task in spec.tasks:
                assert 0 <= task.column < len(spec.columns), f"{spec.key}-{task.number}"


def _all_items(spec: seed.ProjectSpec) -> list[seed.FileSpec]:
    found: list[seed.FileSpec] = []

    def walk(folders: tuple[seed.FolderSpec, ...]) -> None:
        for folder in folders:
            found.extend(folder.items)
            walk(folder.children)

    walk(spec.folders)
    return found
