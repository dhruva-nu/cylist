/**
 * Which folder is selected, which row has focus, and which are open — the
 * folder tree's whole state, and the keyboard model that walks it.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { FolderNode } from '../../api/client'

/** A folder as the pane draws it: flattened, and knowing where it sits. */
export interface TreeRow {
  node: FolderNode
  level: number
  parentId: string | null
  position: number
  siblings: number
}

/** Every folder id from the root down to this one, empty if it is not in there. */
function trailTo(node: FolderNode, target: string): string[] {
  if (node.id === target) return [node.id]
  for (const child of node.children) {
    const below = trailTo(child, target)
    if (below.length) return [node.id, ...below]
  }
  return []
}

/**
 * Which folder the screen is showing.
 *
 * The root stands in whenever the stored choice is not in this tree — nothing
 * chosen yet, or a choice left over from the project we navigated away from.
 * Deriving it rather than storing it means the screen has no moment of showing
 * a folder that is not there.
 */
function chosenWithin(root: FolderNode | null, candidate: string | null): string | null {
  if (root === null) return null
  return candidate !== null && trailTo(root, candidate).length > 0 ? candidate : root.id
}

/**
 * The tree flattened to the rows that are actually on screen.
 *
 * Arrow keys move down the list the eye sees, not through the nesting, so a
 * folder inside a collapsed branch is not somewhere Down can land. Building
 * that list once gives the keyboard handler plain index arithmetic and gives
 * each row the `aria-level` that tells a screen reader the shape back.
 */
function visibleRows(root: FolderNode, expanded: Set<string>): TreeRow[] {
  const rows: TreeRow[] = []

  function walk(nodes: FolderNode[], level: number, parentId: string | null) {
    nodes.forEach((node, index) => {
      rows.push({ node, level, parentId, position: index + 1, siblings: nodes.length })
      if (expanded.has(node.id) && node.children.length) walk(node.children, level + 1, node.id)
    })
  }

  walk([root], 1, null)
  return rows
}

export function useFolderTree(root: FolderNode | null) {
  const [selected, setSelected] = useState<string | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // Moving focus is the point of arrow keys, and React cannot do it from a
  // render, so the rows have to be reachable as DOM nodes.
  const nodeRefs = useRef(new Map<string, HTMLDivElement>())

  const selectedId = chosenWithin(root, selected)
  const focusedId = chosenWithin(root, focused ?? selected)
  const rows = root === null ? [] : visibleRows(root, expanded)

  // The root opens itself once, when its project's tree arrives. Keyed on the
  // id rather than the tree so a refetch does not reopen what has been closed.
  const rootId = root?.id ?? null
  useEffect(() => {
    if (rootId === null) return
    setExpanded((current) => (current.has(rootId) ? current : new Set(current).add(rootId)))
  }, [rootId])

  function setFolderOpen(folderId: string, shouldOpen: boolean) {
    setExpanded((current) => {
      const next = new Set(current)
      if (shouldOpen) next.add(folderId)
      else next.delete(folderId)
      return next
    })
  }

  function selectFolder(folderId: string) {
    setExpanded((current) => {
      const next = new Set(current)
      // Selecting a folder opens it; choosing the open one again closes it.
      if (selectedId === folderId && next.has(folderId)) next.delete(folderId)
      else if (root) for (const id of trailTo(root, folderId)) next.add(id)
      return next
    })
    setSelected(folderId)
    setFocused(folderId)
  }

  function focusNode(folderId: string) {
    setFocused(folderId)
    nodeRefs.current.get(folderId)?.focus()
  }

  /**
   * The tree's keyboard model, as the ARIA tree pattern describes it.
   *
   * The whole tree is one tab stop; once inside, the arrows do the walking.
   * Right opens a closed folder and steps into an open one, Left closes an
   * open folder and steps out of a closed one, so the same two keys both
   * navigate and disclose without a modifier.
   */
  function onTreeKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = rows.find((row) => row.node.id === focusedId)
    if (current === undefined) return

    const index = rows.indexOf(current)
    const { node, parentId } = current
    const branching = node.children.length > 0
    const isOpen = expanded.has(node.id)

    function step(to: number) {
      const target = rows[to]
      if (target) focusNode(target.node.id)
    }

    switch (event.key) {
      case 'ArrowDown':
        step(index + 1)
        break
      case 'ArrowUp':
        step(index - 1)
        break
      case 'ArrowRight':
        // An open folder's first child is the very next row, by construction.
        if (branching && !isOpen) setFolderOpen(node.id, true)
        else if (branching) step(index + 1)
        else return
        break
      case 'ArrowLeft':
        if (branching && isOpen) setFolderOpen(node.id, false)
        else if (parentId !== null) focusNode(parentId)
        else return
        break
      case 'Home':
        step(0)
        break
      case 'End':
        step(rows.length - 1)
        break
      case 'Enter':
      case ' ':
        selectFolder(node.id)
        break
      default:
        return
    }
    event.preventDefault()
  }

  return {
    selectedId,
    focusedId,
    rows,
    expanded,
    nodeRefs,
    selectFolder,
    setFocused,
    onTreeKeyDown,
  }
}
