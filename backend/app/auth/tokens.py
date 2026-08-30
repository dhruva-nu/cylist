"""Bearer token minting and hashing.

A token is 32 bytes from ``secrets``, base64url-encoded, behind a ``cyl_``
prefix that makes it recognisable in a log or a secret scanner.

Tokens are stored as an unsalted SHA-256 digest, not as an Argon2 hash. That is
deliberate: the token is 256 bits of uniform randomness, so there is no
dictionary to attack and no benefit to a slow KDF — while a slow KDF *would*
cost one expensive hash on every authenticated request. Passwords, which are
low-entropy, use Argon2id instead (see :mod:`app.auth.passwords`).
"""

from __future__ import annotations

import base64
import hashlib
import secrets

TOKEN_PREFIX = "cyl_"  # noqa: S105 - an identifying prefix, not a secret
_TOKEN_BYTES = 32


def generate_token() -> tuple[str, str]:
    """Mint a token, returning ``(plaintext, digest)``.

    The plaintext is shown to the caller once and never persisted.
    """
    raw = secrets.token_bytes(_TOKEN_BYTES)
    plaintext = TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return plaintext, hash_token(plaintext)


def hash_token(plaintext: str) -> str:
    """Return the hex SHA-256 digest used to look a token up."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def looks_like_token(value: str) -> bool:
    """Cheap shape check, so obviously wrong input never reaches the database."""
    return value.startswith(TOKEN_PREFIX) and len(value) > len(TOKEN_PREFIX)
