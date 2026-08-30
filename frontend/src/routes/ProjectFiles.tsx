/**
 * A project's files.
 *
 * A folder tree on the left, what the selected folder holds on the right.
 * Uploads and links share the table because they answer the same question —
 * "where is the thing about X?" — and it matters far less whether the bytes are
 * ours than whether you can find them.
 *
 * A project's root holds folders but no files: the API gives every item a
 * folder, so the actions that add one wait until a folder is chosen.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState, type DragEvent, type MouseEvent, type ReactNode } from 'react'
import {
  api,
  type FileItem,
  type FolderNode,
  type ItemSource,
  type LinkInput,
  type Person,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { Avatar, Button, ErrorBanner, cardStyles } from '../components/ui'
import styles from './ProjectFiles.module.css'

const FOLDER_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
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

const UNITS = ['bytes', 'KB', 'MB', 'GB', 'TB']

function formatSize(bytes: number | null): string {
  if (bytes === null) return '—'
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${unit === 0 ? size : size.toFixed(size < 10 ? 1 : 0)} ${UNITS[unit]}`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Every folder id from the tree root down to this one. */
function trailTo(nodes: FolderNode[], target: string): string[] {
  for (const node of nodes) {
    if (node.id === target) return [node.id]
    const below = trailTo(node.children, target)
    if (below.length) return [node.id, ...below]
  }
  return []
}

export function ProjectFiles() {
  const { projectKey } = useParams({ from: '/p/$projectKey/files' })
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [dialog, setDialog] = useState<'upload' | 'link' | 'folder' | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const project = useQuery({
    queryKey: ['project', projectKey],
    queryFn: () => api.getProject(projectKey),
  })
  const tree = useQuery({
    queryKey: ['folder-tree', projectKey],
    queryFn: () => api.getTree(projectKey),
  })
  const contents = useQuery({
    queryKey: ['folder-children', selected],
    queryFn: () => api.getFolderChildren(selected as string),
    enabled: selected !== null,
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['folder-tree', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['folder-children'] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const upload = useMutation({
    mutationFn: async ({ files, addedBy }: { files: File[]; addedBy: string | null }) => {
      if (selected === null) throw new Error('Pick a folder to upload into first.')
      // One at a time: two uploads of the same name in one folder race for it,
      // and the loser's 409 is clearer when it is the only thing that failed.
      for (const file of files) await api.uploadFile(selected, file, addedBy)
    },
    onMutate: () => setProblem(null),
    onSuccess: refresh,
    onError: (error: Error) => setProblem(error.message),
  })

  const removeItem = useMutation({
    mutationFn: (id: string) => api.deleteItem(id),
    onSuccess: refresh,
    onError: (error: Error) => setProblem(error.message),
  })

  const removeFolder = useMutation({
    mutationFn: (id: string) => api.deleteFolder(id),
    onSuccess: refresh,
    onError: (error: Error) => setProblem(error.message),
  })

  // Deleting takes the content off the server's disk, and nothing here undoes
  // it — the one place in Cylist where a stray click really does lose data.
  function confirmDelete(what: string, then: (id: string) => void, id: string) {
    if (window.confirm(`Delete ${what}? This cannot be undone.`)) then(id)
  }

  function open(folderId: string) {
    setExpanded((current) => {
      const next = new Set(current)
      // Selecting a folder opens it; clicking the open one again closes it.
      if (selected === folderId && next.has(folderId)) next.delete(folderId)
      else for (const id of trailTo(tree.data ?? [], folderId)) next.add(id)
      return next
    })
    setSelected(folderId)
  }

  const nodes = tree.data ?? []
  const folders = selected === null ? nodes : (contents.data?.folders ?? [])
  const items = selected === null ? [] : (contents.data?.items ?? [])
  const here = contents.data?.folder.name ?? project.data?.name ?? projectKey
  const crumbs = [
    project.data?.name ?? projectKey,
    ...(contents.data?.path ?? []).map((c) => c.name),
  ]

  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  return (
    <>
      <PageHead
        title="Files"
        actions={
          <>
            <Button
              variant="go"
              disabled={selected === null}
              title={selected === null ? 'Pick a folder first — files live in folders.' : undefined}
              onClick={() => setDialog('upload')}
            >
              ↑ Upload files
            </Button>
            <Button
              disabled={selected === null}
              title={selected === null ? 'Pick a folder first — links live in folders.' : undefined}
              onClick={() => setDialog('link')}
            >
              + Add a link
            </Button>
            <Button onClick={() => setDialog('folder')}>+ New folder</Button>
          </>
        }
      >
        Files upload to the server. Links to SharePoint or Google Drive sit in the same folders.
      </PageHead>

      {problem ? <ErrorBanner>{problem}</ErrorBanner> : null}
      {contents.error ? <ErrorBanner>{contents.error.message}</ErrorBanner> : null}

      <div className={styles.split}>
        <div className={styles.pane}>
          <div className={styles.paneHead}>Folders</div>
          <div className={styles.tree}>
            <button
              className={`${styles.node} ${selected === null ? styles.on : ''} ${styles.open}`}
              onClick={() => setSelected(null)}
            >
              <span className={styles.caret}>{nodes.length ? '▶' : ''}</span>
              {FOLDER_ICON}
              <span>{project.data?.name ?? projectKey}</span>
            </button>
            <div className={styles.kids}>
              {nodes.map((node) => (
                <Branch
                  key={node.id}
                  node={node}
                  selected={selected}
                  expanded={expanded}
                  onOpen={open}
                />
              ))}
            </div>
          </div>
          <DropZone
            here={here}
            enabled={selected !== null}
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
                <span className={styles.hint}>
                  {selected === null
                    ? 'Make a folder to start putting things in it.'
                    : 'Upload a file or add a link.'}
                </span>
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
                      onDelete={(id) =>
                        confirmDelete(
                          `"${folder.name}" and everything in it`,
                          removeFolder.mutate,
                          id,
                        )
                      }
                    />
                  ))}
                  {items.map((item) => (
                    <ItemRow
                      key={item.id}
                      item={item}
                      onDelete={(id) => confirmDelete(`"${item.name}"`, removeItem.mutate, id)}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {dialog === 'folder' ? (
        <NewFolderDialog
          projectKey={projectKey}
          parentId={selected}
          parentName={here}
          onDone={refresh}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog === 'upload' && selected !== null ? (
        <UploadDialog
          projectKey={projectKey}
          folderName={here}
          busy={upload.isPending}
          onUpload={(files, addedBy) => upload.mutate({ files, addedBy })}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog === 'link' && selected !== null ? (
        <LinkDialog
          projectKey={projectKey}
          folderId={selected}
          onDone={refresh}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  )
}

function Branch({
  node,
  selected,
  expanded,
  onOpen,
}: {
  node: FolderNode
  selected: string | null
  expanded: Set<string>
  onOpen: (id: string) => void
}) {
  const isOpen = expanded.has(node.id)
  const classes = [styles.node, selected === node.id && styles.on, isOpen && styles.open]
    .filter(Boolean)
    .join(' ')

  return (
    <div>
      <button className={classes} onClick={() => onOpen(node.id)}>
        <span className={styles.caret}>{node.children.length ? '▶' : ''}</span>
        {FOLDER_ICON}
        <span>{node.name}</span>
      </button>
      {isOpen && node.children.length ? (
        <div className={styles.kids}>
          {node.children.map((child) => (
            <Branch
              key={child.id}
              node={child}
              selected={selected}
              expanded={expanded}
              onOpen={onOpen}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** The folder pane doubles as a drop target, as the mock promises it does. */
function DropZone({
  here,
  enabled,
  busy,
  onFiles,
}: {
  here: string
  enabled: boolean
  busy: boolean
  onFiles: (files: File[]) => void
}) {
  const [over, setOver] = useState(false)

  function accept(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setOver(false)
    if (!enabled) return
    const files = Array.from(event.dataTransfer.files)
    if (files.length) onFiles(files)
  }

  return (
    <div
      className={`${styles.drop} ${over && enabled ? styles.dropActive : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={accept}
    >
      {busy ? (
        'Uploading…'
      ) : enabled ? (
        <>
          Drop files here to upload into <b>{here}</b>
        </>
      ) : (
        'Pick a folder to upload into'
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
  onDelete: (id: string) => void
}) {
  return (
    <tr className={styles.folderRow} onClick={() => onOpen(id)}>
      <td>
        <div className={styles.name}>
          <span className={`${styles.badge} ${styles.folder}`}>▸</span>
          {name}
        </div>
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
              onDelete(id)
            }}
          >
            Delete
          </GhostAction>
        </div>
      </td>
    </tr>
  )
}

function ItemRow({ item, onDelete }: { item: FileItem; onDelete: (id: string) => void }) {
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
      <td className={styles.mono}>{formatDate(item.created_at)}</td>
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
          <GhostAction onClick={() => onDelete(item.id)}>Delete</GhostAction>
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
  parentId: string | null
  parentName: string
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')

  const create = useMutation({
    mutationFn: () => api.createFolder(projectKey, { name, parent_id: parentId }),
    onSuccess: async () => {
      await onDone()
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
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<LinkInput>(EMPTY_LINK)
  const members = useMembers(projectKey)

  const add = useMutation({
    mutationFn: () => api.addLink(folderId, form),
    onSuccess: async () => {
      await onDone()
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
