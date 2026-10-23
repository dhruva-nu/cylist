"""The shared walks over a parent-linked hierarchy."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.core import tree
from app.db import Database
from app.models.file import Folder
from app.services import trees


@dataclass
class Row:
    id: UUID
    parent_id: UUID | None


def chain(depth: int) -> list[Row]:
    """A root and ``depth - 1`` rows beneath it, each the child of the one before."""
    rows = [Row(uuid4(), None)]
    for _ in range(depth - 1):
        rows.append(Row(uuid4(), rows[-1].id))
    return rows


def held(rows: list[Row]) -> dict[UUID, Row]:
    return {row.id: row for row in rows}


class TestAncestors:
    def test_lists_what_is_above_nearest_first(self) -> None:
        rows = chain(4)

        assert tree.ancestors(held(rows), rows[3]) == [rows[2], rows[1], rows[0]]

    def test_a_root_has_nothing_above_it(self) -> None:
        rows = chain(1)

        assert tree.ancestors(held(rows), rows[0]) == []

    def test_stops_at_the_limit(self) -> None:
        rows = chain(6)

        assert tree.ancestors(held(rows), rows[5], limit=2) == [rows[4], rows[3]]

    def test_stops_at_a_parent_that_is_not_there(self) -> None:
        rows = chain(4)
        del_middle = held(rows)
        del del_middle[rows[1].id]

        assert tree.ancestors(del_middle, rows[3]) == [rows[2]]

    def test_a_ring_in_the_data_ends_the_walk_instead_of_hanging_it(self) -> None:
        first, second = Row(uuid4(), None), Row(uuid4(), None)
        first.parent_id, second.parent_id = second.id, first.id

        assert tree.ancestors(held([first, second]), first) == [second]


class TestIsInside:
    def test_true_for_anything_above(self) -> None:
        rows = chain(4)

        assert tree.is_inside(held(rows), rows[3], rows[0].id)
        assert tree.is_inside(held(rows), rows[3], rows[2].id)

    def test_false_for_itself_and_for_what_is_below(self) -> None:
        rows = chain(4)

        assert not tree.is_inside(held(rows), rows[1], rows[1].id)
        assert not tree.is_inside(held(rows), rows[1], rows[3].id)

    def test_true_even_where_the_ancestor_is_not_held(self) -> None:
        rows = chain(3)
        outside = held(rows[1:])

        assert tree.is_inside(outside, rows[2], rows[0].id)

    def test_a_ring_is_a_no_rather_than_a_hang(self) -> None:
        first, second = Row(uuid4(), None), Row(uuid4(), None)
        first.parent_id, second.parent_id = second.id, first.id

        assert not tree.is_inside(held([first, second]), first, uuid4())


class TestInTheDatabase:
    async def test_subtree_and_ancestors_follow_the_parent_links(self, database: Database) -> None:
        from app.models.project import Project

        async with database.session() as session:
            project = Project(key="TRE", name="Trees", description="", colour="#4A7B8C")
            session.add(project)
            await session.flush()
            root = Folder(project_id=project.id, name="Trees", parent_id=None)
            session.add(root)
            await session.flush()
            made = [root]
            for level in range(5):
                made.append(Folder(project_id=project.id, name=f"l{level}", parent_id=made[-1].id))
                session.add(made[-1])
                await session.flush()

            beneath_two = set(await session.scalars(trees.subtree_ids(Folder, made[2].id)))
            above_five = await trees.ancestors(session, Folder, made[5].parent_id, limit=10)
            capped = await trees.ancestors(session, Folder, made[5].parent_id, limit=2)
            nothing = await trees.ancestors(session, Folder, None, limit=10)
            await session.rollback()

        assert beneath_two == {folder.id for folder in made[2:]}
        assert [folder.id for folder in above_five] == [folder.id for folder in reversed(made[:5])]
        assert len(capped) == 3  # limit + 1: enough to tell "past it" from "at it"
        assert nothing == []
