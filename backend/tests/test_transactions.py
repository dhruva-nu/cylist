"""When a write becomes visible to the next request.

A 201 that another connection cannot yet read is a 201 that lies. The browser
is the caller that finds out: a dialog saves, its success handler refetches the
list straight away, and the list comes back without the thing just created —
which looks exactly like a bug in the screen and is not one.

FastAPI decides this with the ``scope`` of a dependency that yields. At
``"function"`` it closes before the response is sent; by default it closes
after the response has already gone out. The commit lives in that closing code,
so the default hands the client a receipt for a row nothing else can see yet.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.dependencies.models import Dependant
from httpx import AsyncClient

from app.config import Settings
from app.db import get_session
from app.main import create_app

PERSON = {
    "name": "Priya R",
    "kind": "team",
    "role": "Platform engineer",
    "responsibilities": "Keeps the deploys boring.",
}


def _routes(node: object, seen: set[int] | None = None) -> Iterator[object]:
    """Every route reachable from an app.

    ``include_router`` leaves one router object in ``app.routes`` rather than
    the routes themselves, so the real ones are a level down through
    ``original_router``.
    """
    seen = set() if seen is None else seen
    if id(node) in seen:
        return
    seen.add(id(node))

    for route in getattr(node, "routes", []):
        yield route
        yield from _routes(route, seen)

    included = getattr(node, "original_router", None)
    if included is not None:
        yield from _routes(included, seen)


def _dependencies(dependant: Dependant) -> Iterator[Dependant]:
    """One route's dependencies, however deeply nested."""
    yield dependant
    for sub in dependant.dependencies:
        yield from _dependencies(sub)


class TestCommitOrdering:
    def test_every_route_closes_its_session_before_replying(self, settings: Settings) -> None:
        """The scope is the fix; this is the test that keeps it.

        Read off the route table rather than by racing two requests: in-process
        the whole ASGI call is awaited before the test client sees anything, so
        the window this guards is invisible here and plain in a browser.
        """
        app = create_app(settings)

        checked = 0
        late = set()
        for route in _routes(app):
            dependant = getattr(route, "dependant", None)
            if not isinstance(dependant, Dependant):
                continue
            for dependency in _dependencies(dependant):
                if dependency.call is not get_session:
                    continue
                checked += 1
                if dependency.scope != "function":
                    late.add(str(getattr(route, "path", route)))

        assert checked > 0, "Found no session dependencies at all — has the traversal drifted?"
        assert not late, (
            "These routes commit after the response has been sent, so a client that "
            "refetches immediately can read stale data. Depend on "
            f"app.db.SessionDependency: {', '.join(sorted(late))}"
        )

    async def test_a_created_person_is_in_the_very_next_listing(
        self, signed_in: AsyncClient
    ) -> None:
        created = await signed_in.post("/people", json=PERSON)

        listed = (await signed_in.get("/people")).json()

        assert created.status_code == 201
        assert [person["id"] for person in listed] == [created.json()["id"]]

    async def test_an_archived_person_is_out_of_the_very_next_listing(
        self, signed_in: AsyncClient
    ) -> None:
        created = (await signed_in.post("/people", json=PERSON)).json()

        await signed_in.delete(f"/people/{created['id']}")

        assert (await signed_in.get("/people")).json() == []
