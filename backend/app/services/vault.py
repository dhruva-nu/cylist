"""Vault trees, nodes and secrets.

Encryption itself lives in :mod:`app.core.crypto`, but every path in or out of
a ciphertext goes through this module, so this is where the rules about *what*
and *where* are enforced:

* a credential is sealed on the way in, and only :func:`reveal` unseals it;
* a secret hangs off a ``secret`` node, never off a ``branch``;
* a node cannot be moved inside itself;
* siblings have distinct names.

The last three are constrained in the schema too (see :mod:`app.models.vault`).
They are checked here as well so a caller who breaks one gets a sentence
explaining the refusal rather than a foreign key violation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import KEY_VERSION, VaultCipher
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.ids import uuid7
from app.models.project import Project
from app.models.vault import VaultNode, VaultNodeKind, VaultSecret, VaultTree
from app.schemas.vault import (
    SecretCreate,
    SecretUpdate,
    VaultNodeCreate,
    VaultNodeMove,
    VaultNodeUpdate,
    VaultTreeCreate,
    VaultTreeUpdate,
)


@dataclass(frozen=True, slots=True)
class VaultCounts:
    """How much is in a project's vault — the hub card's two numbers."""

    trees: int
    secrets: int


@dataclass(frozen=True, slots=True)
class TreeSize:
    """How much is in one tree — the figure beside its name in the rail."""

    nodes: int
    secrets: int


# --- Trees -----------------------------------------------------------------


async def create_tree(session: AsyncSession, project: Project, data: VaultTreeCreate) -> VaultTree:
    """Add a tree to a project, at the end of the list.

    Raises:
        ConflictError: if the project already has a tree with that name.
    """
    existing = await list_trees(session, project)
    if any(tree.name == data.name for tree in existing):
        raise ConflictError(
            f"This project already has a vault tree called {data.name!r}.",
            details={"name": data.name},
        )

    tree = VaultTree(project_id=project.id, name=data.name, position=len(existing))
    session.add(tree)
    await session.flush()
    return tree


async def list_trees(session: AsyncSession, project: Project) -> list[VaultTree]:
    """Return a project's trees in display order."""
    statement = (
        select(VaultTree)
        .where(VaultTree.project_id == project.id)
        .order_by(VaultTree.position, VaultTree.created_at)
    )
    return list(await session.scalars(statement))


async def get_tree(session: AsyncSession, tree_id: UUID) -> VaultTree:
    """Fetch one tree, or raise :class:`NotFoundError`."""
    tree = await session.get(VaultTree, tree_id)
    if tree is None:
        raise NotFoundError("No vault tree with that id.")
    return tree


async def update_tree(session: AsyncSession, tree: VaultTree, data: VaultTreeUpdate) -> VaultTree:
    """Rename or reposition a tree.

    Raises:
        ConflictError: if another tree in this project already has that name.
    """
    if data.name is not None and data.name != tree.name:
        others = await session.scalars(
            select(VaultTree).where(
                VaultTree.project_id == tree.project_id, VaultTree.id != tree.id
            )
        )
        if any(other.name == data.name for other in others):
            raise ConflictError(
                f"This project already has a vault tree called {data.name!r}.",
                details={"name": data.name},
            )
        tree.name = data.name

    if data.position is not None:
        tree.position = data.position

    await session.flush()
    await session.refresh(tree)
    return tree


async def delete_tree(session: AsyncSession, tree: VaultTree) -> None:
    """Remove a tree and everything in it.

    Unlike a project, a vault tree is genuinely deleted. An archived project
    still holds credentials worth keeping; a credential you have finished with
    should stop existing.
    """
    await session.delete(tree)
    await session.flush()


async def tree_sizes(session: AsyncSession, tree_ids: Sequence[UUID]) -> dict[UUID, TreeSize]:
    """Count the nodes and credentials in several trees at once.

    One query for the whole left-hand rail, rather than one per tree — and
    none of them touching a ciphertext column.
    """
    if not tree_ids:
        return {}

    rows = await session.execute(
        select(
            VaultNode.tree_id,
            func.count(),
            func.count().filter(VaultNode.kind == VaultNodeKind.SECRET),
        )
        .where(VaultNode.tree_id.in_(tree_ids))
        .group_by(VaultNode.tree_id)
    )
    sizes = dict.fromkeys(tree_ids, TreeSize(nodes=0, secrets=0))
    for tree_id, nodes, secrets in rows:
        sizes[tree_id] = TreeSize(nodes=nodes, secrets=secrets)
    return sizes


async def counts(session: AsyncSession, project: Project) -> VaultCounts:
    """Count a project's trees, and the credentials across all of them."""
    row = (
        await session.execute(
            select(
                func.count(func.distinct(VaultTree.id)),
                func.count(VaultNode.id).filter(VaultNode.kind == VaultNodeKind.SECRET),
            )
            .select_from(VaultTree)
            .outerjoin(VaultNode, VaultNode.tree_id == VaultTree.id)
            .where(VaultTree.project_id == project.id)
        )
    ).one()
    return VaultCounts(trees=row[0], secrets=row[1])


# --- Nodes -----------------------------------------------------------------


async def nodes_in_tree(session: AsyncSession, tree_id: UUID) -> list[VaultNode]:
    """Every node in one tree, in sibling order.

    Two queries whatever the depth — one for the nodes, one for their secrets'
    metadata — which is why the API hands back a whole tree at a time rather
    than making the client walk it a level per request.
    """
    statement = (
        select(VaultNode)
        .where(VaultNode.tree_id == tree_id)
        .order_by(VaultNode.position, VaultNode.created_at)
    )
    return list(await session.scalars(statement))


async def get_node(session: AsyncSession, node_id: UUID) -> VaultNode:
    """Fetch one node, or raise :class:`NotFoundError`."""
    node = await session.get(VaultNode, node_id)
    if node is None:
        raise NotFoundError("No vault node with that id.")
    return node


async def create_node(
    session: AsyncSession, cipher: VaultCipher, data: VaultNodeCreate
) -> VaultNode:
    """Create a branch or a secret, at the end of its parent's children.

    Raises:
        NotFoundError: if the tree or the named parent does not exist.
        UnprocessableRequestError: if the parent is in another tree, or is
            itself a secret.
        ConflictError: if a sibling already has this name.
    """
    tree = await get_tree(session, data.tree_id)
    parent = await _resolve_parent(session, tree, data.parent_id)

    siblings = await _siblings(session, tree.id, data.parent_id)
    _refuse_duplicate_name(siblings, data.name)

    # The id is minted here rather than left to the column default, because the
    # ciphertext is bound to it: there is nothing to seal a value against until
    # the node has an identity.
    node = VaultNode(
        id=uuid7(),
        tree_id=tree.id,
        parent_id=parent.id if parent is not None else None,
        name=data.name,
        kind=data.kind,
        position=len(siblings),
    )
    if data.secret is not None:
        node.secret = _seal(cipher, node.id, data.secret)

    session.add(node)
    await session.flush()
    await session.refresh(node)
    return node


async def update_node(
    session: AsyncSession, cipher: VaultCipher, node: VaultNode, data: VaultNodeUpdate
) -> VaultNode:
    """Rename a node, or amend its secret.

    Raises:
        ConflictError: if the new name is already used by a sibling.
        UnprocessableRequestError: if a secret is offered for a branch.
    """
    if data.name is not None and data.name != node.name:
        siblings = await _siblings(session, node.tree_id, node.parent_id, excluding=node.id)
        _refuse_duplicate_name(siblings, data.name)
        node.name = data.name

    if data.secret is not None:
        _amend_secret(cipher, node, data.secret)

    await session.flush()
    await session.refresh(node)
    return node


async def delete_node(session: AsyncSession, node: VaultNode) -> None:
    """Remove a node and, if it is a branch, everything beneath it."""
    tree_id, parent_id = node.tree_id, node.parent_id
    await session.delete(node)
    await session.flush()
    await _renumber(session, tree_id, parent_id)


async def move_node(session: AsyncSession, node: VaultNode, data: VaultNodeMove) -> VaultNode:
    """Move a node to a new parent and position within its tree.

    Raises:
        UnprocessableRequestError: if the destination is a secret, is outside
            this tree, or is the node itself or one of its descendants.
        ConflictError: if something of that name is already there.
    """
    everything = {other.id: other for other in await nodes_in_tree(session, node.tree_id)}
    parent = _validate_destination(everything, node, data.parent_id)
    destination_id = parent.id if parent is not None else None

    if destination_id != node.parent_id:
        arrivals = [
            other
            for other in everything.values()
            if other.parent_id == destination_id and other.id != node.id
        ]
        _refuse_duplicate_name(arrivals, node.name)

    origin_id = node.parent_id
    node.parent_id = destination_id
    # Flush before renumbering: with autoflush off, the queries below would
    # otherwise still see the node under its old parent.
    await session.flush()

    await _place(session, node, data.position)
    if origin_id != destination_id:
        await _renumber(session, node.tree_id, origin_id)

    await session.refresh(node)
    return node


async def reveal(session: AsyncSession, cipher: VaultCipher, node: VaultNode) -> str:
    """Decrypt and return one credential.

    This is the only function in Cylist that turns a stored ciphertext back
    into a string. Checking ``vault:reveal`` and writing the audit entry are
    the caller's job, and both happen on the single route that reaches here.

    Raises:
        UnprocessableRequestError: if the node is a branch, or has no value.
        SecretUnreadableError: if the ciphertext does not authenticate.
    """
    secret = await _require_secret(session, node)
    return cipher.open(secret.secret_ciphertext, node_id=node.id, key_version=secret.key_version)


# --- Internals -------------------------------------------------------------


def _seal(cipher: VaultCipher, node_id: UUID, data: SecretCreate) -> VaultSecret:
    return VaultSecret(
        node_id=node_id,
        node_kind=VaultNodeKind.SECRET,
        username=data.username,
        url=data.url,
        notes=data.notes,
        secret_ciphertext=cipher.seal(data.value, node_id=node_id),
        key_version=KEY_VERSION,
    )


def _amend_secret(cipher: VaultCipher, node: VaultNode, data: SecretUpdate) -> None:
    """Apply a partial update to a node's secret, resealing only if asked to."""
    if node.secret is None:
        raise UnprocessableRequestError(
            f"{node.name!r} is a branch, so it has no secret to change.",
            details={"kind": node.kind.value},
        )

    for field, value in data.model_dump(exclude_unset=True, exclude={"value"}).items():
        setattr(node.secret, field, value)

    if data.value is not None:
        # Resealing draws a new nonce, so storing the same credential twice
        # never produces the same bytes.
        node.secret.secret_ciphertext = cipher.seal(data.value, node_id=node.id)
        node.secret.key_version = KEY_VERSION


async def _require_secret(session: AsyncSession, node: VaultNode) -> VaultSecret:
    if node.kind is not VaultNodeKind.SECRET:
        raise UnprocessableRequestError(
            f"{node.name!r} is a branch. Only a secret node holds a credential.",
            details={"kind": node.kind.value},
        )

    secret = node.secret if node.secret is not None else await session.get(VaultSecret, node.id)
    if secret is None:
        # The schema can stop a secret attaching to a branch, but nothing at
        # that level can insist a secret node *has* its row. Say so plainly
        # rather than handing back an empty string.
        raise UnprocessableRequestError(
            f"{node.name!r} has no stored value.", details={"node_id": str(node.id)}
        )
    return secret


async def _resolve_parent(
    session: AsyncSession, tree: VaultTree, parent_id: UUID | None
) -> VaultNode | None:
    if parent_id is None:
        return None

    parent = await get_node(session, parent_id)
    if parent.tree_id != tree.id:
        raise UnprocessableRequestError(
            "That parent belongs to a different vault tree.",
            details={"parent_id": str(parent_id), "tree_id": str(tree.id)},
        )
    _refuse_secret_parent(parent)
    return parent


def _refuse_secret_parent(parent: VaultNode) -> None:
    if parent.kind is not VaultNodeKind.BRANCH:
        raise UnprocessableRequestError(
            f"{parent.name!r} is a secret, and a secret holds a credential rather than "
            "other nodes. Put this under a branch.",
            details={"parent_id": str(parent.id)},
        )


def _validate_destination(
    everything: dict[UUID, VaultNode], node: VaultNode, parent_id: UUID | None
) -> VaultNode | None:
    """Check where a move is headed — above all, that it is not into itself."""
    if parent_id is None:
        return None

    if parent_id == node.id:
        raise UnprocessableRequestError(
            f"{node.name!r} cannot be its own parent.", details={"node_id": str(node.id)}
        )

    parent = everything.get(parent_id)
    if parent is None:
        raise UnprocessableRequestError(
            "That parent is not in this vault tree.",
            details={"parent_id": str(parent_id), "tree_id": str(node.tree_id)},
        )

    _refuse_secret_parent(parent)

    if _is_descendant(everything, parent, node.id):
        raise UnprocessableRequestError(
            f"{parent.name!r} sits inside {node.name!r}, so moving {node.name!r} there "
            "would cut the branch off from its own tree.",
            details={"node_id": str(node.id), "parent_id": str(parent_id)},
        )
    return parent


def _is_descendant(everything: dict[UUID, VaultNode], node: VaultNode, ancestor_id: UUID) -> bool:
    """Walk up from ``node`` looking for ``ancestor_id``.

    ``everything`` holds one whole tree, whose parent links are acyclic, so the
    walk terminates at a root. The visited set is belt and braces against a
    cycle that somehow got written anyway: spinning forever inside a request
    would be a far worse failure than a wrong answer.
    """
    seen: set[UUID] = set()
    current = node
    while current.parent_id is not None and current.parent_id not in seen:
        if current.parent_id == ancestor_id:
            return True
        seen.add(current.parent_id)
        found = everything.get(current.parent_id)
        if found is None:
            return False
        current = found
    return False


def _under(parent_id: UUID | None) -> ColumnElement[bool]:
    """Match one parent's children, where "no parent" means the top level.

    Spelled out rather than left to ``== parent_id``, which would render
    ``IS NULL`` only by accident of the operator overload.
    """
    if parent_id is None:
        return VaultNode.parent_id.is_(None)
    return VaultNode.parent_id == parent_id


async def _siblings(
    session: AsyncSession,
    tree_id: UUID,
    parent_id: UUID | None,
    *,
    excluding: UUID | None = None,
) -> list[VaultNode]:
    statement = (
        select(VaultNode)
        .where(VaultNode.tree_id == tree_id, _under(parent_id))
        .order_by(VaultNode.position, VaultNode.created_at)
    )
    if excluding is not None:
        statement = statement.where(VaultNode.id != excluding)
    return list(await session.scalars(statement))


def _refuse_duplicate_name(siblings: list[VaultNode], name: str) -> None:
    if any(sibling.name == name for sibling in siblings):
        raise ConflictError(
            f"There is already something called {name!r} here.", details={"name": name}
        )


async def _place(session: AsyncSession, node: VaultNode, position: int) -> None:
    """Slot ``node`` in among its siblings at ``position`` and renumber them all.

    A position past the end means "last", which is what a client that does not
    know how many siblings there are will send.
    """
    others = await _siblings(session, node.tree_id, node.parent_id, excluding=node.id)
    index = min(position, len(others))
    for order, sibling in enumerate([*others[:index], node, *others[index:]]):
        if sibling.position != order:
            sibling.position = order
    await session.flush()


async def _renumber(session: AsyncSession, tree_id: UUID, parent_id: UUID | None) -> None:
    """Close the gaps so one parent's children are numbered 0, 1, 2, …

    Order is all that is read, but keeping positions contiguous means a client
    that sends ``{"position": 2}`` gets the third slot rather than a guess.
    """
    for order, sibling in enumerate(await _siblings(session, tree_id, parent_id)):
        if sibling.position != order:
            sibling.position = order
    await session.flush()
