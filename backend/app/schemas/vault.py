"""The vault.

Note what is *not* here: no response model on this page carries a plaintext
secret. The only shape that does is :class:`SecretRevealed`, returned by the
one endpoint that requires ``vault:reveal``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.sensitivity import Sensitivity
from app.models.vault import VaultNodeKind
from app.schemas.common import Page, Schema
from app.services.commands.vault import (
    SecretCreate as SecretCreate,
)
from app.services.commands.vault import (
    SecretUpdate as SecretUpdate,
)
from app.services.commands.vault import (
    VaultNodeCreate as VaultNodeCreate,
)
from app.services.commands.vault import (
    VaultNodeMove as VaultNodeMove,
)
from app.services.commands.vault import (
    VaultNodeUpdate as VaultNodeUpdate,
)
from app.services.commands.vault import (
    VaultTreeCreate as VaultTreeCreate,
)
from app.services.commands.vault import (
    VaultTreeUpdate as VaultTreeUpdate,
)


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
    sensitivity: Sensitivity = Field(
        description="How far this may travel. A role not cleared this high is not shown it."
    )
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
    default_sensitivity: Sensitivity = Field(
        description="What nodes added here are classified as unless the caller says."
    )
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


class VaultTreePage(Page[VaultTreeRead]):
    """A page of a project's vault trees, in display order."""
