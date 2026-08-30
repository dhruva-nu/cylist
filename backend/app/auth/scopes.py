"""Permission scopes.

Scopes exist so that an agent can be handed exactly the authority it needs. A
bot that shuffles cards on the board gets ``write``; nothing forces you to also
give it the ability to read plaintext credentials out of the vault.
"""

from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    READ = "read"
    """Read every project, board, file listing and vault *structure*."""

    WRITE = "write"
    """Create and modify projects, people, tasks, files and vault nodes."""

    VAULT_READ = "vault:read"
    """Read vault entries' metadata: username, URL, notes — never the secret."""

    VAULT_REVEAL = "vault:reveal"
    """Decrypt and return a stored secret. Every use is written to the audit log."""

    ADMIN = "admin"
    """Manage API tokens."""


ALL_SCOPES: frozenset[Scope] = frozenset(Scope)
"""Everything, granted to a browser session logged in as the owner."""


def parse_scopes(values: list[str]) -> frozenset[Scope]:
    """Convert stored scope strings to :class:`Scope`, dropping unknown values.

    Unknown strings are ignored rather than raising: a token issued by a newer
    version of Cylist must not lock the current one out of authenticating.
    """
    known = {scope.value for scope in Scope}
    return frozenset(Scope(value) for value in values if value in known)
