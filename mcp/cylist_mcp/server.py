"""The server: its instructions, and which tools it offers a given token.

The tools themselves live in :mod:`cylist_mcp.tools`, one module per thing on
the board they act on. This module only decides which of them exist and in
what order a client sees them. Two conventions run through all of them:
errors are results rather than exceptions (see :mod:`cylist_mcp.tools.common`),
and a tool that cannot work for this token is not offered at all (see
:mod:`cylist_mcp.tools.vault`).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from cylist_mcp import __version__
from cylist_mcp.client import Api
from cylist_mcp.tools import (
    activity,
    docs,
    files,
    goals,
    human,
    people,
    projects,
    skills,
    tasks,
    vault,
)
from cylist_mcp.tools.activity import local_timezone
from cylist_mcp.tools.vault import VAULT_REVEAL, VAULT_WRITE

__all__ = [
    "INSTRUCTIONS",
    "INSTRUCTIONS_SEEN",
    "VAULT_REVEAL",
    "VAULT_WRITE",
    "build_server",
    "local_timezone",
]

INSTRUCTIONS_SEEN = 2048
"""How much of the instructions a client can be relied on to show. Claude Code
cuts them off at this many characters, so what an agent must do on every card
goes before it; the tests hold the docs paragraph to that."""

INSTRUCTIONS = """\
Cylist is a project manager: each project has a Kanban board, a people
directory, files and a vault of credentials.

Every project has **docs**: markdown filed as section (Product,
Engineering) → topic → doc, much of it written by agents before you. Ask
them before you read the code, and teach them what they could not answer:

1. Whenever you would open the repo to answer a question — where a thing
   lives, how it works, why it is the way it is — `ask_docs` it first, in
   plain words. `ok` hands you the one section that answers it: act on it.
2. Any other status means the docs do not know yet. Find the answer in the
   code, then straight away — not at the end, a session can stop first —
   `place_doc` what you found, one fact per sentence, and make the edits it
   plans with `write_doc`, in the shape it says each doc is written in. The
   next agent to ask gets it from the docs.
3. Facts, not a progress log — the board already reports that. Topics are
   made by people, never agents. If a section has stopped being true, write
   the correction.

Two things are worth knowing before you start.

Projects and tasks are addressed by their human reference, not a UUID: a
project is `ATL`, a task on it is `ATL-41`. Anywhere a person, column or folder
is named you may pass the name you saw on screen ("In progress", "Aditi K")
and it will be resolved; an ambiguous name comes back as an error listing the
candidates rather than a guess.

A task cannot be put on hold or blocked without a reason. That is enforced by
the server, not by politeness — `set_task_status` needs `reason` for `hold`
and `blocked`, and `waiting_on` should name whoever the work is now waiting on.
The same goes for `cancelled`, which is how work that is not going to happen
stops holding up the card it belongs to.

A card can be written under a **goal** — an epic like "Search revamp" — which
is what the board colours it by. `list_goals` shows a project's goals with the
progress counted from their own cards; `set_task_goal` puts a card on one or
takes it off. A card belongs to at most one goal, and a goal cannot be marked
achieved while a card on it is still open — `set_goal_status` refuses that and
names the cards holding it open. The other way round is refused too: an open
card cannot be written on, put on, moved back out of the last column on, or
uncancelled on an achieved goal; the refusal names the goal to reopen first.

A task can be split two ways. `create_subtask` makes a sub-task with a
reference of its own, `ATL-41-2`, addressable like any other task and carrying
its own owner, due date and timeline; `add_checklist_item` makes a tick box that
lives on the parent alone. Neither is on the board — a sub-task has no column,
so `move_task` refuses one and `finish_subtask` is what completes it. Either
way, every one of them has to be finished or cancelled before the parent can be
moved into the board's last column, and `move_task` refuses that too, naming
what is still outstanding. Once the parent is there, no sub-task can be added,
reopened or uncancelled under it: move the parent back out first.

Each project carries what its agents work from. `list_skills` and `read_skill`
give you the procedures somebody has already written down for this project —
worth a look before improvising one.

When you cannot decide something alone, `ask_human` puts the question on
your card and waits for the person's answer; do not use it for progress.

Progress is reported for you. When a session is bound to a card — someone ran
`cylist work ATL-41`, or typed `/work ATL-41` — the harness's own hooks tell
the board when you are working, when you are waiting on a human, and when the
session ends. You do not need to announce that you have started or finished,
and there is no tool for it; `agent_session` on a card is what that reporting
looks like from the outside.

The board's last column is where a card is done: moving one in records the
moment it was finished, and moving it back out reopens it. That column alone
may be divided into up to three **outcomes** — "Done", "Cancelled", "In prod" —
which `get_project` lists beside it; `move_task` takes the name of one to say
how the work ended. A template may narrow which of them its own cards are
allowed to end on.
"""


def forbid_unknown_arguments(server: MCPServer) -> None:
    """Make every tool refuse an argument it does not declare.

    Pydantic drops extras by default, so a misnamed filter (``colunm``) was
    accepted and the caller got an unfiltered answer it believed was filtered.
    An error names the unexpected field and lets the model correct itself.
    """
    for tool in server._tool_manager.list_tools():  # no public hook for this
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)


def build_server(client: Api, scopes: frozenset[str], *, hosted: bool = False) -> MCPServer:
    """Wire the tools onto one API client, honouring the token's scopes.

    ``hosted`` is for the copy served over HTTP by the backend itself (see
    :mod:`cylist_mcp.hosted`). The tools are the same; what changes is that
    "this machine" is no longer the caller's, so nothing is defaulted from it.

    The order below is the order a client lists the tools in, so it runs from
    the board outwards: projects and their cards first, the vault's write
    tools last, since only some tokens get them.
    """
    server = MCPServer(name="cylist", version=__version__, instructions=INSTRUCTIONS)

    projects.register(server, client)
    tasks.register(server, client)
    human.register(server, client)
    goals.register(server, client)
    people.register(server, client)
    files.register(server, client)
    vault.register(server, client)
    activity.register(server, client, hosted=hosted)
    skills.register(server, client)
    docs.register(server, client)

    if scopes >= VAULT_WRITE:
        vault.register_writes(server, client)
    if VAULT_REVEAL in scopes:
        vault.register_reveal(server, client)

    forbid_unknown_arguments(server)
    return server
