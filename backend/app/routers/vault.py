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

from app.auth.dependencies import current_principal, require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core import crypto
from app.core import sensitivity as sensitivity_of
from app.core.clock import now
from app.core.crypto import VaultCipher
from app.core.sensitivity import DEFAULT_LEVEL, Sensitivity
from app.db import SessionDependency
from app.models.project import Project
from app.models.vault import VaultNode, VaultTree
from app.routers import guards
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
from app.services import activity, permissions, vault

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


async def resolved_tree(tree_id: UUID, session: AsyncSession = SessionDependency) -> VaultTree:
    """Turn the path segment into a tree, 404-ing if nothing matches."""
    return await vault.get_tree(session, tree_id)


async def resolved_node(node_id: UUID, session: AsyncSession = SessionDependency) -> VaultNode:
    """Turn the path segment into a node, 404-ing if nothing matches."""
    return await vault.get_node(session, node_id)


# --- Trees -----------------------------------------------------------------


async def _project_of_node(session: AsyncSession, node: VaultNode) -> UUID:
    """Which project a node is in, by way of its tree. A node knows only the
    tree; the tree is what belongs to a project."""
    tree = await vault.get_tree(session, node.tree_id)
    return tree.project_id


def _cleared_nodes(nodes: list[VaultNode], allowed: frozenset[Sensitivity]) -> list[VaultNode]:
    """Drop the nodes this caller is not cleared for, and their subtrees.

    A branch above the clearance takes everything under it, whatever those
    children are classified as. A tree that showed "Production" standing empty
    would have told the reader which branch is the interesting one, which is
    most of what there was to learn.
    """
    hidden: set[UUID] = set()
    kept: list[VaultNode] = []
    # `nodes_in_tree` returns them parent-before-child, so one pass is enough
    # to carry a hidden branch down onto everything beneath it.
    for node in nodes:
        if node.parent_id in hidden or not permissions.readable(node.sensitivity, allowed):
            hidden.add(node.id)
            continue
        kept.append(node)
    return kept


async def visible_node(
    node: VaultNode = Depends(resolved_node),
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> VaultNode:
    """The node, if this caller is cleared to know it exists — else a 404.

    Walks up to the tree's root, because a secret inside a restricted branch
    is restricted whatever it says about itself: hiding the branch and serving
    its children by id would be a lock on the door and a window beside it.

    No scope check of its own — every endpoint below states the one it wants,
    and this runs inside their guards as well as beside them.
    """
    project_id = await _project_of_node(session, node)
    allowed = await permissions.visible_levels_for(session, project_id, principal)

    everything = {row.id: row for row in await vault.nodes_in_tree(session, node.tree_id)}
    walking: VaultNode | None = node
    while walking is not None:
        permissions.enforce_visible(walking.sensitivity, allowed, "node")
        walking = everything.get(walking.parent_id) if walking.parent_id else None
    return node


CHANGE_VAULT = guards.on_project(Permission.VAULT, Scope.WRITE, Scope.VAULT_READ)
CHANGE_THIS_TREE = guards.for_entity(Permission.VAULT, resolved_tree, Scope.WRITE, Scope.VAULT_READ)
CHANGE_THIS_NODE = guards.for_entity(
    Permission.VAULT, visible_node, Scope.WRITE, Scope.VAULT_READ, locate=_project_of_node
)
REVEAL_THIS_SECRET = guards.for_entity(
    Permission.VAULT_REVEAL, visible_node, Scope.VAULT_REVEAL, locate=_project_of_node
)
"""Both gates, and they are a pair worth reading together. ``vault:reveal`` is
what the credential carries; the permission is what this person is on this
board. A token minted to reveal secrets still reveals none on a project whose
role for its owner does not allow it."""


async def may_add_node(
    body: VaultNodeCreate,
    principal: Principal = Depends(WRITE),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """The guard for ``POST /vault/nodes``, whose tree is in the body.

    The one write on this router that names nothing in its path. FastAPI gives
    a dependency the same parsed body the handler gets, so this costs a lookup
    and no second read of the request.
    """
    tree = await vault.get_tree(session, body.tree_id)
    return await permissions.enforce_on(session, tree.project_id, principal, Permission.VAULT)


@project_router.get(
    "/{project_ref}/vault/trees",
    response_model=list[VaultTreeRead],
    tags=["vault"],
    summary="List a project's vault trees",
)
async def list_trees(
    _: Principal = Depends(READ),
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(CHANGE_VAULT),
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
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
    return await _counted_tree_read(session, tree)


@router.get(
    "/trees/{tree_id}",
    response_model=VaultTreeDetail,
    summary="Get a whole vault tree",
)
async def get_tree(
    principal: Principal = Depends(READ),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = SessionDependency,
) -> VaultTreeDetail:
    """Every node in the tree the caller is cleared for, nested, with each
    secret's metadata.

    No secret value appears here at any depth. Call
    `POST /vault/nodes/{id}/reveal` for one of those, one at a time.

    The counts are of what came back rather than of what is there, so the
    header over a filtered tree agrees with the rows under it.
    """
    allowed = await permissions.visible_levels_for(session, tree.project_id, principal)
    nodes = _cleared_nodes(await vault.nodes_in_tree(session, tree.id), allowed)
    size = vault.TreeSize(nodes=len(nodes), secrets=sum(1 for node in nodes if node.is_secret))
    return VaultTreeDetail(
        **_tree_read(tree, size).model_dump(),
        nodes=_nested_nodes(nodes, parent_id=None),
    )


@router.patch(
    "/trees/{tree_id}",
    response_model=VaultTreeRead,
    summary="Rename or reorder a vault tree",
    responses={409: {"description": "This project already has a tree with that name."}},
)
async def update_tree(
    body: VaultTreeUpdate,
    principal: Principal = Depends(CHANGE_THIS_TREE),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = SessionDependency,
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
    return await _counted_tree_read(session, updated)


@router.delete("/trees/{tree_id}", response_model=Acknowledged, summary="Delete a vault tree")
async def delete_tree(
    principal: Principal = Depends(CHANGE_THIS_TREE),
    tree: VaultTree = Depends(resolved_tree),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(may_add_node),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(READ),
    node: VaultNode = Depends(visible_node),
    session: AsyncSession = SessionDependency,
) -> VaultNodeRead:
    """One node and its subtree — metadata only, never a secret value."""
    allowed = await permissions.visible_levels_for(
        session, await _project_of_node(session, node), principal
    )
    nodes = _cleared_nodes(await vault.nodes_in_tree(session, node.tree_id), allowed)
    return _node_read(node, children=_nested_nodes(nodes, parent_id=node.id))


@router.patch(
    "/nodes/{node_id}",
    response_model=VaultNodeRead,
    summary="Rename a node, or amend its secret",
    responses={409: {"description": "A sibling already has that name."}},
)
async def update_node(
    body: VaultNodeUpdate,
    principal: Principal = Depends(CHANGE_THIS_NODE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(CHANGE_THIS_NODE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(CHANGE_THIS_NODE),
    node: VaultNode = Depends(resolved_node),
    session: AsyncSession = SessionDependency,
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
    principal: Principal = Depends(REVEAL_THIS_SECRET),
    node: VaultNode = Depends(visible_node),
    session: AsyncSession = SessionDependency,
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
        default_sensitivity=sensitivity_of.parse(tree.default_sensitivity, DEFAULT_LEVEL),
        node_count=size.nodes,
        secret_count=size.secrets,
        created_at=tree.created_at,
        updated_at=tree.updated_at,
    )


async def _counted_tree_read(session: AsyncSession, tree: VaultTree) -> VaultTreeRead:
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
        sensitivity=sensitivity_of.parse(node.sensitivity, DEFAULT_LEVEL),
        created_at=node.created_at,
        updated_at=node.updated_at,
        secret=SecretRead.model_validate(node.secret) if node.secret is not None else None,
        children=children or [],
    )


def _nested_nodes(nodes: list[VaultNode], *, parent_id: UUID | None) -> list[VaultNodeRead]:
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
