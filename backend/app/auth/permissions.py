"""What a role lets its holder do, as a fixed vocabulary.

CYLIST-45 gave a project roles and said, deliberately, that a role permits
nothing: it was a word an admin hands out, and the only authority any of them
carried was the admin role's own. This is the other half. A role now carries a
set of permissions, and every one of them is a name from the list below.

**Fixed, not free-form.** A role's *name* is the admin's invention — "QA",
"Reviewer" — because only they know what their board calls people. What a role
may do cannot be, because every entry here is a fence the server has to
recognise at a particular endpoint. An admin who could invent "can approve
budgets" would have invented a word nothing checks.

**Actions, not reading.** Everything here is something a caller *does*.
Reading a project you can reach is not fenced by a role, which keeps Cylist's
projects the shared workspace decision 4 called them: a board whose cards half
the team cannot see is a different product, and nobody has asked for it. The
one read-shaped entry, :attr:`Permission.VAULT_REVEAL`, is here because
revealing a secret was already a distinct, logged, separately scoped act
before roles existed.

**Two gates, still.** A permission answers "who is this person on this board";
:class:`~app.auth.scopes.Scope` answers "what may this credential do at all".
Both are checked, and neither can stand in for the other — a read-only token
held by an admin still cannot write, and a `write` token held by somebody whose
role does not allow cards still cannot move one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    """One thing a role may allow. The values are stored, so they are stable."""

    TASKS = "tasks"
    """Create, edit, move, finish and delete cards, sub-tasks and checklists."""

    COMMENTS = "comments"
    """Say something on a card.

    Separate from :attr:`TASKS` because the two are genuinely different jobs —
    a client who should never move a card is often exactly the person whose
    comment you want. The reason attached to a status change is not this: it
    is part of making the change, and is fenced with the change itself.
    """

    GOALS = "goals"
    """Create, rename, retarget, drop and finish this project's goals."""

    GOAL_ASSIGN = "goal_assign"
    """Put a card on a goal, or take it off.

    Separate from :attr:`GOALS` because it is the one goal-shaped thing that
    happens daily, on a card, by whoever is doing the work — while renaming a
    goal or moving its target date is a decision about the plan. A board where
    only the person who owns the plan may say which goal a card counts towards
    is a board whose goals are always slightly out of date.
    """

    GOAL_OWNER = "goal_owner"
    """Change whose goal a goal is.

    Its own right because it is the one edit that reassigns work to a person
    rather than describing it. Naming the owner while *creating* a goal is
    part of creating it and needs only :attr:`GOALS`; handing an existing one
    to somebody else is this.
    """

    BOARD = "board"
    """Change the shape of the board: its columns, and its card templates."""

    FILES = "files"
    """Create folders, upload files, add links, rename and delete them."""

    VAULT = "vault"
    """Add, change and delete vault entries — never read their secrets."""

    VAULT_REVEAL = "vault_reveal"
    """Decrypt and read a stored secret. Every reveal is written to the log."""

    PEOPLE = "people"
    """Say who is on this project.

    Membership only. The people *directory* is global — one entry for somebody
    who works on three boards — so who may edit it is not a question one
    project's role can answer, and it stays behind the `write` scope.
    """

    AGENTS = "agents"
    """Upload the skills and write the notes this project's agents work from."""

    PROJECT = "project"
    """Rename, describe and archive the project itself."""


@dataclass(frozen=True, slots=True)
class PermissionInfo:
    """One line of the grid an admin configures, and the words on it."""

    key: Permission
    label: str
    """The column heading — two or three words, read at a glance."""

    summary: str
    """What ticking it allows, in the sentence a hover would show."""

    refusal: str
    """How the refusal finishes the sentence "your role does not let you …"."""


CATALOGUE: tuple[PermissionInfo, ...] = (
    PermissionInfo(
        Permission.TASKS,
        "Cards",
        "Create, edit, move and finish cards, sub-tasks and checklists.",
        "change cards",
    ),
    PermissionInfo(
        Permission.COMMENTS,
        "Comments",
        "Say something on a card.",
        "comment on cards",
    ),
    PermissionInfo(
        Permission.GOALS,
        "Goals",
        "Create, rename, retarget, drop and finish goals.",
        "change goals",
    ),
    PermissionInfo(
        Permission.GOAL_ASSIGN,
        "Goal of a card",
        "Say which goal a card counts towards.",
        "say which goal a card is on",
    ),
    PermissionInfo(
        Permission.GOAL_OWNER,
        "Goal owner",
        "Hand an existing goal to somebody else.",
        "change whose goal a goal is",
    ),
    PermissionInfo(
        Permission.BOARD,
        "Board shape",
        "Add, rename, reorder and remove columns, and manage card templates.",
        "change the board's shape",
    ),
    PermissionInfo(
        Permission.FILES,
        "Files",
        "Create folders, upload files and add links.",
        "change this project's files",
    ),
    PermissionInfo(
        Permission.VAULT,
        "Vault entries",
        "Add, change and delete credentials — not read their secrets.",
        "change the vault",
    ),
    PermissionInfo(
        Permission.VAULT_REVEAL,
        "Reveal secrets",
        "Decrypt and read a stored secret. Every reveal is logged.",
        "reveal secrets",
    ),
    PermissionInfo(
        Permission.PEOPLE,
        "Membership",
        "Say who is on this project.",
        "change who is on this project",
    ),
    PermissionInfo(
        Permission.AGENTS,
        "Agent skills",
        "Upload the skills and write the notes this project's agents work from.",
        "change what this project's agents work from",
    ),
    PermissionInfo(
        Permission.PROJECT,
        "Project settings",
        "Rename, describe and archive this project.",
        "change this project's settings",
    ),
)
"""Every permission, in the order the screen draws them.

The order is deliberate rather than alphabetical: the everyday ones first, the
ones that change what other people see after them, and the vault in between
because it is the pair most admins come to the screen for.
"""

INFO: dict[Permission, PermissionInfo] = {entry.key: entry for entry in CATALOGUE}

ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)
"""What an admin holds, and what every role holds on a project nobody has
narrowed yet."""


def parse_permissions(values: list[str]) -> frozenset[Permission]:
    """Convert stored strings to :class:`Permission`, dropping unknown values.

    Unknown strings are ignored rather than raising, for the reason
    :func:`~app.auth.scopes.parse_scopes` ignores them: a row written by a
    newer Cylist — or left behind by one whose vocabulary has since shrunk —
    must not make a project impossible to read. A permission the server does
    not recognise is one it cannot be enforcing either way.
    """
    known = {permission.value for permission in Permission}
    return frozenset(Permission(value) for value in values if value in known)
