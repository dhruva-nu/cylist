"""The vault: credentials, and who is allowed to see them.

Scopes here are deliberately narrower than elsewhere in Cylist.

* Reading a tree's shape, and an entry's username, URL and notes, needs
  ``vault:read``.
* Changing anything needs ``write`` **and** ``vault:read`` — a mutation echoes
  the entry back, and a token handed plain ``write`` to shuffle cards on the
  board has no business reaching into the credential store at all.
* ``POST /vault/nodes/{id}/reveal`` is the only endpoint anywhere that returns
  a plaintext secret. It needs ``vault:reveal``, and every call is written to
  the audit trail.

No other response model on this router can carry a secret value, so a token
without ``vault:reveal`` cannot obtain one however it asks.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core import crypto
from app.core.clock import now
from app.core.crypto import VaultCipher
from app.db import get_session
from app.models.project import Project
from app.models.vault import VaultNode, VaultTree
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.vault import (
    SecretRead,
    SecretRevealed,
    VaultNodeCreate,
    VaultNodeMove,
    VaultNodeRead,
    VaultNodeUpdate,
    VaultTreeCreate,
    VaultTreeDetail,
    VaultTreeRead,
    VaultTreeUpdate,
)
from app.services import activity, vault

READ = require(Scope.VAULT_READ)
WRITE = require(Scope.WRITE, Scope.VAULT_READ)
REVEAL = require(Scope.VAULT_REVEAL)

project_router = APIRouter(prefix="/projects", tags=["vault"])
router = APIRouter(prefix="/vault", tags=["vault"])


def vault_cipher(settings: Settings = Depends(app_settings)) -> VaultCipher:
    """The cipher this deployment's ``CYLIST_VAULT_KEY`` configures.

    Built on demand rather than at startup, so a deployment that has not set a
    key still boots and still serves everything outside the vault — and fails
    loudly, here, the moment a credential is involved.
    """
    return crypto.cipher_for(settings.vault_key)


async def resolved_tree(tree_id: UUID, session: AsyncSession = Depends(get_session)) -> VaultTree:
    """Turn the path segment into a tree, 404-ing if nothing matches."""
    return await vault.get_tree(session, tree_id)


async def resolved_node(node_id: UUID, session: AsyncSession = Depends(get_session)) -> VaultNode:
    """Turn the path segment into a node, 404-ing if nothing matches."""
    return await vault.get_node(session, node_id)


# --- Trees -----------------------------------------------------------------


@project_router.get(
    "/{project_ref}/vault/trees",
    response_model=list[VaultTreeRead],
    tags=["vault"],
    summary="List a project's vault trees",
)
async def list_trees(
    _: Principal = Depends(READ),
    project: Project = Depends(resolved_project),
    session: AsyncSession = Depends(get_session),
) -> list[VaultTreeRead]:
    """The trees down the left of the vault screen, with their headline counts."""
    trees = await vault.list_trees(session, project)
    sizes = await vault.tree_sizes(session, [tree.id for tree in trees])
    return [_tree_read(tree, sizes[tree.id]) for tree in trees]


@project_router.post(
    "/{project_ref}/vault/trees",
    response_model=VaultTreeRead,
    status_code=status.HTTP_201_CREATED,
    tags=["vault"],
    summary="Start a vault tree",
    responses={409: {"description": "This project already has a tree with that name."}},
)
async def create_tree(
    body: VaultTreeCreate,
    principal: Principal = Depends(WRITE),
    project: Project = Depends(resolved_project),
    session: AsyncSession = Depends(get_session),
) -> VaultTreeRead:
    """Add a tree — "Logins", "Certificates & keys", whatever the project needs."""
    tree = await vault.create_tree(session, project, body)
    await activity.record(
        session,
        principal,
        "vault.tree_created",
        entity_type="vault_tree",
        entity_id=tree.id,
        project_id=project.id,
        payload={"name": tree.name},
    )
    return await _sized(session, tree)


@router.get(
    "/trees/{tree_id}",
    response_model=VaultTreeDetail,
    summary="Get a whole vault tree",
)
async def get_tree(
    _: Principal = Depends(READ),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = Depends(get_session),
) -> VaultTreeDetail:
    """Every node in the tree, nested, with each secret's metadata.

    No secret value appears here at any depth. Call
    `POST /vault/nodes/{id}/reveal` for one of those, one at a time.
    """
    nodes = await vault.nodes_in_tree(session, tree.id)
    size = vault.TreeSize(nodes=len(nodes), secrets=sum(1 for node in nodes if node.is_secret))
    return VaultTreeDetail(
        **_tree_read(tree, size).model_dump(),
        nodes=_nest(nodes, parent_id=None),
    )


@router.patch(
    "/trees/{tree_id}",
    response_model=VaultTreeRead,
    summary="Rename or reorder a vault tree",
    responses={409: {"description": "This project already has a tree with that name."}},
)
async def update_tree(
    body: VaultTreeUpdate,
    principal: Principal = Depends(WRITE),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = Depends(get_session),
) -> VaultTreeRead:
    updated = await vault.update_tree(session, tree, body)
    await activity.record(
        session,
        principal,
        "vault.tree_updated",
        entity_type="vault_tree",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"name": updated.name, "fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return await _sized(session, updated)


@router.delete("/trees/{tree_id}", response_model=Acknowledged, summary="Delete a vault tree")
async def delete_tree(
    principal: Principal = Depends(WRITE),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = Depends(get_session),
) -> Acknowledged:
    """Delete a tree and every credential in it. There is no undo."""
    name, project_id, tree_id = tree.name, tree.project_id, tree.id
    await vault.delete_tree(session, tree)
    await activity.record(
        session,
        principal,
        "vault.tree_deleted",
        entity_type="vault_tree",
        entity_id=tree_id,
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()


# --- Nodes -----------------------------------------------------------------


@router.post(
    "/nodes",
    response_model=VaultNodeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a branch or a secret",
    responses={
        409: {"description": "A sibling already has that name."},
        422: {"description": "The parent is a secret, or is in another tree."},
    },
)
async def create_node(
    body: VaultNodeCreate,
    principal: Principal = Depends(WRITE),
    session: AsyncSession = Depends(get_session),
    cipher: VaultCipher = Depends(vault_cipher),
) -> VaultNodeRead:
    """Create a node.

    A `branch` groups other nodes. A `secret` carries one credential, given as
    `secret.value`; it is encrypted before it is stored and the response does
    not contain it.
    """
    node = await vault.create_node(session, cipher, body)
    tree = await vault.get_tree(session, node.tree_id)
    await activity.record(
        session,
        principal,
        "vault.node_created",
        entity_type="vault_node",
        entity_id=node.id,
        project_id=tree.project_id,
        # The name and kind, never the credential: the audit log is readable
        # with `read` alone.
        payload={"name": node.name, "kind": node.kind.value, "tree": tree.name},
    )
    return _node_read(node)


@router.get("/nodes/{node_id}", response_model=VaultNodeRead, summary="Get a node")
async def get_node(
    _: Principal = Depends(READ),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = Depends(get_session),
) -> VaultNodeRead:
    """One node and its subtree — metadata only, never a secret value."""
    nodes = await vault.nodes_in_tree(session, node.tree_id)
    return _node_read(node, children=_nest(nodes, parent_id=node.id))


@router.patch(
    "/nodes/{node_id}",
    response_model=VaultNodeRead,
    summary="Rename a node, or amend its secret",
    responses={409: {"description": "A sibling already has that name."}},
)
async def update_node(
    body: VaultNodeUpdate,
    principal: Principal = Depends(WRITE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = Depends(get_session),
    cipher: VaultCipher = Depends(vault_cipher),
) -> VaultNodeRead:
    """Change any subset of a node. Omitted fields are left alone.

    Supplying `secret.value` re-encrypts under a fresh nonce; omitting it
    leaves the stored credential untouched, so a username or a note can be
    corrected without knowing it.
    """
    updated = await vault.update_node(session, cipher, node, body)
    tree = await vault.get_tree(session, updated.tree_id)
    changed = sorted(body.model_dump(exclude_unset=True))
    await activity.record(
        session,
        principal,
        "vault.node_updated",
        entity_type="vault_node",
        entity_id=updated.id,
        project_id=tree.project_id,
        payload={"name": updated.name, "fields": changed},
    )
    return _node_read(updated)


@router.delete("/nodes/{node_id}", response_model=Acknowledged, summary="Delete a node")
async def delete_node(
    principal: Principal = Depends(WRITE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = Depends(get_session),
) -> Acknowledged:
    """Delete a node. A branch takes its whole subtree with it."""
    tree = await vault.get_tree(session, node.tree_id)
    name, kind, node_id = node.name, node.kind.value, node.id
    await vault.delete_node(session, node)
    await activity.record(
        session,
        principal,
        "vault.node_deleted",
        entity_type="vault_node",
        entity_id=node_id,
        project_id=tree.project_id,
        payload={"name": name, "kind": kind, "tree": tree.name},
    )
    return Acknowledged()


@router.post(
    "/nodes/{node_id}/move",
    response_model=VaultNodeRead,
    summary="Move a node",
    responses={
        409: {"description": "Something of that name is already there."},
        422: {"description": "The destination is inside the node being moved."},
    },
)
async def move_node(
    body: VaultNodeMove,
    principal: Principal = Depends(WRITE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = Depends(get_session),
) -> VaultNodeRead:
    """Re-parent and reposition a node within its tree.

    `parent_id` is always the destination: `null` means the top level, not
    "leave it where it is". Moving a branch into its own subtree is refused —
    it would detach that subtree from the tree entirely.
    """
    moved = await vault.move_node(session, node, body)
    tree = await vault.get_tree(session, moved.tree_id)
    await activity.record(
        session,
        principal,
        "vault.node_moved",
        entity_type="vault_node",
        entity_id=moved.id,
        project_id=tree.project_id,
        payload={
            "name": moved.name,
            "parent_id": str(moved.parent_id) if moved.parent_id else None,
            "position": moved.position,
        },
    )
    return _node_read(moved)


@router.post(
    "/nodes/{node_id}/reveal",
    response_model=SecretRevealed,
    summary="Reveal a stored secret",
    responses={
        403: {"description": "This token does not hold `vault:reveal`."},
        422: {"description": "That node is a branch, so it holds no credential."},
    },
)
async def reveal_secret(
    principal: Principal = Depends(REVEAL),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = Depends(get_session),
    cipher: VaultCipher = Depends(vault_cipher),
) -> SecretRevealed:
    """Decrypt one credential and return it.

    The only endpoint in Cylist that does. `POST` rather than `GET` on purpose:
    revealing a credential is an event with a consequence, it must not be
    cached by a proxy, and it must not sit in a browser's history or a server
    access log as a URL anyone can replay.

    Every call is written to the activity feed naming the node, so the log
    answers "who looked at the Twilio password, and when?". The plaintext
    itself is never recorded.
    """
    tree = await vault.get_tree(session, node.tree_id)
    value = await vault.reveal(session, cipher, node)
    await activity.record(
        session,
        principal,
        "vault.secret_revealed",
        entity_type="vault_node",
        entity_id=node.id,
        project_id=tree.project_id,
        payload={"name": node.name, "tree": tree.name},
    )
    return SecretRevealed(node_id=node.id, name=node.name, value=value, revealed_at=now())


# --- Presentation ----------------------------------------------------------


def _tree_read(tree: VaultTree, size: vault.TreeSize) -> VaultTreeRead:
    return VaultTreeRead(
        id=tree.id,
        project_id=tree.project_id,
        name=tree.name,
        position=tree.position,
        node_count=size.nodes,
        secret_count=size.secrets,
        created_at=tree.created_at,
        updated_at=tree.updated_at,
    )


async def _sized(session: AsyncSession, tree: VaultTree) -> VaultTreeRead:
    """One tree with its counts, for the endpoints that return exactly one."""
    sizes = await vault.tree_sizes(session, [tree.id])
    return _tree_read(tree, sizes[tree.id])


def _node_read(node: VaultNode, *, children: list[VaultNodeRead] | None = None) -> VaultNodeRead:
    return VaultNodeRead(
        id=node.id,
        tree_id=node.tree_id,
        parent_id=node.parent_id,
        name=node.name,
        kind=node.kind,
        position=node.position,
        created_at=node.created_at,
        updated_at=node.updated_at,
        secret=SecretRead.model_validate(node.secret) if node.secret is not None else None,
        children=children or [],
    )


def _nest(nodes: list[VaultNode], *, parent_id: UUID | None) -> list[VaultNodeRead]:
    """Turn a flat, ordered node list into the nesting the client draws.

    Done in one pass over an index rather than a query per level, so the depth
    of a tree costs nothing.
    """
    by_parent: dict[UUID | None, list[VaultNode]] = {}
    for node in nodes:
        by_parent.setdefault(node.parent_id, []).append(node)

    def build(current: UUID | None) -> list[VaultNodeRead]:
        return [_node_read(child, children=build(child.id)) for child in by_parent.get(current, [])]

    return build(parent_id)
