/**
 * A project's files.
 *
 * A folder tree on the left, what the selected folder holds on the right.
 * Uploads and links share the table because they answer the same question —
 * "where is the thing about X?" — and it matters far less whether the bytes are
 * ours than whether you can find them.
 *
 * The project's root is a folder like any other: it is selected when the screen
 * opens, it takes uploads and links directly, and it is drawn by the same code
 * as every folder under it. Nothing here has to special-case "no folder chosen"
 * because there is no such state to be in.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import {
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react'
import {
  api,
  type FileItem,
  type FolderNode,
  type ItemSource,
  type LinkInput,
  type Person,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { formatSize, formatStamp } from '../components/format'
import { PageHead } from '../components/Shell'
import { Avatar, Button, ErrorBanner, LiveRegion, cardStyles, useAnnouncer } from '../components/ui'
import styles from './ProjectFiles.module.css'

const FOLDER_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
)

const SOURCE_LABELS: Record<ItemSource, string> = {
  upload: 'Uploaded',
  sharepoint: 'SharePoint',
  gdrive: 'Google Drive',
  other: 'Link',
}

/** Which coloured badge a filename gets, keyed by its extension. */
const BADGES: Record<string, string> = {
  pdf: 'pdf',
  doc: 'doc',
  docx: 'doc',
  md: 'doc',
  txt: 'doc',
  rtf: 'doc',
  html: 'doc',
  xls: 'xls',
  xlsx: 'xls',
  csv: 'xls',
  zip: 'zip',
  gz: 'zip',
  tar: 'zip',
  rar: 'zip',
  '7z': 'zip',
  png: 'img',
  jpg: 'img',
  jpeg: 'img',
  gif: 'img',
  svg: 'img',
  webp: 'img',
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

function badgeOf(name: string): { kind: string; label: string } {
  const extension = extensionOf(name)
  const kind = BADGES[extension]
  if (kind) return { kind, label: extension.slice(0, 4).toUpperCase() }
  return { kind: 'other', label: extension ? extension.slice(0, 3).toUpperCase() : 'FILE' }
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

/** A folder as the pane draws it: flattened, and knowing where it sits. */
interface TreeRow {
  node: FolderNode
  level: number
  parentId: string | null
  position: number
  siblings: number
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

export function ProjectFiles() {
  const { projectKey } = useParams({ from: '/p/$projectKey/files' })
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<string | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [dialog, setDialog] = useState<'upload' | 'link' | 'folder' | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const { message, announce } = useAnnouncer()

  // Moving focus is the point of arrow keys, and React cannot do it from a
  // render, so the rows have to be reachable as DOM nodes.
  const nodeRefs = useRef(new Map<string, HTMLDivElement>())

  const project = useQuery({
    queryKey: ['project', projectKey],
    queryFn: () => api.getProject(projectKey),
  })
  const tree = useQuery({
    queryKey: ['folder-tree', projectKey],
    queryFn: () => api.getTree(projectKey),
  })

  const root = tree.data ?? null
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

  const contents = useQuery({
    queryKey: ['folder-children', selectedId],
    queryFn: () => api.getFolderChildren(selectedId as string),
    enabled: selectedId !== null,
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['folder-tree', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['folder-children'] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const here = contents.data?.folder.name ?? project.data?.name ?? projectKey

  const upload = useMutation({
    mutationFn: async ({ files, addedBy }: { files: File[]; addedBy: string | null }) => {
      if (selectedId === null) throw new Error('The folder tree has not finished loading.')
      // One at a time: two uploads of the same name in one folder race for it,
      // and the loser's 409 is clearer when it is the only thing that failed.
      for (const file of files) await api.uploadFile(selectedId, file, addedBy)
      return files.length
    },
    onMutate: () => setProblem(null),
    onSuccess: async (count) => {
      await refresh()
      announce(`${count} ${count === 1 ? 'file' : 'files'} uploaded to ${here}.`)
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const removeItem = useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => api.deleteItem(id),
    onSuccess: async (_deleted, { name }) => {
      await refresh()
      announce(`${name} deleted.`)
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const removeFolder = useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => api.deleteFolder(id),
    onSuccess: async (_deleted, { name }) => {
      await refresh()
      announce(`Folder ${name} deleted.`)
    },
    onError: (error: Error) => setProblem(error.message),
  })

  // Deleting takes the content off the server's disk, and nothing here undoes
  // it — the one place in Cylist where a stray click really does lose data.
  function confirmDelete(what: string, remove: () => void) {
    if (window.confirm(`Delete ${what}? This cannot be undone.`)) remove()
  }

  async function added(what: string) {
    await refresh()
    announce(`${what} added to ${here}.`)
  }

  function setOpen(folderId: string, shouldOpen: boolean) {
    setExpanded((current) => {
      const next = new Set(current)
      if (shouldOpen) next.add(folderId)
      else next.delete(folderId)
      return next
    })
  }

  function open(folderId: string) {
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
        if (branching && !isOpen) setOpen(node.id, true)
        else if (branching) step(index + 1)
        else return
        break
      case 'ArrowLeft':
        if (branching && isOpen) setOpen(node.id, false)
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
        open(node.id)
        break
      default:
        return
    }
    event.preventDefault()
  }

  const folders = contents.data?.folders ?? []
  const items = contents.data?.items ?? []
  const crumbs = contents.data?.path.map((crumb) => crumb.name) ?? [
    project.data?.name ?? projectKey,
  ]

  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  return (
    <>
      <PageHead
        title="Files"
        actions={
          <>
            {/* Disabled only while the tree is still on its way: every folder
                it can arrive at, the root included, takes files. */}
            <Button variant="go" disabled={selectedId === null} onClick={() => setDialog('upload')}>
              ↑ Upload files
            </Button>
            <Button disabled={selectedId === null} onClick={() => setDialog('link')}>
              + Add a link
            </Button>
            <Button disabled={selectedId === null} onClick={() => setDialog('folder')}>
              + New folder
            </Button>
          </>
        }
      >
        Files upload to the server. Links to SharePoint or Google Drive sit in the same folders.
      </PageHead>

      <LiveRegion message={message} />
      {problem ? <ErrorBanner>{problem}</ErrorBanner> : null}
      {contents.error ? <ErrorBanner>{contents.error.message}</ErrorBanner> : null}

      <div className={styles.split}>
        <div className={styles.pane}>
          <div className={styles.paneHead}>Folders</div>
          <div className={styles.tree} role="tree" aria-label="Folders" onKeyDown={onTreeKeyDown}>
            {rows.map(({ node, level, position, siblings }) => (
              <div
                key={node.id}
                ref={(element) => {
                  if (element) nodeRefs.current.set(node.id, element)
                  else nodeRefs.current.delete(node.id)
                }}
                role="treeitem"
                aria-level={level}
                aria-posinset={position}
                aria-setsize={siblings}
                aria-selected={selectedId === node.id}
                aria-expanded={node.children.length ? expanded.has(node.id) : undefined}
                tabIndex={focusedId === node.id ? 0 : -1}
                className={[
                  styles.node,
                  selectedId === node.id && styles.on,
                  expanded.has(node.id) && styles.open,
                ]
                  .filter(Boolean)
                  .join(' ')}
                // Depth is drawn rather than nested: a flat list of rows is
                // what the arrow keys walk, and aria-level carries the shape.
                style={{ paddingLeft: 10 + (level - 1) * 16 }}
                onClick={() => open(node.id)}
                onFocus={() => setFocused(node.id)}
              >
                <span className={styles.caret} aria-hidden="true">
                  {node.children.length ? '▶' : ''}
                </span>
                {FOLDER_ICON}
                <span>{node.name}</span>
              </div>
            ))}
          </div>
          <DropZone
            here={here}
            busy={upload.isPending}
            onFiles={(files) => upload.mutate({ files, addedBy: null })}
          />
        </div>

        <div className={styles.pane}>
          <div className={styles.paneHead}>
            <span className={styles.crumbs}>{crumbs.join(' / ')}</span>
            <span className={styles.tally}>
              {folders.length} folders · {items.length} files
            </span>
          </div>
          <div className={styles.scroll}>
            {folders.length + items.length === 0 ? (
              <div className={styles.blank}>
                This folder is empty.
                <span className={styles.hint}>Upload a file, add a link, or make a folder.</span>
              </div>
            ) : (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Source</th>
                    <th>Size</th>
                    <th>Added by</th>
                    <th>Date</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {folders.map((folder) => (
                    <FolderRow
                      key={folder.id}
                      id={folder.id}
                      name={folder.name}
                      onOpen={open}
                      onDelete={() =>
                        confirmDelete(`"${folder.name}" and everything in it`, () =>
                          removeFolder.mutate({ id: folder.id, name: folder.name }),
                        )
                      }
                    />
                  ))}
                  {items.map((item) => (
                    <ItemRow
                      key={item.id}
                      item={item}
                      onDelete={() =>
                        confirmDelete(`"${item.name}"`, () =>
                          removeItem.mutate({ id: item.id, name: item.name }),
                        )
                      }
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {dialog === 'folder' && selectedId !== null ? (
        <NewFolderDialog
          projectKey={projectKey}
          parentId={selectedId}
          parentName={here}
          onDone={added}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog === 'upload' && selectedId !== null ? (
        <UploadDialog
          projectKey={projectKey}
          folderName={here}
          busy={upload.isPending}
          onUpload={(files, addedBy) => upload.mutate({ files, addedBy })}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog === 'link' && selectedId !== null ? (
        <LinkDialog
          projectKey={projectKey}
          folderId={selectedId}
          onDone={added}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  )
}

/** The folder pane doubles as a drop target, as the mock promises it does. */
function DropZone({
  here,
  busy,
  onFiles,
}: {
  here: string
  busy: boolean
  onFiles: (files: File[]) => void
}) {
  const [over, setOver] = useState(false)

  function accept(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setOver(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length) onFiles(files)
  }

  return (
    <div
      className={`${styles.drop} ${over ? styles.dropActive : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={accept}
    >
      {busy ? (
        'Uploading…'
      ) : (
        <>
          Drop files here to upload into <b>{here}</b>
        </>
      )}
    </div>
  )
}

function FolderRow({
  id,
  name,
  onOpen,
  onDelete,
}: {
  id: string
  name: string
  onOpen: (id: string) => void
  onDelete: () => void
}) {
  return (
    // The whole row is clickable for the mouse, but a `tr` is not something
    // the keyboard can reach, so the name is a real button and that is what
    // Tab lands on. Both do the same thing.
    <tr className={styles.folderRow} onClick={() => onOpen(id)}>
      <td>
        <button
          type="button"
          className={`${styles.name} ${styles.openFolder}`}
          onClick={(event) => {
            event.stopPropagation()
            onOpen(id)
          }}
        >
          <span className={`${styles.badge} ${styles.folder}`} aria-hidden="true">
            ▸
          </span>
          {name}
        </button>
      </td>
      <td className={styles.muted}>Folder</td>
      <td className={styles.mono}>—</td>
      <td className={styles.muted}>—</td>
      <td className={styles.mono}>—</td>
      <td>
        <div className={styles.actions}>
          <GhostAction
            onClick={(event) => {
              event.stopPropagation()
              onDelete()
            }}
          >
            Delete
          </GhostAction>
        </div>
      </td>
    </tr>
  )
}

function ItemRow({ item, onDelete }: { item: FileItem; onDelete: () => void }) {
  const isLink = item.kind === 'link'
  const badge = isLink ? { kind: 'link', label: '↗' } : badgeOf(item.name)

  return (
    <tr>
      <td>
        <div className={styles.name}>
          <span className={`${styles.badge} ${styles[badge.kind]}`}>{badge.label}</span>
          <span>
            {item.name}
            {item.url ? <div className={styles.url}>{item.url}</div> : null}
          </span>
        </div>
      </td>
      <td className={styles.muted}>{SOURCE_LABELS[item.source]}</td>
      <td className={styles.mono}>{formatSize(item.size)}</td>
      <td>
        {item.added_by ? (
          <span className={styles.who}>
            <Avatar name={item.added_by.name} colour={item.added_by.colour} />
            {item.added_by.name}
          </span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>
      <td className={styles.mono}>{formatStamp(item.created_at)}</td>
      <td>
        <div className={styles.actions}>
          {isLink && item.url ? (
            <a
              className={`${cardStyles.button} ${cardStyles.ghost} ${cardStyles.small} ${styles.action}`}
              href={item.url}
              target="_blank"
              rel="noreferrer"
            >
              Open
            </a>
          ) : (
            <a
              className={`${cardStyles.button} ${cardStyles.ghost} ${cardStyles.small} ${styles.action}`}
              href={api.downloadUrl(item.id)}
              download={item.name}
            >
              Download
            </a>
          )}
          <GhostAction onClick={() => onDelete()}>Delete</GhostAction>
        </div>
      </td>
    </tr>
  )
}

function GhostAction({
  onClick,
  children,
}: {
  onClick: (event: MouseEvent<HTMLButtonElement>) => void
  children: ReactNode
}) {
  return (
    <Button variant="ghost" small danger onClick={onClick}>
      {children}
    </Button>
  )
}

function NewFolderDialog({
  projectKey,
  parentId,
  parentName,
  onDone,
  onClose,
}: {
  projectKey: string
  parentId: string
  parentName: string
  onDone: (created: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')

  const create = useMutation({
    mutationFn: () => api.createFolder(projectKey, { name, parent_id: parentId }),
    onSuccess: async (folder) => {
      await onDone(`Folder ${folder.name}`)
      onClose()
    },
  })

  return (
    <Modal
      title="New folder"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={create.isPending || !name.trim()}
            onClick={() => create.mutate()}
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {create.error ? <ErrorBanner>{create.error.message}</ErrorBanner> : null}
        <Field label="Folder name" required hint={`It will sit inside ${parentName}.`}>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
      </ModalBody>
    </Modal>
  )
}

function UploadDialog({
  projectKey,
  folderName,
  busy,
  onUpload,
  onClose,
}: {
  projectKey: string
  folderName: string
  busy: boolean
  onUpload: (files: File[], addedBy: string | null) => void
  onClose: () => void
}) {
  const [chosen, setChosen] = useState<File[]>([])
  const [addedBy, setAddedBy] = useState('')
  const members = useMembers(projectKey)

  return (
    <Modal
      title="Upload files"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={busy || chosen.length === 0}
            onClick={() => {
              onUpload(chosen, addedBy || null)
              onClose()
            }}
          >
            {busy ? 'Uploading…' : `Upload ${chosen.length || ''}`.trim()}
          </Button>
        </>
      }
    >
      <ModalBody>
        <Field label="Files" required hint={`Stored on the server, inside ${folderName}.`}>
          <input
            type="file"
            multiple
            onChange={(event) => setChosen(Array.from(event.target.files ?? []))}
          />
        </Field>
        <Field label="Added by" hint="Optional — an API token is not a person.">
          <select value={addedBy} onChange={(event) => setAddedBy(event.target.value)}>
            <option value="">Nobody in particular</option>
            {members.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
          </select>
        </Field>
      </ModalBody>
    </Modal>
  )
}

const EMPTY_LINK: LinkInput = { name: '', url: '', source: 'sharepoint' }

function LinkDialog({
  projectKey,
  folderId,
  onDone,
  onClose,
}: {
  projectKey: string
  folderId: string
  onDone: (created: string) => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<LinkInput>(EMPTY_LINK)
  const members = useMembers(projectKey)

  const add = useMutation({
    mutationFn: () => api.addLink(folderId, form),
    onSuccess: async (item) => {
      await onDone(`Link ${item.name}`)
      onClose()
    },
  })

  return (
    <Modal
      title="Add a link"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={add.isPending || !form.name.trim() || !form.url.trim()}
            onClick={() => add.mutate()}
          >
            {add.isPending ? 'Adding…' : 'Add link'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {add.error ? <ErrorBanner>{add.error.message}</ErrorBanner> : null}
        <Field label="Label" required>
          <input
            value={form.name}
            placeholder="What is it?"
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </Field>
        <Field label="URL" required>
          <input
            value={form.url}
            placeholder="https://…"
            onChange={(event) => setForm({ ...form, url: event.target.value })}
          />
        </Field>
        <FieldPair>
          <Field label="Source">
            <select
              value={form.source}
              onChange={(event) => setForm({ ...form, source: event.target.value as ItemSource })}
            >
              <option value="sharepoint">SharePoint</option>
              <option value="gdrive">Google Drive</option>
              <option value="other">Other</option>
            </select>
          </Field>
          <Field label="Added by">
            <select
              value={form.added_by ?? ''}
              onChange={(event) => setForm({ ...form, added_by: event.target.value || null })}
            >
              <option value="">Nobody in particular</option>
              {members.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                </option>
              ))}
            </select>
          </Field>
        </FieldPair>
      </ModalBody>
    </Modal>
  )
}

/** Who can be credited with adding something — the project's own people. */
function useMembers(projectKey: string): Person[] {
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })
  return members.data?.members ?? []
}
