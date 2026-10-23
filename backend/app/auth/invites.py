"""Invitation tokens.

An invitation is a one-time secret that lets somebody who has never signed in
set their first password. It is the same construction as a bearer token — 32
random bytes, stored as a SHA-256 digest, shown once — and for the same
reasons, which is why this module is three lines of reuse rather than three
lines of its own crypto.

What it does not share is the prefix. ``cylinv_`` rather than ``cyl_`` so the
two can never be confused by a person reading a log, by a secret scanner, or
by :func:`app.auth.tokens.looks_like_token`, which would otherwise wave an
invitation through to a database lookup it can never satisfy.

The digest is what the server keeps, so the invitation in somebody's mailbox
is the only copy. Accepting it clears the digest, which is what makes the link
single-use: a second attempt has nothing left to match.
"""

from __future__ import annotations

import base64
import secrets

from app.auth.tokens import hash_token

INVITE_PREFIX = "cylinv_"
_INVITE_BYTES = 32


def generate_invite() -> tuple[str, str]:
    """Mint an invitation, returning ``(plaintext, digest)``."""
    raw = secrets.token_bytes(_INVITE_BYTES)
    plaintext = INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return plaintext, hash_token(plaintext)


def looks_like_invite(value: str) -> bool:
    """Cheap shape check, so obviously wrong input never reaches the database."""
    return value.startswith(INVITE_PREFIX) and len(value) > len(INVITE_PREFIX)
