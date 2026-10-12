/** The left-hand rail of the vault: each tree, and the nodes in it. */

import { useRef, useState, type KeyboardEvent } from 'react'
import type { VaultNode, VaultTreeDetail } from '../../api/client'
import styles from './ProjectVault.module.css'
import { NodeIcon } from './vaultIcons'
import { visibleRows } from './vaultTree'

/**
 * One tree in the left-hand rail: its heading, which opens and closes it, and
 * — while it is open — its nodes and the button that adds one.
 */
export function TreeSection({
  tree,
  open,
  onToggleOpen,
  selectedId,
  openNodes,
  onSelect,
  onSetBranchOpen,
  onAddNode,
}: {
  tree: VaultTreeDetail
  open: boolean
  onToggleOpen: () => void
  selectedId: string | null
  openNodes: string[]
  onSelect: (node: VaultNode) => void
  onSetBranchOpen: (nodeId: string, open: boolean) => void
  /** Null where the reader's role does not allow changing the vault. */
  onAddNode: (() => void) | null
}) {
  return (
    <div className={styles.tree}>
      <button type="button" className={styles.treeHead} onClick={onToggleOpen} aria-expanded={open}>
        <b>{tree.name}</b>
        <span className={styles.treeCount}>
          {tree.node_count} {tree.node_count === 1 ? 'node' : 'nodes'} {open ? '▾' : '▸'}
        </span>
      </button>
      {open ? (
        <div className={styles.branchList}>
          <TreeNav
            tree={tree}
            selectedId={selectedId}
            openNodes={openNodes}
            onSelect={onSelect}
            onSetOpen={onSetBranchOpen}
          />
          {/* Outside the tree on purpose: role="tree" may only hold
              treeitems, and this is an action, not a node. */}
          {onAddNode ? (
            <button type="button" className={`${styles.node} ${styles.add}`} onClick={onAddNode}>
              <span className={styles.caret} aria-hidden="true" />+ Add a node
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

/**
 * One tree, navigable by keyboard.
 *
 * Each tree is its own `role="tree"` holding a single tab stop, so Tab steps
 * between trees and the arrows move within one. The alternative — every node
 * its own tab stop — makes a vault of forty credentials forty presses deep,
 * which is how a tree stops being a shortcut and becomes an obstacle.
 */
function TreeNav({
  tree,
  selectedId,
  openNodes,
  onSelect,
  onSetOpen,
}: {
  tree: VaultTreeDetail
  selectedId: string | null
  openNodes: string[]
  onSelect: (node: VaultNode) => void
  onSetOpen: (nodeId: string, open: boolean) => void
}) {
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const list = useRef<HTMLUListElement>(null)

  const rows = visibleRows(tree.nodes, openNodes)
  // Where Tab lands: wherever the arrows left off, else the selection, else the
  // top. A row that has just been collapsed out of sight falls through the same
  // chain rather than taking the tree's only tab stop with it.
  const active =
    rows.find((row) => row.node.id === focusedId)?.node.id ??
    rows.find((row) => row.node.id === selectedId)?.node.id ??
    rows[0]?.node.id ??
    null

  function focus(nodeId: string) {
    setFocusedId(nodeId)
    list.current?.querySelector<HTMLElement>(`[data-node-id="${nodeId}"]`)?.focus()
  }

  function choose(node: VaultNode) {
    focus(node.id)
    onSelect(node)
  }

  function onKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    const index = rows.findIndex((row) => row.node.id === active)
    const row = rows[index]
    if (row === undefined) return

    const { node, parentId } = row
    const isBranch = node.kind === 'branch'
    const expanded = isBranch && openNodes.includes(node.id)
    const previous = rows[index - 1]
    const next = rows[index + 1]
    const first = rows[0]
    const last = rows[rows.length - 1]

    switch (event.key) {
      case 'ArrowDown':
        if (next) focus(next.node.id)
        break
      case 'ArrowUp':
        if (previous) focus(previous.node.id)
        break
      case 'ArrowRight':
        // Open it, or — already open — step into it. Two meanings for one key,
        // but they are the same intention twice: go further in.
        if (isBranch && !expanded) onSetOpen(node.id, true)
        else if (expanded && node.children[0]) focus(node.children[0].id)
        break
      case 'ArrowLeft':
        if (expanded) onSetOpen(node.id, false)
        else if (parentId) focus(parentId)
        break
      case 'Home':
        if (first) focus(first.node.id)
        break
      case 'End':
        if (last) focus(last.node.id)
        break
      case 'Enter':
      case ' ':
        choose(node)
        break
      default:
        return
    }

    // Only reached for a key we handled: Space would scroll the page and the
    // arrows would scroll the rail out from under the row they just moved to.
    event.preventDefault()
  }

  return (
    <ul ref={list} role="tree" aria-label={tree.name} className={styles.tier} onKeyDown={onKeyDown}>
      {tree.nodes.map((node) => (
        <NodeRow
          key={node.id}
          node={node}
          level={1}
          activeId={active}
          selectedId={selectedId}
          openNodes={openNodes}
          onSelect={choose}
        />
      ))}
    </ul>
  )
}

function NodeRow({
  node,
  level,
  activeId,
  selectedId,
  openNodes,
  onSelect,
}: {
  node: VaultNode
  level: number
  activeId: string | null
  selectedId: string | null
  openNodes: string[]
  onSelect: (node: VaultNode) => void
}) {
  const open = openNodes.includes(node.id)
  const isBranch = node.kind === 'branch'

  return (
    <li
      role="treeitem"
      // Named explicitly, because the accessible name of a branch would
      // otherwise gather up every descendant nested inside its <li>.
      aria-label={node.name}
      aria-level={level}
      aria-selected={node.id === selectedId}
      aria-expanded={isBranch ? open : undefined}
      data-node-id={node.id}
      tabIndex={node.id === activeId ? 0 : -1}
      className={styles.row}
    >
      <span
        className={`${styles.node} ${node.id === selectedId ? styles.selected : ''}`}
        onClick={(event) => {
          // A subtree is nested inside its branch's <li>, so without this a
          // click on a child would select every ancestor on the way up.
          event.stopPropagation()
          onSelect(node)
        }}
      >
        <span className={`${styles.caret} ${open ? styles.open : ''}`} aria-hidden="true">
          {isBranch ? '▶' : ''}
        </span>
        <NodeIcon kind={node.kind} />
        <span className={styles.nodeName}>{node.name}</span>
      </span>
      {isBranch && open ? (
        <ul role="group" className={styles.children}>
          {node.children.map((child) => (
            <NodeRow
              key={child.id}
              node={child}
              level={level + 1}
              activeId={activeId}
              selectedId={selectedId}
              openNodes={openNodes}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}
