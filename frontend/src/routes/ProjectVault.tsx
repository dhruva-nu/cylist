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
import { useEffect, useState } from 'react'
import {
  api,
  type SecretInput,
  type VaultNode,
  type VaultNodeKind,
  type VaultTreeDetail,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { Button, EmptyState, ErrorBanner, Eyebrow, cardStyles } from '../components/ui'
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
    mutationFn: (nodeId: string) => api.deleteVaultNode(nodeId),
    onSuccess: async () => {
      setSelectedId(null)
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
                    {tree.nodes.map((node) => (
                      <NodeRow
                        key={node.id}
                        node={node}
                        selectedId={selectedId}
                        openNodes={openNodes}
                        onSelect={select}
                      />
                    ))}
                    <button
                      type="button"
                      className={`${styles.node} ${styles.add}`}
                      onClick={() => setAdding({ treeId: tree.id, parentId: null })}
                    >
                      <span className={styles.caret} />+ Add a node
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
                onAdd={(kind) =>
                  setAdding({ treeId: selected.node.tree_id, parentId: selected.node.id, kind })
                }
                onSelect={select}
                onEdit={() => setEditing(selected.node)}
                onMove={() => setMoving(selected.node)}
                onDelete={() => remove.mutate(selected.node.id)}
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
        <TreeDialog projectKey={projectKey} onDone={refresh} onClose={() => setNamingTree(false)} />
      ) : null}

      {adding ? (
        <NodeDialog destination={adding} onDone={refresh} onClose={() => setAdding(null)} />
      ) : null}

      {editing ? (
        <NodeDialog
          node={editing}
          destination={{ treeId: editing.tree_id, parentId: editing.parent_id }}
          onDone={refresh}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {moving ? (
        <MoveDialog
          node={moving}
          tree={loaded.find((tree) => tree.id === moving.tree_id)}
          onDone={refresh}
          onClose={() => setMoving(null)}
        />
      ) : null}
    </>
  )
}

function NodeRow({
  node,
  selectedId,
  openNodes,
  onSelect,
}: {
  node: VaultNode
  selectedId: string | null
  openNodes: string[]
  onSelect: (node: VaultNode) => void
}) {
  const open = openNodes.includes(node.id)
  const isBranch = node.kind === 'branch'

  return (
    <div>
      <button
        type="button"
        className={`${styles.node} ${node.id === selectedId ? styles.selected : ''}`}
        onClick={() => onSelect(node)}
      >
        <span className={`${styles.caret} ${open ? styles.open : ''}`}>{isBranch ? '▶' : ''}</span>
        {isBranch ? <FolderIcon /> : <LockIcon />}
        <span className={styles.nodeName}>{node.name}</span>
      </button>
      {isBranch && open ? (
        <div className={styles.children}>
          {node.children.map((child) => (
            <NodeRow
              key={child.id}
              node={child}
              selectedId={selectedId}
              openNodes={openNodes}
              onSelect={onSelect}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function NodeDetail({
  node,
  path,
  onAdd,
  onSelect,
  onEdit,
  onMove,
  onDelete,
}: {
  node: VaultNode
  path: string[]
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
        <SecretDetail node={node} />
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
function SecretDetail({ node }: { node: VaultNode }) {
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
    onSuccess: (secret) => setRevealed(secret.value),
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
    onSuccess: setCopied,
  })

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
              <Button variant="ghost" small onClick={() => copy.mutate('username')}>
                {copied === 'username' ? 'Copied' : 'Copy'}
              </Button>
            </span>
          </>
        ) : null}

        <span className={styles.key}>Secret</span>
        <span className={styles.value}>
          <span className={styles.masked}>{revealed ?? MASK}</span>
          <Button
            variant="ghost"
            small
            disabled={reveal.isPending}
            onClick={() => (revealed ? setRevealed(null) : reveal.mutate())}
          >
            {revealed ? 'Hide' : reveal.isPending ? 'Revealing…' : 'Reveal'}
          </Button>
          <Button
            variant="ghost"
            small
            disabled={copy.isPending}
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
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')

  const save = useMutation({
    mutationFn: () => api.createVaultTree(projectKey, name.trim()),
    onSuccess: async () => {
      await onDone()
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
  onDone: () => Promise<void>
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
      await onDone()
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
            <div className={styles.segmented}>
              <button
                type="button"
                className={kind === 'branch' ? styles.on : undefined}
                onClick={() => setKind('branch')}
              >
                Branch
              </button>
              <button
                type="button"
                className={kind === 'secret' ? styles.on : undefined}
                onClick={() => setKind('secret')}
              >
                Secret
              </button>
            </div>
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
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [parentId, setParentId] = useState<string>(node.parent_id ?? '')
  const [position, setPosition] = useState(0)

  const save = useMutation({
    mutationFn: () => api.moveVaultNode(node.id, parentId || null, position),
    onSuccess: async () => {
      await onDone()
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
