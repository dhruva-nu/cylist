"""Moving every sealed secret onto the newest vault key.

The second half of a key rotation (:mod:`app.core.crypto` has the first). Once
a newer key is in the keyring, everything written from then on is sealed with
it, but every row written before still names the old version and needs the old
key to open. :func:`reseal` walks both tables that hold ciphertext —
``vault_secret`` and ``hook`` — and re-encrypts each such row under the newest
key, so that the old one can be taken out of the configuration.

Run by an operator, through ``python -m app.cli vault-reseal``, while the
application keeps serving. That shapes how it is written:

* **In batches, each its own transaction**, so a vault of thousands of rows is
  never one long lock, and an interrupted run has kept everything it finished.
  Running it again picks up where it stopped: a row already on the newest key
  is not selected again.
* **Each batch locks the rows it rewrites** (``FOR UPDATE``), so a person
  changing a secret at the same moment either lands first, and their new value
  is what gets resealed, or waits for the batch and writes over it — never a
  resealed copy of the value they just replaced.
* **The binding stays as it was.** A row is opened and sealed against the same
  node or hook id and the same domain; only the key and the version stored
  beside it change.
* **``updated_at`` does not move.** Nobody changed the credential, and a page
  that says it was edited today would be telling them somebody did.
* **A row that will not open is reported, not fatal.** It is already unreadable
  — tampered with, or sealed under a key this deployment no longer holds — and
  resealing it is impossible; the run carries on with the rest and names it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import InstrumentedAttribute

from app.core.crypto import HOOK_SECRET_DOMAIN, VAULT_DOMAIN, SecretUnreadableError, VaultCipher
from app.db import Database
from app.models.hook import Hook
from app.models.vault import VaultSecret

BATCH = 200
"""Rows per transaction. Small enough that no batch holds its locks for long,
large enough that a vault of a few thousand secrets is a handful of round trips."""


@dataclass(frozen=True)
class _Sealed:
    """One table that holds ciphertext, and how its rows are bound."""

    name: str
    model: type[Any]
    row_id: InstrumentedAttribute[UUID]
    ciphertext: InstrumentedAttribute[bytes]
    key_version: InstrumentedAttribute[int]
    updated_at: InstrumentedAttribute[Any]
    domain: str


SEALED = (
    _Sealed(
        "vault_secret",
        VaultSecret,
        VaultSecret.node_id,
        VaultSecret.secret_ciphertext,
        VaultSecret.key_version,
        VaultSecret.updated_at,
        VAULT_DOMAIN,
    ),
    _Sealed(
        "hook",
        Hook,
        Hook.id,
        Hook.secret_ciphertext,
        Hook.key_version,
        Hook.updated_at,
        HOOK_SECRET_DOMAIN,
    ),
)
"""Every table with a ``key_version`` column. A new one belongs here, or a
rotation would leave it behind on the old key."""


@dataclass(frozen=True)
class Unreadable:
    """A row :func:`reseal` could not open, so left as it was."""

    table: str
    row_id: UUID
    key_version: int
    reason: str


@dataclass
class ResealReport:
    """What a run did, and what is left on each key version afterwards."""

    version: int
    """The key everything was resealed under."""
    held: tuple[int, ...]
    """Every version the cipher held during the run."""
    resealed: dict[str, int] = field(default_factory=dict)
    """Rows rewritten, by table."""
    unreadable: list[Unreadable] = field(default_factory=list)
    remaining: dict[int, int] = field(default_factory=dict)
    """Rows on each key version across every table, counted after the run."""

    @property
    def retirable(self) -> tuple[int, ...]:
        """Key versions no row needs any more: safe to remove from configuration."""
        return tuple(version for version in self.held if version not in self.remaining)


async def reseal(database: Database, cipher: VaultCipher, *, batch: int = BATCH) -> ResealReport:
    """Re-encrypt every row not yet on ``cipher``'s newest key, under it."""
    report = ResealReport(version=cipher.version, held=cipher.versions)
    for sealed in SEALED:
        report.resealed[sealed.name] = await _reseal_table(
            database, cipher, sealed, batch, report.unreadable
        )
    report.remaining = await versions_in_use(database)
    return report


async def versions_in_use(database: Database) -> dict[int, int]:
    """How many sealed rows each key version still has to open, across every table."""
    counts: Counter[int] = Counter()
    async with database.session() as session:
        for sealed in SEALED:
            rows = await session.execute(
                select(sealed.key_version, func.count()).group_by(sealed.key_version)
            )
            for version, count in rows.tuples():
                counts[version] += count
    return dict(sorted(counts.items()))


async def _reseal_table(
    database: Database,
    cipher: VaultCipher,
    sealed: _Sealed,
    batch: int,
    unreadable: list[Unreadable],
) -> int:
    resealed = 0
    # Keyset pagination on the primary key rather than "whatever is still on
    # an old version": a row that will not open stays on its old version, and
    # without a cursor moving past it every batch would select it again.
    after: UUID | None = None
    while True:
        async with database.session() as session:
            statement = (
                select(sealed.row_id, sealed.ciphertext, sealed.key_version)
                .where(sealed.key_version != cipher.version)
                .order_by(sealed.row_id)
                .limit(batch)
                .with_for_update()
            )
            if after is not None:
                statement = statement.where(sealed.row_id > after)
            rows = (await session.execute(statement)).tuples().all()

            for row_id, ciphertext, key_version in rows:
                try:
                    plaintext = cipher.open(
                        ciphertext, node_id=row_id, key_version=key_version, domain=sealed.domain
                    )
                except SecretUnreadableError as exc:
                    unreadable.append(Unreadable(sealed.name, row_id, key_version, exc.message))
                    continue
                await session.execute(
                    update(sealed.model)
                    .where(sealed.row_id == row_id)
                    .values(
                        {
                            sealed.ciphertext: cipher.seal(
                                plaintext, node_id=row_id, domain=sealed.domain
                            ),
                            sealed.key_version: cipher.version,
                            # Named, so its onupdate default does not fire: see
                            # the module docstring.
                            sealed.updated_at: sealed.updated_at,
                        }
                    )
                    .execution_options(synchronize_session=False)
                )
                resealed += 1

        if len(rows) < batch:
            return resealed
        after = rows[-1][0]
