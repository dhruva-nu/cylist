import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { usePeople } from '../../api/queries'
import { DialogActions, Modal, ModalBody } from '../../components/Modal'
import { Avatar, ErrorNotice } from '../../components/ui'
import styles from './ProjectPeople.module.css'

/** Pick who from the directory is on this project. */
export function DirectoryDialog({
  projectKey,
  currentMemberIds,
  onSaved,
  onDone,
  onClose,
}: {
  projectKey: string
  currentMemberIds: string[]
  onSaved: (count: number) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [selected, setSelected] = useState<string[]>(currentMemberIds)
  const directory = usePeople()

  const save = useMutation({
    mutationFn: () => api.setMembers(projectKey, selected),
    onSuccess: async (result) => {
      await onDone()
      onSaved(result.members.length)
      onClose()
    },
  })

  function toggle(personId: string) {
    setSelected((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    )
  }

  return (
    <Modal
      title="Who is on this project?"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Save members"
          pendingLabel="Saving…"
          pending={save.isPending}
          onSubmit={() => save.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error} />
        {directory.isPending ? <p>Loading the directory…</p> : null}
        {directory.data?.length === 0 ? (
          <p>
            The directory is empty. Close this and use <strong>Add a person</strong> instead.
          </p>
        ) : null}
        {directory.data?.length ? (
          <div className={styles.picker}>
            {directory.data.map((person) => (
              <label key={person.id} className={styles.option}>
                <input
                  type="checkbox"
                  checked={selected.includes(person.id)}
                  onChange={() => toggle(person.id)}
                />
                <Avatar name={person.name} colour={person.colour} />
                <span className={styles.optionText}>
                  <b>{person.name}</b>
                  <span>
                    {person.kind} · {person.title}
                  </span>
                </span>
              </label>
            ))}
          </div>
        ) : null}
      </ModalBody>
    </Modal>
  )
}
