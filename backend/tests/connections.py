"""Checking that nothing waits on somebody else's network with a connection in hand.

A request that holds a pooled connection while jev reads a question, or while a
hook's receiver takes its time, keeps that connection from every other request
for as long as the wait lasts — and a few of those at once starved the pool,
``/health`` included (CYLIST-131). :func:`assert_no_connection_held` is called
by the tests' stand-ins for the outside world — the fake jev engine, the fake
hook receiver — at the moment they are called, so any route that reaches one
of them with a connection checked out fails its test, whichever test it is.

It counts the whole pool, so a test that holds a session of its own across a
call to the outside world has to switch its stand-in's check off; the ones that
do say why.
"""

from __future__ import annotations

from sqlalchemy.pool import QueuePool

from app.db import Database


def connections_held(database: Database) -> int:
    """How many of ``database``'s pooled connections are checked out right now."""
    pool = database.engine.pool
    assert isinstance(pool, QueuePool), f"expected a queue pool, got {type(pool).__name__}"
    return pool.checkedout()


def assert_no_connection_held(database: Database, waiting_on: str) -> None:
    """Fail unless every pooled connection is back in the pool.

    ``waiting_on`` names the outside call, for the failure message.
    """
    held = connections_held(database)
    assert held == 0, (
        f"{held} database connection(s) held while waiting on {waiting_on}; "
        "release the session before calling out (Database.release)"
    )
