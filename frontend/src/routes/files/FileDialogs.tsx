/**
 * The files screen's dialogs: a new folder, an upload, a link, and a rename.
 *
 * Each reports back with what it added rather than announcing it: the screen
 * owns the live region, and the dialog is about to stop existing.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type FileItem,
  type ItemSource,
  type LinkInput,
  type Sensitivity,
} from '../../api/client'
import { affects, invalidates, useMembers } from '../../api/queries'
import { DialogActions, Field, FieldPair, Modal, ModalBody } from '../../components/Modal'
import { ErrorNotice, LevelPicker } from '../../components/ui'
import { extensionWarning, renameIsAChange, renameProblem } from './fileRename'
import { handlesOwnErrors } from '../../api/mutationErrors'

export function NewFolderDialog({
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
  const [level, setLevel] = useState<Sensitivity | ''>('')

  const create = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.files) },
    mutationFn: () =>
      api.createFolder(projectKey, {
        name,
        parent_id: parentId,
        default_sensitivity: level || null,
      }),
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
        <DialogActions
          submitLabel="Create"
          pendingLabel="Creating…"
          pending={create.isPending}
          disabled={!name.trim()}
          onSubmit={() => create.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={create.error} />
        <Field label="Folder name" required hint={`It will sit inside ${parentName}.`}>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field
          label="What goes in here is"
          hint="Said once per folder rather than once per file. Uploads take it unless they say otherwise."
        >
          <LevelPicker
            projectKey={projectKey}
            value={level}
            onChange={setLevel}
            includeInherit={`Same as ${parentName}`}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Renaming a file or a link.
 *
 * An upload arrives named whatever it was called on the machine it came from,
 * which is how `Screenshot 2026-09-18 at 11.04.22.png` ends up being the name
 * of the thing everyone is meant to find. The name is the only handle this
 * screen gives a file — it is what the table sorts by eye, what a `>` tag in
 * a description carries, and what the download saves as — so it is the one
 * thing that has to be correctable after the fact.
 *
 * The dialog holds itself open on a failure rather than closing and reporting
 * from the page, because the failure that matters here is the folder already
 * having something with that name, and the answer to it is to type a
 * different one while the one you tried is still in the box.
 */
export function RenameDialog({
  item,
  onDone,
  onClose,
}: {
  item: FileItem
  onDone: (from: string, to: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(item.name)

  const rename = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.files) },
    mutationFn: () => api.updateItem(item.id, { name: name.trim() }),
    onSuccess: async (renamed) => {
      await onDone(item.name, renamed.name)
      onClose()
    },
  })

  const problem = renameProblem(item.name, name)
  const warning = extensionWarning(item.name, name)
  const ready = renameIsAChange(item.name, name) && problem === null

  return (
    <Modal
      title={item.kind === 'link' ? 'Rename link' : 'Rename file'}
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Rename"
          pendingLabel="Renaming…"
          pending={rename.isPending}
          disabled={!ready}
          onSubmit={() => rename.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={rename.error} />
        <Field
          label="Name"
          required
          hint={
            problem ??
            warning ??
            'The bytes, who added it and where it sits are unchanged. A > tag already written with the old name stops matching it.'
          }
        >
          <input
            value={name}
            autoFocus
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              // Enter is what a one-field dialog is for.
              if (event.key === 'Enter' && ready && !rename.isPending) rename.mutate()
            }}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Choosing files to upload, who to credit and how far they may travel.
 *
 * The upload itself belongs to the screen rather than to this dialog: it can
 * take a while, the drop target beside the tree starts the same one, and the
 * screen is what says when it has finished.
 */
export function UploadDialog({
  projectKey,
  folderName,
  busy,
  onUpload,
  onClose,
}: {
  projectKey: string
  folderName: string
  busy: boolean
  onUpload: (files: File[], addedBy: string | null, level: Sensitivity | null) => void
  onClose: () => void
}) {
  const [chosenFiles, setChosenFiles] = useState<File[]>([])
  const [addedBy, setAddedBy] = useState('')
  const [level, setLevel] = useState<Sensitivity | ''>('')

  return (
    <Modal
      title="Upload files"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel={`Upload ${chosenFiles.length || ''}`.trim()}
          pendingLabel="Uploading…"
          pending={busy}
          disabled={chosenFiles.length === 0}
          onSubmit={() => {
            onUpload(chosenFiles, addedBy || null, level || null)
            onClose()
          }}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <Field label="Files" required hint={`Stored on the server, inside ${folderName}.`}>
          <input
            type="file"
            multiple
            onChange={(event) => setChosenFiles(Array.from(event.target.files ?? []))}
          />
        </Field>
        <Field label="Added by" hint="Optional — an API token is not a person.">
          <AddedBySelect projectKey={projectKey} value={addedBy} onChange={setAddedBy} />
        </Field>
        <Field
          label="How far it may travel"
          hint="A role not cleared this high is not shown that the file exists."
        >
          <LevelPicker
            projectKey={projectKey}
            value={level}
            onChange={setLevel}
            includeInherit={`Same as ${folderName}`}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

const EMPTY_LINK: LinkInput = { name: '', url: '', source: 'sharepoint' }

export function LinkDialog({
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

  const add = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.files) },
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
        <DialogActions
          submitLabel="Add link"
          pendingLabel="Adding…"
          pending={add.isPending}
          disabled={!form.name.trim() || !form.url.trim()}
          onSubmit={() => add.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={add.error} />
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
            <AddedBySelect
              projectKey={projectKey}
              value={form.added_by ?? ''}
              onChange={(personId) => setForm({ ...form, added_by: personId || null })}
            />
          </Field>
        </FieldPair>
        <Field label="How far it may travel">
          <LevelPicker
            projectKey={projectKey}
            value={form.sensitivity ?? ''}
            onChange={(level) => setForm({ ...form, sensitivity: level || null })}
            includeInherit="Same as this folder"
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Who to credit with adding something — the project's own people, or nobody.
 *
 * The value is a person's id, or empty for nobody in particular.
 */
function AddedBySelect({
  projectKey,
  value,
  onChange,
}: {
  projectKey: string
  value: string
  onChange: (personId: string) => void
}) {
  const members = useMembers(projectKey)

  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">Nobody in particular</option>
      {(members.data?.members ?? []).map((person) => (
        <option key={person.id} value={person.id}>
          {person.name}
        </option>
      ))}
    </select>
  )
}
