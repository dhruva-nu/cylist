"""Owner password hashing.

Argon2id via ``argon2-cffi`` with the library's current defaults, which follow
the OWASP recommendation. There is exactly one password in Cylist — the
owner's — and it is only ever consulted at ``POST /auth/login``.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash, including its parameters and salt."""
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, expected_hash: str) -> bool:
    """Check a password against a stored hash.

    Returns ``False`` for a mismatch and for a malformed or empty hash, so a
    deployment that has not set ``CYLIST_PASSWORD_HASH`` rejects every login
    instead of accepting every login.
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
