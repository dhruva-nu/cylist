/** The roles screen's dialogs: inventing a role, and ending the whole project. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type RoleSummary } from '../../api/client'
import { queryKeys } from '../../api/queries'
import { DialogActions, Field, Modal, ModalBody } from '../../components/Modal'
import { Button, ErrorNotice, cardStyles } from '../../components/ui'
import { confirms } from './projectDelete'
import styles from './ProjectRoles.module.css'
import { nameIsTaken } from './projectRoles'

/**
 * The end of a project, offered where the rest of its settings are.
 *
 * On this screen rather than on the grid of projects, and only to an admin,
 * for the reason the server only admits one: a project is a board, a file
 * store and a vault, and the gesture that ends all three should not be a hover
 * menu on a tile somebody is trying to click into.
 *
 * Archiving is the answer to almost every reason somebody arrives here, so it
 * is named rather than left to be discovered — most projects people want gone
 * are projects they want out of the way.
 */
export function DeleteProject({ onOpen }: { onOpen: () => void }) {
  return (
    <section className={`${cardStyles.card} ${styles.ending}`}>
      <div>
        <h2>Delete this project</h2>
        <p>
          Its board and every card on it, its files, its docs and its vault go with it. Nothing here
          comes back. Only the activity log keeps a record that it existed.
        </p>
      </div>
      <Button danger onClick={onOpen}>
        Delete project
      </Button>
    </section>
  )
}

/** Naming the project, which is what turns the button on — and what the server asks for. */
export function DeleteProjectDialog({
  projectKey,
  onClose,
}: {
  projectKey: string
  onClose: () => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [typed, setTyped] = useState('')
  const named = confirms(projectKey, typed)

  const remove = useMutation({
    mutationFn: () => api.deleteProject(projectKey, typed.trim()),
    onSuccess: async () => {
      // Away from the project before the cache is cleared: every query on this
      // screen is about a project that is no longer there, and a refetch would
      // paint the page with 404s on the way out.
      await navigate({ to: '/' })
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects })
    },
  })

  return (
    <Modal
      title={`Delete ${projectKey}`}
      onClose={onClose}
      footer={
        <DialogActions
          danger
          submitLabel={`Delete ${projectKey}`}
          pendingLabel="Deleting…"
          pending={remove.isPending}
          disabled={!named}
          onSubmit={() => remove.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={remove.error} />
        <p className={styles.warning}>
          This deletes the board and every card on it, the files and what was uploaded, the docs,
          and the vault with every secret in it. It cannot be undone. To put the project away
          without losing any of that, archive it instead.
        </p>
        <Field label={`Type ${projectKey} to confirm`} required>
          <input
            value={typed}
            autoFocus
            spellCheck={false}
            aria-invalid={typed.trim() !== '' && !named}
            onChange={(event) => setTyped(event.target.value)}
            placeholder={projectKey}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/** Invent a role for this board. It starts with what the baseline allows. */
export function AddRoleDialog({
  projectKey,
  roles,
  onSaved,
  onDone,
  onClose,
}: {
  projectKey: string
  roles: RoleSummary[]
  onSaved: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const add = useMutation({
    mutationFn: () =>
      api.createRole(projectKey, { name: name.trim(), description: description.trim() }),
    onSuccess: async (role) => {
      await onDone()
      onSaved(`${role.name} is now a role on this project.`)
      onClose()
    },
  })

  const taken = nameIsTaken(roles, name)
  const complete = name.trim() !== '' && !taken

  return (
    <Modal
      title="Add a role"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Add role"
          pendingLabel="Adding…"
          pending={add.isPending}
          disabled={!complete}
          onSubmit={() => add.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={add.error} />
        <Field label="Name" required>
          <input
            value={name}
            maxLength={40}
            placeholder="QA"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        {taken ? <p className={styles.clash}>This project already has a {name.trim()}.</p> : null}
        <Field label="What it means here" hint="Optional.">
          <input value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
      </ModalBody>
    </Modal>
  )
}
