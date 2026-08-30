"""The vault.

Note what is *not* here: no response model on this page carries a plaintext
secret. The only shape that does is :class:`SecretRevealed`, returned by the
one endpoint that requires ``vault:reveal``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.vault import VaultNodeKind
from app.schemas.common import Schema


class VaultTreeCreate(Schema):
    name: str = Field(min_length=1, max_length=120, examples=["Logins"])

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class VaultTreeUpdate(Schema):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0)


class SecretCreate(Schema):
    """The contents of a new secret node."""

    value: str = Field(
        min_length=1,
        description=(
            "The credential itself. Encrypted before it is stored, and returned "
            "by no endpoint except `POST /vault/nodes/{id}/reveal`."
        ),
    )
    username: str | None = Field(default=None, max_length=254)
    url: str | None = None
    notes: str = ""


class SecretUpdate(Schema):
    """A partial update to a secret. Omitted fields keep their current value.

    Omitting ``value`` leaves the stored ciphertext untouched, so the username
    or a note can be corrected without the caller having to know the
    credential.
    """

    value: str | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, max_length=254)
    url: str | None = None
    notes: str | None = None


class VaultNodeCreate(Schema):
    tree_id: UUID
    parent_id: UUID | None = Field(
        default=None, description="The branch to create this under. Omit for the top level."
    )
    name: str = Field(min_length=1, max_length=160)
    kind: VaultNodeKind = Field(
        description="`branch` holds other nodes; `secret` holds one credential."
    )
    secret: SecretCreate | None = Field(
        default=None, description="Required when `kind` is `secret`, forbidden otherwise."
    )

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _secret_matches_kind(self) -> VaultNodeCreate:
        """A branch has no credential; a secret is nothing without one."""
        if self.kind is VaultNodeKind.SECRET and self.secret is None:
            raise ValueError("a node of kind 'secret' needs a secret")
        if self.kind is VaultNodeKind.BRANCH and self.secret is not None:
            raise ValueError("a node of kind 'branch' cannot hold a secret")
        return self


class VaultNodeUpdate(Schema):
    """Every field optional; omitted fields are left as they are.

    ``kind`` is deliberately absent. Turning a secret into a branch would have
    to decide what happens to the credential underneath it, and "silently
    discard it" is not an answer worth shipping — delete the node instead.
    """

    name: str | None = Field(default=None, min_length=1, max_length=160)
    secret: SecretUpdate | None = None


class VaultNodeMove(Schema):
    """Where a node should end up."""

    parent_id: UUID | None = Field(
        default=None,
        description=(
            "The branch to move under, within the same tree. `null` moves the "
            "node to the top level; this field is always the new parent, never "
            "'leave it where it is'."
        ),
    )
    position: int = Field(default=0, ge=0, description="Index among its new siblings.")


class SecretRead(Schema):
    """A secret's metadata — everything about it except the credential."""

    username: str | None
    url: str | None
    notes: str
    key_version: int
    updated_at: datetime


class VaultNodeRead(Schema):
    id: UUID
    tree_id: UUID
    parent_id: UUID | None
    name: str
    kind: VaultNodeKind
    position: int
    created_at: datetime
    updated_at: datetime
    secret: SecretRead | None = Field(
        default=None, description="Present on a `secret` node. Never includes the value."
    )
    children: list[VaultNodeRead] = Field(
        default_factory=list, description="The subtree beneath this node, in position order."
    )


class VaultTreeRead(Schema):
    id: UUID
    project_id: UUID
    name: str
    position: int
    node_count: int
    secret_count: int
    created_at: datetime
    updated_at: datetime


class VaultTreeDetail(VaultTreeRead):
    """A tree with everything in it — and not one secret value."""

    nodes: list[VaultNodeRead] = Field(description="Top-level nodes, each nesting its own.")


class SecretRevealed(Schema):
    """The only shape in Cylist that carries a plaintext credential."""

    node_id: UUID
    name: str
    value: str = Field(description="The decrypted secret. This request has been logged.")
    revealed_at: datetime
