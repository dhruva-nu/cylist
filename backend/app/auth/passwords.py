"""Password hashing.

Argon2id via ``argon2-cffi`` with the library's current defaults, which follow
the OWASP recommendation.

A password belongs to a person and lives in ``person.password_hash``. It is
consulted at ``POST /auth/login`` and nowhere else; everything after that
carries a token instead, which is why this module can afford a deliberately
slow hash.

Slow is the point, and it is also why the request path must never run one on
the event loop (CYLIST-121): a hash or a check costs tens of milliseconds of
CPU, and forty wrong passwords a second would freeze every other request in
the process for as long as they kept coming. :func:`hash_password` and
:func:`verify_password` therefore hand the work to a worker thread. The
``_sync`` variants are for the CLI and the tests, which have no loop to block.

``CYLIST_PASSWORD_HASH`` survives as the bootstrap credential for a deployment
with no accounts in it yet — the one login that is checked against a setting
rather than a row. See :func:`app.routers.auth.login`.
"""

from __future__ import annotations

import secrets

from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

_hasher = PasswordHasher()

_dummy_hash: str | None = None
"""A hash of nothing anybody knows, for checking a password that matches no
account — see :func:`dummy_hash`."""


def hash_password_sync(plaintext: str) -> str:
    """Return an Argon2id hash, including its parameters and salt.

    On the calling thread. For the CLI and the tests; a request handler uses
    :func:`hash_password`.
    """
    return _hasher.hash(plaintext)


def verify_password_sync(plaintext: str, expected_hash: str | None) -> bool:
    """Check a password against a stored hash, on the calling thread.

    Returns ``False`` for a mismatch and for a malformed or empty hash, so a
    person with no password — every client, and every teammate who has not
    accepted their invitation — rejects every login rather than accepting
    every login. The same goes for a deployment that has not set
    ``CYLIST_PASSWORD_HASH``.
    """
    if not expected_hash:
        return False
    try:
        return _hasher.verify(expected_hash, plaintext)
    except (Argon2Error, InvalidHashError):
        return False


async def hash_password(plaintext: str) -> str:
    """:func:`hash_password_sync`, in a worker thread, so the loop keeps serving."""
    return await to_thread.run_sync(hash_password_sync, plaintext)


async def verify_password(plaintext: str, expected_hash: str | None) -> bool:
    """:func:`verify_password_sync`, in a worker thread.

    An empty or missing hash is refused without leaving the loop: there is
    nothing to compute. A caller that wants a refusal to *cost* the same as a
    real check passes :func:`dummy_hash` instead — see
    :func:`app.services.people.authenticate`.
    """
    if not expected_hash:
        return False
    return await to_thread.run_sync(verify_password_sync, plaintext, expected_hash)


def dummy_hash() -> str:
    """A real hash that no password matches, made once with the current parameters.

    What an unknown email is checked against. Refusing one without hashing
    would make a wrong address measurably quicker to reject than a wrong
    password, and that difference is how an attacker learns which addresses
    have accounts here. Made lazily rather than at import so that it is
    hashed with whatever parameters are in force when first needed — the
    tests swap in cheaper ones after import — and so the module costs
    nothing to import.
    """
    global _dummy_hash
    if _dummy_hash is None:
        # Random, so the one thing it could never be is a password somebody
        # chose; checked against, never matched.
        _dummy_hash = _hasher.hash(secrets.token_urlsafe(32))
    return _dummy_hash


def needs_rehash(expected_hash: str) -> bool:
    """True when the hash was made with weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(expected_hash)
    except (Argon2Error, InvalidHashError):
        return False
