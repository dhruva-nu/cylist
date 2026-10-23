"""The commands the vault services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.vault``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.sensitivity import Sensitivity
from app.models.vault import VaultNodeKind
from app.services.commands.base import Command


class VaultTreeCreate(Command):
    name: str = Field(min_length=1, max_length=120, examples=["Logins"])
    default_sensitivity: Sensitivity | None = Field(
        default=None,
        description=(
            "What nodes added to this tree are classified as unless the caller "
            "says. A tree is how a vault is already divided, so this is where "
            '"everything in Certificates is restricted" is said once.'
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class VaultTreeUpdate(Command):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0)
    default_sensitivity: Sensitivity | None = None


class SecretCreate(Command):
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


class SecretUpdate(Command):
    """A partial update to a secret. Omitted fields keep their current value.

    Omitting ``value`` leaves the stored ciphertext untouched, so the username
    or a note can be corrected without the caller having to know the
    credential.
    """

    value: str | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, max_length=254)
    url: str | None = None
    notes: str | None = None


class VaultNodeCreate(Command):
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
    sensitivity: Sensitivity | None = Field(
        default=None,
        description=(
            "How far this may travel. Omit to inherit the parent branch's, or "
            "the tree's default at the top level."
        ),
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


class VaultNodeUpdate(Command):
    """Every field optional; omitted fields are left as they are.

    ``kind`` is deliberately absent. Turning a secret into a branch would have
    to decide what happens to the credential underneath it, and "silently
    discard it" is not an answer worth shipping — delete the node instead.
    """

    name: str | None = Field(default=None, min_length=1, max_length=160)
    secret: SecretUpdate | None = None
    sensitivity: Sensitivity | None = Field(
        default=None,
        description=(
            "Reclassify it. Raising a branch's level hides everything under it "
            "from anybody not cleared that far."
        ),
    )


class VaultNodeMove(Command):
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
