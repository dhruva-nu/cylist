"""Tasks: creating them, moving them, stalling them, talking about them.

Several rules here are worth stating outright, because they are choices rather
than mechanics:

* a new task lands in the first column and nowhere else — work enters a board
  at one end, and letting a client drop a card straight into "Done" makes the
  board a record of intentions rather than of progress;
* a card created from a template goes only where that template's stages
  allow it — see :mod:`app.services.templates`, which owns that rule and is
  asked about it here twice: once to decide where a new card lands, and once
  on every move;
* a card lands on the sub-stages its template set for the column it is in —
  its own ``sub_statuses``, reloaded from the template every time the card
  arrives somewhere new — and cannot leave a column while it is short of the
  last one; see :func:`~app.services.tasks.moves.refuse_stage_incomplete`.
  This is checked on every move out of the column, not only into the board's
  last one;
* a task cannot go on hold or become blocked without a reason — a red card
  that does not say why is a question, not information;
* a card with unfinished sub-tasks cannot reach the board's last column. A
  ticket whose parts are still open is not done, and the board saying
  otherwise is how work gets forgotten rather than finished;
* a sub-task is not on the board. It has no column, it cannot be moved, and it
  is finished by :func:`set_finished` rather than by being dragged somewhere —
  one piece of work is one card, and a card dealt out across four columns is
  four things to read where there was one thing to do;
* a goal is set on a card, not on a sub-task. A sub-task belongs to its card,
  and its card is what belongs to the goal.

The package is split by what a caller is doing to a card, and every public
call is re-exported here, so ``tasks.move`` reads the same at every call site
whichever module it lives in:

* :mod:`.lookup` finds tasks, and :mod:`.counts` counts them for a listing;
* :mod:`.lifecycle` creates and deletes them;
* :mod:`.editing` changes fields in place, and :mod:`.dates` owns the per-column
  due dates and how they read against the board;
* :mod:`.moves` puts a card in a column and finishes or reopens it;
* :mod:`.cold_storage` takes the finished ones off the board, and puts them back;
* :mod:`.status` stalls and unstalls it;
* :mod:`.timeline` and :mod:`.checklists` are what hangs off a card;
* :mod:`.history` is the one shape every change is reported in, and
  :mod:`.numbering` and :mod:`.stacking` are the bookkeeping underneath.
"""

from app.services.tasks.checklists import (
    add_checklist_item,
    checklist,
    delete_checklist_item,
    get_checklist_item,
    update_checklist_item,
)
from app.services.tasks.cold_storage import (
    Stored,
    list_stored,
    restore,
    store_finished,
    stored_outcome,
)
from app.services.tasks.counts import (
    NOT_SPLIT,
    Split,
    card_count,
    status_counts,
    subtask_counts,
    subtask_owners,
)
from app.services.tasks.dates import BoardDates, ColumnDue, board_dates, set_column_due_dates
from app.services.tasks.editing import set_sub_status, update
from app.services.tasks.history import FieldChange, field_change
from app.services.tasks.lifecycle import create, delete
from app.services.tasks.lookup import (
    all_for_project,
    get,
    list_for_project,
    require_on_project,
    resolve,
    subtasks,
)
from app.services.tasks.moves import move, set_finished
from app.services.tasks.status import change_status
from app.services.tasks.timeline import (
    comment,
    comment_counts,
    comments,
    delete_comment,
    get_comment,
)

__all__ = [
    "NOT_SPLIT",
    "BoardDates",
    "ColumnDue",
    "FieldChange",
    "Split",
    "Stored",
    "add_checklist_item",
    "all_for_project",
    "board_dates",
    "card_count",
    "change_status",
    "checklist",
    "comment",
    "comment_counts",
    "comments",
    "create",
    "delete",
    "delete_checklist_item",
    "delete_comment",
    "field_change",
    "get",
    "get_checklist_item",
    "get_comment",
    "list_for_project",
    "list_stored",
    "move",
    "require_on_project",
    "resolve",
    "restore",
    "set_column_due_dates",
    "set_finished",
    "set_sub_status",
    "status_counts",
    "store_finished",
    "stored_outcome",
    "subtask_counts",
    "subtask_owners",
    "subtasks",
    "update",
    "update_checklist_item",
]
