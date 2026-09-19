"""Password hashing.

Argon2id via ``argon2-cffi`` with the library's current defaults, which follow
the OWASP recommendation.

A password belongs to a person and lives in ``person.password_hash``. It is
consulted at ``POST /auth/login`` and nowhere else; everything after that
carries a token instead, which is why this module can afford a deliberately
slow hash.

``CYLIST_PASSWORD_HASH`` survives as the bootstrap credential for a deployment
with no accounts in it yet — the one login that is checked against a setting
rather than a row. See :func:`app.routers.auth.login`.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash, including its parameters and salt."""
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, expected_hash: str | None) -> bool:
    """Check a password against a stored hash.

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


def needs_rehash(expected_hash: str) -> bool:
    """True when the hash was made with weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(expected_hash)
    except (Argon2Error, InvalidHashError):
        return False
