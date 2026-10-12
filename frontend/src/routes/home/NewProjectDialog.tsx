import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api, type ProjectInput } from '../../api/client'
import { queryKeys } from '../../api/queries'
import { DialogActions, Field, Modal, ModalBody } from '../../components/Modal'
import { ErrorNotice } from '../../components/ui'

/** Starting a project: its key, its name, and what it is for. */
export function NewProjectDialog({
  onCreated,
  onClose,
}: {
  onCreated: (name: string) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ProjectInput>({ key: '', name: '', description: '' })

  const create = useMutation({
    mutationFn: (input: ProjectInput) => api.createProject(input),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      onCreated(project.name)
      onClose()
    },
  })

  const keyTaken = create.error instanceof ApiError && create.error.code === 'conflict'

  return (
    <Modal
      title="New project"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Create project"
          pendingLabel="Creating…"
          pending={create.isPending}
          disabled={!form.key || !form.name}
          onSubmit={() => create.mutate(form)}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={create.error} />
        <Field
          label="Key"
          required
          hint="2–6 letters and digits. Prefixes every task number, e.g. ATL-41."
        >
          <input
            value={form.key}
            maxLength={6}
            aria-invalid={keyTaken}
            onChange={(event) =>
              setForm({ ...form, key: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '') })
            }
            placeholder="ATL"
          />
        </Field>
        <Field label="Name" required>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Atlas Billing Migration"
          />
        </Field>
        <Field label="Description">
          <textarea
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="What is this project for?"
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}
