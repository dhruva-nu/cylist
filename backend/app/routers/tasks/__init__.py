"""Tasks: the cards on a board, and everything that happens to them.

Every path that names a task accepts either its id or its reference, so an
agent can call `/tasks/ATL-41` with the string a human just read out.

Split by what a path is about — the card itself, its tick boxes, its
timeline, cold storage — with the resolvers and guards they share in
:mod:`.dependencies`. The one way a card is drawn is
:mod:`app.api.presenters.tasks`. The names other routers borrow are
re-exported here.
"""

from fastapi import APIRouter

from app.api.card_deps import WRITE_THIS_CARD, resolved_task
from app.routers.tasks import cards, checklist, cold_storage, comments

router = APIRouter()
router.include_router(cards.router)
router.include_router(checklist.router)
router.include_router(comments.router)
router.include_router(cold_storage.router)

__all__ = ["WRITE_THIS_CARD", "resolved_task", "router"]
