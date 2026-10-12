/**
 * The vault's dialogs: a new tree, a node made or edited, and a node moved.
 *
 * Each reports back with the name of what it changed rather than announcing
 * it: the screen owns the live region, and the dialog is about to stop
 * existing.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type SecretInput,
  type Sensitivity,
  type VaultNode,
  type VaultNodeKind,
  type VaultTreeDetail,
} from '../../api/client'
import { DialogActions, Field, FieldPair, Modal, ModalBody } from '../../components/Modal'
import { SegmentedChoice } from '../../components/SegmentedChoice'
import { ErrorNotice, LevelPicker } from '../../components/ui'
import { branchesOutside, type Destination } from './vaultTree'

export function TreeDialog({
  projectKey,
  onDone,
  onClose,
}: {
  projectKey: string
  onDone: (name: string) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [level, setLevel] = useState<Sensitivity | ''>('')

  const save = useMutation({
    mutationFn: () => api.createVaultTree(projectKey, name.trim(), level || null),
    onSuccess: async () => {
      await onDone(name.trim())
      onClose()
    },
  })

  return (
    <Modal
      title="New tree"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Create tree"
          pendingLabel="Creating…"
          pending={save.isPending}
          disabled={!name.trim()}
          onSubmit={() => save.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error} />
        <Field label="Tree name" required>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Cloud accounts"
          />
        </Field>
        <Field
          label="What goes in here is"
          hint="A tree is how a vault is already divided, so this is where it is said once."
        >
          <LevelPicker
            projectKey={projectKey}
            value={level}
            onChange={setLevel}
            includeInherit="Internal"
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

/** The form's own shape: every field a string, so the inputs stay controlled.
 * Blanks are turned back into nulls on the way to the API. */
interface SecretForm {
  value: string
  username: string
  url: string
  notes: string
}

/** The secret's own fields, in the shape the API takes them: blanks sent as nulls. */
function secretInputFrom(form: SecretForm): SecretInput {
  return {
    username: form.username.trim() || null,
    url: form.url.trim() || null,
    notes: form.notes,
    ...(form.value ? { value: form.value } : {}),
  }
}

function nodeDialogTitle(editing: boolean, kind: VaultNodeKind): string {
  if (editing) return 'Edit entry'
  return kind === 'secret' ? 'New secret' : 'New node'
}

/**
 * Creates a node, or edits one.
 *
 * Editing never shows the stored credential — the form has no way to read it
 * — so the value field means "replace it with this", and leaving it blank
 * keeps whatever is there.
 */
export function NodeDialog({
  projectKey,
  node,
  destination,
  onCreated,
  onDone,
  onClose,
}: {
  projectKey: string
  node?: VaultNode
  destination: Destination
  /** What was just added, so the screen can open the tree onto it. */
  onCreated?: (created: VaultNode) => void
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
  const [level, setLevel] = useState<Sensitivity | ''>(node?.sensitivity ?? '')

  const save = useMutation({
    mutationFn: async () => {
      const secret = secretInputFrom(form)
      if (node) {
        await api.updateVaultNode(node.id, {
          name: name.trim(),
          ...(level ? { sensitivity: level } : {}),
          ...(node.kind === 'secret' ? { secret } : {}),
        })
        return null
      }
      return api.createVaultNode({
        tree_id: destination.treeId,
        parent_id: destination.parentId,
        name: name.trim(),
        kind,
        sensitivity: level || null,
        ...(kind === 'secret' ? { secret: { ...secret, value: form.value } } : {}),
      })
    },
    // The request carried the credential typed into the form; there is no
    // reason for react-query to keep holding it once the dialog has gone.
    gcTime: 0,
    onSuccess: async (created) => {
      // Before the refetch, so the tree is already open on the new node by
      // the time it arrives rather than adding it somewhere out of sight.
      if (created) onCreated?.(created)
      await onDone(name.trim())
      onClose()
    },
  })

  const complete = name.trim() !== '' && (editing || kind === 'branch' || form.value !== '')

  return (
    <Modal
      title={nodeDialogTitle(editing, kind)}
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel={editing ? 'Save' : 'Add'}
          pendingLabel="Saving…"
          pending={save.isPending}
          disabled={!complete}
          onSubmit={() => save.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error} />

        <Field label="Name" required>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>

        {/* Hidden when the caller already said which — "+ Secret" means secret. */}
        {editing || destination.kind ? null : (
          <Field label="Kind">
            <SegmentedChoice label="Kind" options={KINDS} value={kind} onChange={setKind} />
          </Field>
        )}

        <Field
          label="How far it may travel"
          hint={
            kind === 'branch'
              ? 'A branch above somebody’s clearance hides everything under it.'
              : 'A role not cleared this high is not shown that it exists.'
          }
        >
          {/* Editing offers no "same as where it goes": the node is already
              somewhere, and its level is already said. */}
          <LevelPicker
            projectKey={projectKey}
            value={level}
            onChange={setLevel}
            includeInherit={editing ? undefined : 'Same as where it goes'}
          />
        </Field>

        {kind === 'secret' ? (
          <SecretFields form={form} editing={editing} onChange={setForm} />
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/**
 * The fields only a secret has: who it logs in as, where, the credential
 * itself, and notes.
 */
function SecretFields({
  form,
  editing,
  onChange,
}: {
  form: SecretForm
  editing: boolean
  onChange: (form: SecretForm) => void
}) {
  return (
    <>
      <FieldPair>
        <Field label="Username">
          <input
            value={form.username}
            onChange={(event) => onChange({ ...form, username: event.target.value })}
          />
        </Field>
        <Field label="URL">
          <input
            value={form.url}
            onChange={(event) => onChange({ ...form, url: event.target.value })}
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
          onChange={(event) => onChange({ ...form, value: event.target.value })}
        />
      </Field>
      <Field label="Notes">
        <textarea
          value={form.notes}
          onChange={(event) => onChange({ ...form, notes: event.target.value })}
        />
      </Field>
    </>
  )
}

/** Re-parent a node, among the branches it can legally go into. */
export function MoveDialog({
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
        <DialogActions
          submitLabel="Move"
          pendingLabel="Moving…"
          pending={save.isPending}
          onSubmit={() => save.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error} />
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
