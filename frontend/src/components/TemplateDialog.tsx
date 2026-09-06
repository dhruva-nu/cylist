/**
 * Task templates: what kind of card a task is, which columns its cards may
 * sit in, and the sub-stages it passes through in each.
 *
 * The dialog opens on a list of templates and steps into an editor for one —
 * a swap rather than a second modal on top of the first, so there is never
 * two Save buttons with no way to tell them apart.
 *
 * A template's stages are saved as one thing. The rows together are the
 * policy — which columns a card of this kind may sit in, and what sub-stages
 * it carries through each — and a set that wrote each row as you typed it
 * would leave a half-written policy behind every time somebody closed the
 * dialog mid-thought.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type BoardColumn, type Template } from '../api/client'
import { Field, Modal, ModalBody } from './Modal'
import { Button, EmptyState, ErrorBanner } from './ui'
import styles from './TemplateDialog.module.css'

/** Matches `Task.sub_statuses`: a stage's labels are loaded onto it verbatim. */
const MAX_SUB_STAGES = 4

/** A stage while it is being edited: the same shape, minus the column's name. */
interface DraftStage {
  column_id: string
  sub_stage_labels: string[]
  allowed_outcomes: string[]
}

export function TemplateDialog({
  projectKey,
  columns,
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  columns: BoardColumn[]
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  /** Which template is being edited — `'new'` for one that does not exist yet. */
  const [editing, setEditing] = useState<Template | 'new' | null>(null)

  const templates = useQuery({
    queryKey: ['templates', projectKey],
    queryFn: () => api.listTemplates(projectKey),
  })

  const queryClient = useQueryClient()
  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['templates', projectKey] })
    await onDone()
  }

  if (templates.error || !templates.data) {
    return (
      <Modal title="Templates" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
        <ModalBody>
          {templates.error ? <ErrorBanner>{templates.error.message}</ErrorBanner> : <p>Loading…</p>}
        </ModalBody>
      </Modal>
    )
  }

  if (editing !== null) {
    return (
      <TemplateEditor
        // Remounted per template, so an editor opened on a second one does
        // not inherit the first one's half-written stages.
        key={editing === 'new' ? 'new' : editing.id}
        projectKey={projectKey}
        template={editing === 'new' ? null : editing}
        columns={columns}
        announce={announce}
        onDone={refresh}
        onBack={() => setEditing(null)}
      />
    )
  }

  return (
    <Modal
      title="Templates"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="go" onClick={() => setEditing('new')}>
            + New template
          </Button>
        </>
      }
    >
      <ModalBody>
        {templates.data.length ? (
          <div className={styles.rows}>
            {templates.data.map((template) => (
              <div key={template.id} className={styles.row}>
                <div className={styles.rowText}>
                  <strong>{template.name}</strong>
                  <span className={styles.muted}>{summarise(template, columns)}</span>
                </div>
                <Button small variant="ghost" onClick={() => setEditing(template)}>
                  Edit
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>
            No templates yet. A card created with no template may sit in any column.
          </EmptyState>
        )}
      </ModalBody>
    </Modal>
  )
}

/** What a template amounts to on this board, in one line. */
function summarise(template: Template, columns: BoardColumn[]): string {
  if (!template.stages.length) return 'Any column — unrestricted.'
  return template.stages
    .map(
      (stage) => columns.find((column) => column.id === stage.column_id)?.name ?? stage.column_name,
    )
    .join(' → ')
}

/**
 * One template: its name, and the columns it owns — each with its own
 * ordered list of sub-stages.
 *
 * Clicking a column puts it under this template; clicking it again takes it
 * back out, which is how a column stops being part of the rule at all. A card
 * of this template gets a column's labels loaded onto its own click-through
 * progress bar the moment it lands there, and cannot leave until it is on the
 * last one.
 */
function TemplateEditor({
  projectKey,
  template,
  columns,
  announce,
  onDone,
  onBack,
}: {
  projectKey: string
  /** Null for a template being written for the first time. */
  template: Template | null
  columns: BoardColumn[]
  announce: (message: string) => void
  onDone: () => Promise<void>
  onBack: () => void
}) {
  const [name, setName] = useState(template?.name ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [stages, setStages] = useState<DraftStage[]>(
    (template?.stages ?? []).map((stage) => ({
      column_id: stage.column_id,
      sub_stage_labels: [...stage.sub_stage_labels],
      allowed_outcomes: [...stage.allowed_outcomes],
    })),
  )

  const save = useMutation({
    mutationFn: () => {
      const input = { name: name.trim(), description: description.trim(), stages }
      return template
        ? api.updateTemplate(template.id, input)
        : api.createTemplate(projectKey, input)
    },
    onSuccess: async (saved) => {
      announce(`${saved.name} saved.`)
      await onDone()
      onBack()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteTemplate(template?.id ?? ''),
    onSuccess: async () => {
      announce(`${template?.name ?? 'The template'} deleted.`)
      await onDone()
      onBack()
    },
  })

  const complete = Boolean(name.trim())
  const error = save.error ?? remove.error

  function toggleStage(columnId: string) {
    setStages((current) =>
      current.some((stage) => stage.column_id === columnId)
        ? current.filter((stage) => stage.column_id !== columnId)
        : [...current, { column_id: columnId, sub_stage_labels: [], allowed_outcomes: [] }],
    )
  }

  function setLabels(columnId: string, labels: string[]) {
    setStages((current) =>
      current.map((stage) =>
        stage.column_id === columnId ? { ...stage, sub_stage_labels: labels } : stage,
      ),
    )
  }

  /**
   * Turn one of a column's outcomes on or off for this stage.
   *
   * An empty list means all of them, so the toggles start lit and the first
   * click has to take one *out* of the full set rather than add one to
   * nothing. Choosing every one is the same rule as choosing none and is
   * stored as none, which is the form that survives an outcome being renamed;
   * choosing none at all is refused, because it would light them all straight
   * back up and read as a click that did nothing.
   */
  function toggleOutcome(columnId: string, outcome: string, all: string[]) {
    setStages((current) =>
      current.map((stage) => {
        if (stage.column_id !== columnId) return stage
        const chosen = new Set(stage.allowed_outcomes.length ? stage.allowed_outcomes : all)
        if (chosen.has(outcome)) chosen.delete(outcome)
        else chosen.add(outcome)
        if (chosen.size === 0) return stage
        // In the column's own order, so the template reads the way the board
        // draws it however the toggles were clicked.
        const next = all.filter((one) => chosen.has(one))
        return { ...stage, allowed_outcomes: next.length === all.length ? [] : next }
      }),
    )
  }

  // Board order, not the order columns were added to the template: a
  // template reads along the board, the same way a card crosses it.
  const ordered = [...stages].sort(
    (a, b) =>
      columns.findIndex((column) => column.id === a.column_id) -
      columns.findIndex((column) => column.id === b.column_id),
  )

  return (
    <Modal
      title={template ? `${template.name} · editing` : 'New template'}
      onClose={onBack}
      footer={
        <>
          {template ? (
            <Button
              variant="ghost"
              danger
              className={styles.spacer}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete
            </Button>
          ) : null}
          <Button onClick={onBack}>Back</Button>
          <Button variant="go" disabled={save.isPending || !complete} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : template ? 'Save changes' : 'Create template'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

        <Field label="Name" required>
          <input
            value={name}
            maxLength={80}
            placeholder="Hotfix"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        <Field label="Description">
          <input
            value={description}
            maxLength={500}
            placeholder="What kind of card this is (optional)"
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        <Field
          label="Where this template's cards may go"
          hint="Click a column to let this template's cards sit there — green is every column they may go. Click it again to take it back out."
        >
          <div
            className={styles.columnToggles}
            role="group"
            aria-label="Columns this template's cards may sit in"
          >
            {columns.map((column) => {
              const allowed = stages.some((stage) => stage.column_id === column.id)
              return (
                <button
                  key={column.id}
                  type="button"
                  className={`${styles.columnToggle} ${allowed ? styles.columnToggleActive : ''}`}
                  aria-pressed={allowed}
                  onClick={() => toggleStage(column.id)}
                >
                  {column.name}
                </button>
              )
            })}
          </div>

          {ordered.length ? (
            <div className={styles.stages}>
              {ordered.map((stage) => {
                const column = columns.find((candidate) => candidate.id === stage.column_id)
                return (
                  <div key={stage.column_id} className={styles.stage}>
                    <div className={styles.stageHead}>
                      <strong>{column?.name ?? 'Unknown column'}</strong>
                    </div>
                    <SubStageLabelsEditor
                      value={stage.sub_stage_labels}
                      onChange={(labels) => setLabels(stage.column_id, labels)}
                    />
                    {/* Only where the column has sections to choose between,
                        which is the board's last and only once it has been
                        divided. Choosing none is choosing all of them, the
                        same silence the columns above keep. */}
                    {column?.outcomes.length ? (
                      <div
                        className={styles.columnToggles}
                        role="group"
                        aria-label={`How ${column.name} may end for this template's cards`}
                      >
                        {column.outcomes.map((outcome) => {
                          const chosen =
                            stage.allowed_outcomes.length === 0 ||
                            stage.allowed_outcomes.includes(outcome)
                          return (
                            <button
                              key={outcome}
                              type="button"
                              className={`${styles.columnToggle} ${
                                chosen ? styles.columnToggleActive : ''
                              }`}
                              aria-pressed={chosen}
                              onClick={() =>
                                toggleOutcome(stage.column_id, outcome, column.outcomes)
                              }
                            >
                              {outcome}
                            </button>
                          )
                        })}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyState>
              No columns yet. Click one above — cards of this template go anywhere until you do.
            </EmptyState>
          )}
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * The sub-stages a column carries, in order: added, removed, never reordered.
 *
 * Order here is the order a card meets them crossing this column — the same
 * order the click-through progress bar shows them in — so appending is the
 * only way to add one. Capped at `MAX_SUB_STAGES`, same as the field it feeds.
 */
function SubStageLabelsEditor({
  value,
  onChange,
}: {
  value: string[]
  onChange: (value: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const full = value.length >= MAX_SUB_STAGES

  function add() {
    const label = draft.trim()
    if (!label || full) return
    onChange([...value, label])
    setDraft('')
  }

  return (
    <div className={styles.checklistEditor}>
      {value.map((label, index) => (
        <div key={index} className={styles.checklistRow}>
          <span className={styles.checklistTitle}>{label}</span>
          <Button
            variant="ghost"
            small
            aria-label={`Remove "${label}"`}
            onClick={() => onChange(value.filter((_, position) => position !== index))}
          >
            ×
          </Button>
        </div>
      ))}
      {full ? (
        <span className={styles.muted}>Up to {MAX_SUB_STAGES} sub-stages here.</span>
      ) : (
        <div className={styles.composer}>
          <input
            value={draft}
            maxLength={60}
            placeholder="Add a sub-stage…"
            aria-label="Add a sub-stage"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                add()
              }
            }}
          />
          <Button small disabled={!draft.trim()} onClick={add}>
            Add
          </Button>
        </div>
      )}
    </div>
  )
}
