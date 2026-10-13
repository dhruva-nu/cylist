/** Walking a vault's trees: finding a node, listing what is on screen, and where a node may move. */

import type { VaultNode, VaultNodeKind, VaultTreeDetail } from '../../api/client'

/** The list with this id added, or taken out if it was already there. */
export function toggleId(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((other) => other !== id) : [...list, id]
}

/** The list with this id in it, added only if it was not there already. */
export function withId(list: string[], id: string): string[] {
  return list.includes(id) ? list : [...list, id]
}

/** Where a new node would go: under a tree's root, or under a branch. */
export interface Destination {
  treeId: string
  parentId: string | null
  /** Set when the caller has already said which — "+ Secret" means a secret. */
  kind?: VaultNodeKind
}

/** One row of a tree as the keyboard sees it: flattened, and only what is on
 * screen — a collapsed branch's children are not somewhere the arrows can go. */
export interface Row {
  node: VaultNode
  parentId: string | null
  level: number
}

export function visibleRows(
  nodes: VaultNode[],
  openNodes: string[],
  level = 1,
  parentId: string | null = null,
): Row[] {
  return nodes.flatMap((node) => {
    const row = { node, parentId, level }
    const expanded = node.kind === 'branch' && openNodes.includes(node.id)
    return expanded ? [row, ...visibleRows(node.children, openNodes, level + 1, node.id)] : [row]
  })
}

/**
 * Every branch a node could be moved into, labelled by its path.
 *
 * The API refuses a move into the node's own subtree, so the node and
 * everything under it are left out rather than offered as a certain 422.
 */
export function branchesOutside(
  nodes: VaultNode[],
  excludeId: string,
  trail: string[],
): { id: string; label: string }[] {
  return nodes.flatMap((node) => {
    if (node.id === excludeId || node.kind !== 'branch') return []
    const label = [...trail, node.name]
    return [
      { id: node.id, label: label.join(' / ') },
      ...branchesOutside(node.children, excludeId, label),
    ]
  })
}

/** A node found in a vault, and the names from its tree down to it. */
export interface FoundNode {
  node: VaultNode
  path: string[]
}

export function findNode(trees: VaultTreeDetail[], id: string): FoundNode | null {
  function walk(nodes: VaultNode[], trail: string[]): FoundNode | null {
    for (const node of nodes) {
      const path = [...trail, node.name]
      if (node.id === id) return { node, path }
      const found = walk(node.children, path)
      if (found) return found
    }
    return null
  }

  for (const tree of trees) {
    const found = walk(tree.nodes, [tree.name])
    if (found) return found
  }
  return null
}
