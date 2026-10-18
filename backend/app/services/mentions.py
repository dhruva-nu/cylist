"""Who a piece of prose tags: ``@Aditi K`` names a person on the project.

The board recognises a tag when it draws the text — ``frontend/src/components/
mentions.ts`` — and this is the same rule, read once on the server so a hook
can fire on it. The two have to agree, or a hook would fire for a comment the
board draws as plain text, or stay quiet for one it draws as a tag:

* an ``@`` with a letter, digit or underscore just before it is part of a word
  — ``deploy@example.com`` is an address, not a tag on somebody called Example;
* a name counts only when the text does not run on past it — ``@Aditi Kumar``
  is nobody, not "Aditi K" and "umar";
* longest name first, so ``@Aditi K`` is "Aditi K" when "Aditi" is on the
  project too; and names match without regard to case.

Only the project's members can be tagged, because they are who the composer
offers and who the board draws as tags.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person
from app.models.project import ProjectMember

PERSON_SIGIL = "@"


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def tagged_in(text: str, members: Sequence[Person]) -> list[Person]:
    """The people ``text`` tags, each once, in the order first tagged."""
    if PERSON_SIGIL not in text:
        return []

    people = sorted(members, key=lambda person: len(person.name), reverse=True)
    found: dict[UUID, Person] = {}
    index = text.find(PERSON_SIGIL)
    while index != -1:
        person = None
        if index == 0 or not _is_word(text[index - 1]):
            person = _whole_name_at(text, index + 1, people)
        if person is not None:
            found.setdefault(person.id, person)
            index += len(person.name)
        index = text.find(PERSON_SIGIL, index + 1)
    return list(found.values())


def _whole_name_at(text: str, start: int, people: Sequence[Person]) -> Person | None:
    for person in people:
        end = start + len(person.name)
        if text[start:end].lower() != person.name.lower():
            continue
        if end < len(text) and _is_word(text[end]):
            continue
        return person
    return None


async def tagged_on_project(session: AsyncSession, project_id: UUID, text: str) -> list[Person]:
    """The members of the project that ``text`` tags.

    Costs nothing for text without an ``@``, which is most of it.
    """
    if PERSON_SIGIL not in text:
        return []
    members = await session.scalars(
        select(Person)
        .join(ProjectMember, ProjectMember.person_id == Person.id)
        .where(ProjectMember.project_id == project_id)
    )
    return tagged_in(text, list(members))
