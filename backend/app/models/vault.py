"""The vault: trees of branches and secrets.

A project has several trees — "Logins", "Links", "Certificates & keys" — and
each is an adjacency list of nodes nested as deeply as you like. A ``branch``
holds other nodes; a ``secret`` holds one credential and nothing under it.

**Only the secret value is encrypted.** Username, URL and notes are stored as
plaintext on purpose: they are what you search the vault *by* ("which login
was the SendGrid one?"), and no index is possible over ciphertext. The
trade-off is deliberate and worth being explicit about — anyone who reaches
the database learns which accounts exist and who they belong to, but not a
single credential.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VaultNodeKind(StrEnum):
    """What a node is."""

    BRANCH = "branch"
    """A folder. Holds other nodes, never a credential."""

    SECRET = "secret"  # noqa: S105 - the name of a node kind, not a credential
    """A leaf. Holds exactly one credential, and nothing beneath it."""


class VaultTree(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One top-level grouping of credentials within a project."""

    __tablename__ = "vault_tree"
    __table_args__ = (
        Index("ix_vault_tree_project_id_position", "project_id", "position"),
        UniqueConstraint("project_id", "name"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class VaultNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A branch or a secret, somewhere in a tree."""

    __tablename__ = "vault_node"
    __table_args__ = (
        # Two siblings cannot share a name. NULLS NOT DISTINCT is load-bearing:
        # without it Postgres treats every NULL parent_id as unique, so the
        # constraint would silently do nothing for top-level nodes — which is
        # where duplicates are most likely.
        UniqueConstraint("tree_id", "parent_id", "name", postgresql_nulls_not_distinct=True),
        # Redundant on its own — id is already the primary key — but a foreign
        # key needs a unique constraint over exactly the columns it references,
        # and `vault_secret` references (id, kind). See VaultSecret.
        UniqueConstraint("id", "kind"),
        Index("ix_vault_node_tree_id_parent_id_position", "tree_id", "parent_id", "position"),
    )

    tree_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("vault_tree.id", ondelete="CASCADE"),
        nullable=False,
    )

    parent_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("vault_node.id", ondelete="CASCADE"),
    )
    """NULL for a node sitting directly under the tree. Cascading means
    deleting a branch takes its whole subtree — and every secret in it — with
    it, in one statement rather than a recursive walk that could stop half
    way."""

    name: Mapped[str] = mapped_column(String(160), nullable=False)

    kind: Mapped[VaultNodeKind] = mapped_column(
        Enum(
            VaultNodeKind,
            name="vault_node_kind",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    secret: Mapped[VaultSecret | None] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="selectin",  # one extra query per fetch, never N+1
    )

    @property
    def is_secret(self) -> bool:
        return self.kind is VaultNodeKind.SECRET


class VaultSecret(Base, TimestampMixin):
    """The contents of one ``secret`` node.

    Split from :class:`VaultNode` so that listing a tree — which happens on
    every page load — never has to touch a ciphertext column at all.
    """

    __tablename__ = "vault_secret"
    __table_args__ = (
        CheckConstraint("node_kind = 'secret'", name="node_kind_is_secret"),
        # The pair of constraints above and below is what stops a secret from
        # ever hanging off a branch. `node_kind` mirrors the node's own kind so
        # that this foreign key has something to check it against; the CHECK
        # pins that mirror to 'secret'. Together they also freeze a node's kind
        # for as long as it holds a credential, since the update would break
        # the reference.
        ForeignKeyConstraint(
            ["node_id", "node_kind"],
            ["vault_node.id", "vault_node.kind"],
            ondelete="CASCADE",
        ),
    )

    node_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    """Primary key as well as foreign key: one secret per node, at most."""

    node_kind: Mapped[VaultNodeKind] = mapped_column(
        Enum(
            VaultNodeKind,
            name="vault_node_kind",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=VaultNodeKind.SECRET,
    )

    username: Mapped[str | None] = mapped_column(String(254))
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """Nonce ‖ ciphertext ‖ tag, as produced by :meth:`app.core.crypto.VaultCipher.seal`.
    The only field here that is not readable straight off the disk."""

    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    """Which ``CYLIST_VAULT_KEY`` sealed this row, so the key can be rotated."""

    node: Mapped[VaultNode] = relationship(back_populates="secret")
