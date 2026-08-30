/**
 * A project's vault.
 *
 * Trees down the left, whatever is selected on the right. The rule the whole
 * screen is built around: **a plaintext secret is never in the page until you
 * ask for it.** The tree endpoint returns metadata only, `Reveal` calls
 * `/vault/nodes/{id}/reveal` (which the server logs), and the value is held in
 * component state that a second click, or moving the selection, throws away.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import {
  api,
  type SecretInput,
  type VaultNode,
  type VaultNodeKind,
  type VaultTreeDetail,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import {
  Button,
  EmptyState,
  ErrorBanner,
  Eyebrow,
  LiveRegion,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import styles from './ProjectVault.module.css'

const MASK = '••••••••••••'

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

/** Where a new node would go: under a tree's root, or under a branch. */
interface Destination {
  treeId: string
  parentId: string | null
  kind?: VaultNodeKind
}

export function ProjectVault() {
  const { projectKey } = useParams({ from: '/p/$projectKey/vault' })
  const queryClient = useQueryClient()
  const { message, announce } = useAnnouncer()

  const [openTrees, setOpenTrees] = useState<string[]>([])
  const [openNodes, setOpenNodes] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [namingTree, setNamingTree] = useState(false)
  const [adding, setAdding] = useState<Destination | null>(null)
  const [editing, setEditing] = useState<VaultNode | null>(null)
  const [moving, setMoving] = useState<VaultNode | null>(null)

  const trees = useQuery({
    queryKey: ['vault-trees', projectKey],
    queryFn: () => api.listVaultTrees(projectKey),
  })

  const details = useQuery({
    queryKey: ['vault-tree-details', projectKey, (trees.data ?? []).map((t) => t.id).join(',')],
    queryFn: () => Promise.all((trees.data ?? []).map((tree) => api.getVaultTree(tree.id))),
    enabled: trees.data !== undefined,
  })

  // The first tree starts open, as in the mock: an empty left rail teaches
  // nobody what a tree is.
  const firstTreeId = trees.data?.[0]?.id
  useEffect(() => {
    if (firstTreeId) setOpenTrees((current) => (current.length ? current : [firstTreeId]))
  }, [firstTreeId])

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['vault-trees', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['vault-tree-details', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const remove = useMutation({
    // The node rather than its id, so the announcement can name what has gone.
    mutationFn: (node: VaultNode) => api.deleteVaultNode(node.id),
    onSuccess: async (_deleted, node) => {
      setSelectedId(null)
      announce(`Deleted ${node.name}.`)
      await refresh()
    },
  })

  if (trees.isPending || details.isPending) return <EmptyState>Loading the vault…</EmptyState>
  if (trees.error) return <ErrorBanner>{trees.error.message}</ErrorBanner>
  if (details.error) return <ErrorBanner>{details.error.message}</ErrorBanner>

  const loaded = details.data
  const selected = selectedId ? findNode(loaded, selectedId) : null

  function toggle(list: string[], id: string): string[] {
    return list.includes(id) ? list.filter((other) => other !== id) : [...list, id]
  }

  function select(node: VaultNode) {
    setSelectedId(node.id)
    if (node.kind === 'branch') setOpenNodes((current) => toggle(current, node.id))
  }

  /** Expand or collapse one branch. The arrow keys need to say which, where a
   * click only ever means "the other one". */
  function setBranchOpen(nodeId: string, open: boolean) {
    setOpenNodes((current) => {
      if (open) return current.includes(nodeId) ? current : [...current, nodeId]
      return current.filter((other) => other !== nodeId)
    })
  }

  return (
    <>
      <PageHead
        title="Vault"
        actions={
          <Button variant="go" onClick={() => setNamingTree(true)}>
            + New tree
          </Button>
        }
      >
        Multiple trees, each as deep as you like. Secrets stay masked until you reveal them, and
        every reveal is written to the activity log.
      </PageHead>

      <LiveRegion message={message} />

      {remove.error ? <ErrorBanner>{remove.error.message}</ErrorBanner> : null}

      {loaded.length === 0 ? (
        <EmptyState>
          No trees yet. Start one — “Logins”, “Certificates &amp; keys”, whatever this project
          needs.
        </EmptyState>
      ) : (
        <div className={styles.split}>
          <div className={styles.trees}>
            {loaded.map((tree) => (
              <div key={tree.id} className={styles.tree}>
                <button
                  type="button"
                  className={styles.treeHead}
                  onClick={() => setOpenTrees((current) => toggle(current, tree.id))}
                  aria-expanded={openTrees.includes(tree.id)}
                >
                  <b>{tree.name}</b>
                  <span className={styles.treeCount}>
                    {tree.node_count} {tree.node_count === 1 ? 'node' : 'nodes'}{' '}
                    {openTrees.includes(tree.id) ? '▾' : '▸'}
                  </span>
                </button>
                {openTrees.includes(tree.id) ? (
                  <div className={styles.branchList}>
                    <TreeNav
                      tree={tree}
                      selectedId={selectedId}
                      openNodes={openNodes}
                      onSelect={select}
                      onSetOpen={setBranchOpen}
                    />
                    {/* Outside the tree on purpose: role="tree" may only hold
                        treeitems, and this is an action, not a node. */}
                    <button
                      type="button"
                      className={`${styles.node} ${styles.add}`}
                      onClick={() => setAdding({ treeId: tree.id, parentId: null })}
                    >
                      <span className={styles.caret} aria-hidden="true" />+ Add a node
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <div className={styles.pane}>
            {selected ? (
              <NodeDetail
                node={selected.node}
                path={selected.path}
                announce={announce}
                onAdd={(kind) =>
                  setAdding({ treeId: selected.node.tree_id, parentId: selected.node.id, kind })
                }
                onSelect={select}
                onEdit={() => setEditing(selected.node)}
                onMove={() => setMoving(selected.node)}
                onDelete={() => remove.mutate(selected.node)}
              />
            ) : (
              <div className={styles.blank}>
                <div>
                  <LockIcon />
                  <br />
                  Select a node to see what is inside.
                  <br />
                  <span className={styles.hint}>
                    Branches hold more nodes; secrets hold a login, key or link.
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {namingTree ? (
        <TreeDialog
          projectKey={projectKey}
          onDone={async (name) => {
            await refresh()
            announce(`Created the ${name} tree.`)
          }}
          onClose={() => setNamingTree(false)}
        />
      ) : null}

      {adding ? (
        <NodeDialog
          destination={adding}
          onDone={async (name) => {
            await refresh()
            announce(`Added ${name}.`)
          }}
          onClose={() => setAdding(null)}
        />
      ) : null}

      {editing ? (
        <NodeDialog
          node={editing}
          destination={{ treeId: editing.tree_id, parentId: editing.parent_id }}
          onDone={async (name) => {
            await refresh()
            announce(`Saved ${name}.`)
          }}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {moving ? (
        <MoveDialog
          node={moving}
          tree={loaded.find((tree) => tree.id === moving.tree_id)}
          onDone={async (name) => {
            await refresh()
            announce(`Moved ${name}.`)
          }}
          onClose={() => setMoving(null)}
        />
      ) : null}
    </>
  )
}

/** One row of a tree as the keyboard sees it: flattened, and only what is on
 * screen — a collapsed branch's children are not somewhere the arrows can go. */
interface Row {
  node: VaultNode
  parentId: string | null
  level: number
}

function visibleRows(
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
        {isBranch ? <FolderIcon /> : <LockIcon />}
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

function NodeDetail({
  node,
  path,
  announce,
  onAdd,
  onSelect,
  onEdit,
  onMove,
  onDelete,
}: {
  node: VaultNode
  path: string[]
  announce: (message: string) => void
  onAdd: (kind: VaultNodeKind) => void
  onSelect: (node: VaultNode) => void
  onEdit: () => void
  onMove: () => void
  onDelete: () => void
}) {
  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        <div>
          <div className={styles.path}>{path.slice(0, -1).join(' / ')}</div>
          <h2>{node.name}</h2>
          {node.kind === 'branch' ? (
            <p className={styles.note}>
              Branch with {node.children.length} child{node.children.length === 1 ? '' : 'ren'}.
            </p>
          ) : null}
        </div>
        <div className={styles.detailActions}>
          {node.kind === 'branch' ? (
            <>
              <Button small onClick={() => onAdd('branch')}>
                + Branch
              </Button>
              <Button small variant="go" onClick={() => onAdd('secret')}>
                + Secret
              </Button>
            </>
          ) : (
            <Button small onClick={onEdit}>
              Edit
            </Button>
          )}
          <Button small variant="ghost" onClick={onMove}>
            Move
          </Button>
          <Button small variant="ghost" danger onClick={onDelete}>
            Delete
          </Button>
        </div>
      </div>

      {node.kind === 'secret' ? (
        <SecretDetail node={node} announce={announce} />
      ) : (
        <div className={styles.cards}>
          {node.children.map((child) => (
            <button
              key={child.id}
              type="button"
              className={`${cardStyles.card} ${cardStyles.clickable} ${styles.childCard}`}
              onClick={() => onSelect(child)}
            >
              <span className={styles.childKind}>
                {child.kind === 'branch' ? <FolderIcon /> : <LockIcon />}
                <Eyebrow>{child.kind}</Eyebrow>
              </span>
              <b>{child.name}</b>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * One credential.
 *
 * `revealed` is the only place a plaintext lives, and it is cleared whenever
 * the selected node changes so switching entries cannot leave one on screen.
 */
function SecretDetail({
  node,
  announce,
}: {
  node: VaultNode
  announce: (message: string) => void
}) {
  const [revealed, setRevealed] = useState<string | null>(null)
  const [copied, setCopied] = useState<'username' | 'secret' | null>(null)

  useEffect(() => {
    setRevealed(null)
    setCopied(null)
  }, [node.id])

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(null), 2000)
    return () => clearTimeout(timer)
  }, [copied])

  const reveal = useMutation({
    mutationFn: () => api.revealSecret(node.id),
    onSuccess: (secret) => {
      setRevealed(secret.value)
      // What is announced is that it is on screen, never the value itself: a
      // live region is read aloud, and a credential read aloud is a credential
      // told to the room.
      announce(`The secret for ${node.name} is now showing on screen.`)
    },
  })

  /** Copying the secret reveals it, so it goes through the same logged
   * endpoint — taking a credential to the clipboard is reading it. */
  const copy = useMutation({
    mutationFn: async (what: 'username' | 'secret') => {
      const text =
        what === 'username'
          ? (node.secret?.username ?? '')
          : (revealed ?? (await api.revealSecret(node.id)).value)
      await navigator.clipboard.writeText(text)
      return what
    },
    onSuccess: (what) => {
      setCopied(what)
      announce(`${what === 'username' ? 'Username' : 'Secret'} copied to the clipboard.`)
    },
  })

  /** Reveal, or put it away again. Hiding is a state change nobody watching
   * the screen can miss and nobody listening would otherwise hear. */
  function toggleReveal() {
    if (revealed === null) {
      reveal.mutate()
      return
    }
    setRevealed(null)
    announce(`The secret for ${node.name} is hidden again.`)
  }

  const secret = node.secret
  if (!secret) return <p className={styles.note}>This entry has no stored value.</p>

  return (
    <>
      {reveal.error ? <ErrorBanner>{reveal.error.message}</ErrorBanner> : null}
      {copy.error ? <ErrorBanner>{copy.error.message}</ErrorBanner> : null}

      <div className={styles.kv}>
        {secret.username ? (
          <>
            <span className={styles.key}>Username</span>
            <span className={styles.value}>
              <span className={styles.mono}>{secret.username}</span>
              <Button
                variant="ghost"
                small
                aria-label={copied === 'username' ? 'Username copied' : 'Copy username'}
                onClick={() => copy.mutate('username')}
              >
                {copied === 'username' ? 'Copied' : 'Copy'}
              </Button>
            </span>
          </>
        ) : null}

        <span className={styles.key}>Secret</span>
        <span className={styles.value}>
          {/* Masked, the dots are decoration standing in for something absent,
              so they are hidden and the state is spelled out instead — twelve
              bullet characters read one at a time tell nobody anything. */}
          <span className={styles.masked}>
            {revealed === null ? (
              <>
                <span className="visually-hidden">Secret, hidden</span>
                <span aria-hidden="true">{MASK}</span>
              </>
            ) : (
              <>
                <span className="visually-hidden">Secret, showing: </span>
                {revealed}
              </>
            )}
          </span>
          <Button variant="ghost" small disabled={reveal.isPending} onClick={toggleReveal}>
            {revealed ? 'Hide' : reveal.isPending ? 'Revealing…' : 'Reveal'}
          </Button>
          <Button
            variant="ghost"
            small
            disabled={copy.isPending}
            aria-label={copied === 'secret' ? 'Secret copied' : 'Copy secret'}
            onClick={() => copy.mutate('secret')}
          >
            {copied === 'secret' ? 'Copied' : 'Copy'}
          </Button>
        </span>

        {secret.url ? (
          <>
            <span className={styles.key}>URL</span>
            <span className={styles.value}>
              <a className={styles.mono} href={secret.url} target="_blank" rel="noreferrer">
                {secret.url}
              </a>
            </span>
          </>
        ) : null}

        {secret.notes ? (
          <>
            <span className={styles.key}>Notes</span>
            <span className={styles.value}>
              <p className={styles.note}>{secret.notes}</p>
            </span>
          </>
        ) : null}

        <span className={styles.key}>Updated</span>
        <span className={`${styles.value} ${styles.updated}`}>
          {new Date(secret.updated_at).toLocaleString()}
        </span>
      </div>
    </>
  )
}

function TreeDialog({
  projectKey,
  onDone,
  onClose,
}: {
  projectKey: string
  onDone: (name: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')

  const save = useMutation({
    mutationFn: () => api.createVaultTree(projectKey, name.trim()),
    onSuccess: async () => {
      // The name goes back up rather than the dialog announcing it: the screen
      // owns the live region, and the dialog is about to stop existing.
      await onDone(name.trim())
      onClose()
    },
  })

  return (
    <Modal
      title="New tree"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={save.isPending || !name.trim()}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Creating…' : 'Create tree'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        <Field label="Tree name" required>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Cloud accounts"
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

const KINDS: { value: VaultNodeKind; label: string }[] = [
  { value: 'branch', label: 'Branch' },
  { value: 'secret', label: 'Secret' },
]

/**
 * Branch or secret, as a radio group rather than as two buttons.
 *
 * One tab stop with the arrows moving between the choices. Two adjacent tab
 * stops that look like a single control is how a five-field form comes to feel
 * like a ten-field one.
 */
function KindPicker({
  kind,
  onPick,
}: {
  kind: VaultNodeKind
  onPick: (kind: VaultNodeKind) => void
}) {
  const group = useRef<HTMLDivElement>(null)

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0
    if (step === 0) return
    event.preventDefault()

    const index = KINDS.findIndex((option) => option.value === kind)
    const next = KINDS[(index + step + KINDS.length) % KINDS.length]
    if (next === undefined) return

    onPick(next.value)
    // Focus follows the choice, or the group's single tab stop would be left
    // sitting on the option the user just moved away from.
    group.current?.querySelector<HTMLElement>(`[data-kind="${next.value}"]`)?.focus()
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label="Kind"
      className={styles.segmented}
      onKeyDown={onKeyDown}
    >
      {KINDS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          data-kind={option.value}
          aria-checked={option.value === kind}
          tabIndex={option.value === kind ? 0 : -1}
          className={option.value === kind ? styles.on : undefined}
          onClick={() => onPick(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** The form's own shape: every field a string, so the inputs stay controlled.
 * Blanks are turned back into nulls on the way to the API. */
interface SecretForm {
  value: string
  username: string
  url: string
  notes: string
}

/**
 * Creates a node, or edits one.
 *
 * Editing never shows the stored credential — the form has no way to read it
 * — so the value field means "replace it with this", and leaving it blank
 * keeps whatever is there.
 */
function NodeDialog({
  node,
  destination,
  onDone,
  onClose,
}: {
  node?: VaultNode
  destination: Destination
  onDone: (name: string) => Promise<void>
  onClose: () => void
}) {
  const editing = node !== undefined
  const [kind, setKind] = useState<VaultNodeKind>(node?.kind ?? destination.kind ?? 'branch')
  const [name, setName] = useState(node?.name ?? '')
  const [form, setForm] = useState<SecretForm>({
    value: '',
    username: node?.secret?.username ?? '',
    url: node?.secret?.url ?? '',
    notes: node?.secret?.notes ?? '',
  })

  const save = useMutation({
    mutationFn: async () => {
      const secret: SecretInput = {
        username: form.username.trim() || null,
        url: form.url.trim() || null,
        notes: form.notes,
        ...(form.value ? { value: form.value } : {}),
      }
      if (node) {
        await api.updateVaultNode(node.id, {
          name: name.trim(),
          ...(node.kind === 'secret' ? { secret } : {}),
        })
        return
      }
      await api.createVaultNode({
        tree_id: destination.treeId,
        parent_id: destination.parentId,
        name: name.trim(),
        kind,
        ...(kind === 'secret' ? { secret: { ...secret, value: form.value } } : {}),
      })
    },
    onSuccess: async () => {
      await onDone(name.trim())
      onClose()
    },
  })

  const complete = name.trim() && (editing || kind === 'branch' || form.value)

  return (
    <Modal
      title={editing ? 'Edit entry' : kind === 'secret' ? 'New secret' : 'New node'}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="go" disabled={save.isPending || !complete} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : editing ? 'Save' : 'Add'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}

        <Field label="Name" required>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>

        {/* Hidden when the caller already said which — "+ Secret" means secret. */}
        {editing || destination.kind ? null : (
          <Field label="Kind">
            <KindPicker kind={kind} onPick={setKind} />
          </Field>
        )}

        {kind === 'secret' ? (
          <>
            <FieldPair>
              <Field label="Username">
                <input
                  value={form.username}
                  onChange={(event) => setForm({ ...form, username: event.target.value })}
                />
              </Field>
              <Field label="URL">
                <input
                  value={form.url}
                  onChange={(event) => setForm({ ...form, url: event.target.value })}
                />
              </Field>
            </FieldPair>
            <Field
              label="Password / key"
              required={!editing}
              hint={
                editing
                  ? 'Leave blank to keep the stored credential. It is never shown here.'
                  : 'Encrypted before it is stored; only Reveal brings it back.'
              }
            >
              <input
                type="password"
                autoComplete="new-password"
                value={form.value}
                onChange={(event) => setForm({ ...form, value: event.target.value })}
              />
            </Field>
            <Field label="Notes">
              <textarea
                value={form.notes}
                onChange={(event) => setForm({ ...form, notes: event.target.value })}
              />
            </Field>
          </>
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/** Re-parent a node. The API refuses a move into the node's own subtree, so
 * the picker leaves those branches out rather than offering a certain 422. */
function MoveDialog({
  node,
  tree,
  onDone,
  onClose,
}: {
  node: VaultNode
  tree: VaultTreeDetail | undefined
  onDone: (name: string) => Promise<void>
  onClose: () => void
}) {
  const [parentId, setParentId] = useState<string>(node.parent_id ?? '')
  const [position, setPosition] = useState(0)

  const save = useMutation({
    mutationFn: () => api.moveVaultNode(node.id, parentId || null, position),
    onSuccess: async () => {
      await onDone(node.name)
      onClose()
    },
  })

  const options = tree ? branchesOutside(tree.nodes, node.id, []) : []

  return (
    <Modal
      title={`Move ${node.name}`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="go" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Moving…' : 'Move'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        <FieldPair>
          <Field label="Into">
            <select value={parentId} onChange={(event) => setParentId(event.target.value)}>
              <option value="">Top of the tree</option>
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Position" hint="0 puts it first; a larger number puts it last.">
            <input
              type="number"
              min={0}
              value={position}
              onChange={(event) => setPosition(Math.max(0, Number(event.target.value)))}
            />
          </Field>
        </FieldPair>
      </ModalBody>
    </Modal>
  )
}

function branchesOutside(
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

function findNode(
  trees: VaultTreeDetail[],
  id: string,
): { node: VaultNode; path: string[] } | null {
  function walk(nodes: VaultNode[], trail: string[]): { node: VaultNode; path: string[] } | null {
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
