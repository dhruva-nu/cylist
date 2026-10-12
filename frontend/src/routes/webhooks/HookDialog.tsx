/** Adding a hook or editing one: its name and URL, what it fires on, and its filters. */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type BoardColumn,
  type Hook,
  type HookEvent,
  type TaskType,
  type Template,
} from '../../api/client'
import { DialogActions, Field, FieldPair, Modal, ModalBody } from '../../components/Modal'
import { ErrorNotice } from '../../components/ui'
import styles from './ProjectHooks.module.css'
import {
  EMPTY_DRAFT,
  draftOf,
  groupEvents,
  inputOf,
  pickedIn,
  whyIncomplete,
  withVerbs,
  type HookDraft,
} from './projectHooks'

const TYPES: { value: TaskType; label: string }[] = [
  { value: 'feature', label: 'Feature' },
  { value: 'bug', label: 'Bug' },
  { value: 'chore', label: 'Chore' },
]

export function HookDialog({
  projectKey,
  hook,
  hooks,
  events,
  columns,
  templates,
  onClose,
  onSaved,
}: {
  projectKey: string
  hook: Hook | null
  hooks: Hook[]
  events: HookEvent[]
  columns: BoardColumn[]
  templates: Template[]
  onClose: () => void
  onSaved: (saved: Hook, secret: string | null) => Promise<void>
}) {
  const [draft, setDraft] = useState<HookDraft>(hook ? draftOf(hook) : EMPTY_DRAFT)
  const set = (patch: Partial<HookDraft>) => setDraft((current) => ({ ...current, ...patch }))

  const save = useMutation({
    mutationFn: async () => {
      if (hook === null) {
        const created = await api.createHook(projectKey, inputOf(draft, true))
        return { saved: created, secret: created.secret }
      }
      return {
        saved: await api.updateHook(projectKey, hook.id, inputOf(draft, false)),
        secret: null,
      }
    },
    // A new hook's secret comes back in this response and is handed straight
    // to the dialog that shows it once; nothing else should hold on to it.
    gcTime: 0,
    onSuccess: ({ saved, secret }) => onSaved(saved, secret),
  })

  const blocked = whyIncomplete(draft, hooks, hook)
  const moveOnly = !draft.anyChange && draft.verbs.length === 1 && draft.verbs[0] === 'task.moved'

  return (
    <Modal
      title={hook ? `Edit ${hook.name}` : 'Add a hook'}
      onClose={onClose}
      wide
      footer={
        <DialogActions
          note={
            blocked && draft.name !== '' ? <span className={styles.blocked}>{blocked}</span> : null
          }
          submitLabel={hook ? 'Save' : 'Add hook'}
          pendingLabel="Saving…"
          pending={save.isPending}
          disabled={blocked !== null}
          onSubmit={() => save.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error} />
        <FieldPair>
          <Field label="Name" required>
            <input
              value={draft.name}
              maxLength={80}
              placeholder="Hotfix to staging"
              onChange={(event) => set({ name: event.target.value })}
            />
          </Field>
          <Field label="URL" required hint="Where each event is POSTed.">
            <input
              value={draft.url}
              type="url"
              placeholder="https://ci.example.com/cylist"
              onChange={(event) => set({ url: event.target.value })}
            />
          </Field>
        </FieldPair>

        <EventPicker events={events} draft={draft} onChange={set} />

        <p className={styles.quiet}>Only for cards that match every filter you set:</p>
        <FieldPair>
          <Field label={moveOnly ? 'Moved into' : 'In column'}>
            <select
              value={draft.toColumnId}
              onChange={(event) => set({ toColumnId: event.target.value })}
            >
              <option value="">Any column</option>
              {columns.map((column) => (
                <option key={column.id} value={column.id}>
                  {column.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Moved from">
            <select
              value={draft.fromColumnId}
              onChange={(event) => set({ fromColumnId: event.target.value })}
            >
              <option value="">Any column</option>
              {columns.map((column) => (
                <option key={column.id} value={column.id}>
                  {column.name}
                </option>
              ))}
            </select>
          </Field>
        </FieldPair>
        <FieldPair>
          <Field label="Template">
            <select
              value={draft.templateId}
              onChange={(event) => set({ templateId: event.target.value })}
            >
              <option value="">Any template</option>
              {templates.map((template) => (
                <option key={template.id} value={template.id}>
                  {template.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Type">
            <select
              value={draft.taskType}
              onChange={(event) => set({ taskType: event.target.value as HookDraft['taskType'] })}
            >
              <option value="">Any type</option>
              {TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </Field>
        </FieldPair>

        {hook === null ? (
          <Field
            label="Signing secret"
            hint="Leave empty to have one made. Shown once, after saving."
          >
            <input
              value={draft.secret}
              autoComplete="off"
              onChange={(event) => set({ secret: event.target.value })}
            />
          </Field>
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/**
 * Which changes a hook fires on: everything, or the events ticked here.
 *
 * Grouped, because twenty-two checkboxes in a wall is a list nobody reads:
 * the groups down the left say what kinds of change there are, and one
 * group's events — each with a line on when it happens — fill the right. The
 * count beside a group is how many of its events are ticked, so a choice made
 * in a group not on screen is still in sight.
 */
/** Every event a group of the picker offers, by verb. */
function verbsOf(group: { events: HookEvent[] }): string[] {
  return group.events.map((event) => event.verb)
}

function EventPicker({
  events,
  draft,
  onChange,
}: {
  events: HookEvent[]
  draft: HookDraft
  onChange: (patch: Partial<HookDraft>) => void
}) {
  const groups = groupEvents(events)
  // Opens on the first group with something ticked, so editing a hook starts
  // where its events are.
  const [chosenId, setChosenId] = useState<string | null>(null)
  const active =
    groups.find((group) => group.id === chosenId) ??
    groups.find((group) => pickedIn(group, draft.verbs) > 0) ??
    groups[0]
  const tick = (verbs: string[], on: boolean) =>
    onChange({ verbs: withVerbs(draft.verbs, verbs, on, events) })

  return (
    <fieldset className={styles.when}>
      <legend className={styles.whenLegend}>When</legend>
      <div className={styles.mode} role="group" aria-label="Which changes">
        <button
          type="button"
          className={draft.anyChange ? `${styles.seg} ${styles.segOn}` : styles.seg}
          aria-pressed={draft.anyChange}
          onClick={() => onChange({ anyChange: true })}
        >
          Any change
        </button>
        <button
          type="button"
          className={draft.anyChange ? styles.seg : `${styles.seg} ${styles.segOn}`}
          aria-pressed={!draft.anyChange}
          onClick={() => onChange({ anyChange: false })}
        >
          Only these events
        </button>
      </div>

      {draft.anyChange ? (
        <p className={styles.quiet}>
          Every card, column, template and goal change is sent. Narrow it with the filters below.
        </p>
      ) : active === undefined ? (
        <p className={styles.quiet}>Loading events…</p>
      ) : (
        <div className={styles.picker}>
          <div className={styles.groups}>
            {groups.map((group) => {
              const picked = pickedIn(group, draft.verbs)
              const current = group.id === active.id
              return (
                <button
                  key={group.id}
                  type="button"
                  className={current ? `${styles.group} ${styles.groupOn}` : styles.group}
                  aria-current={current ? 'true' : undefined}
                  onClick={() => setChosenId(group.id)}
                >
                  <span>{group.name}</span>
                  <span
                    className={picked > 0 ? `${styles.badge} ${styles.badgeOn}` : styles.badge}
                    aria-label={`${picked} of ${group.events.length} ticked`}
                  >
                    {picked > 0 ? `${picked}/${group.events.length}` : group.events.length}
                  </span>
                </button>
              )
            })}
          </div>
          <div className={styles.groupPane}>
            <div className={styles.groupHead}>
              <h3>{active.name}</h3>
              <div className={styles.bulk}>
                <button
                  type="button"
                  className={styles.linkish}
                  onClick={() => tick(verbsOf(active), true)}
                >
                  All
                </button>
                <button
                  type="button"
                  className={styles.linkish}
                  onClick={() => tick(verbsOf(active), false)}
                >
                  None
                </button>
              </div>
            </div>
            {active.events.map((event) => (
              <label key={event.verb} className={styles.eventRow}>
                <input
                  type="checkbox"
                  checked={draft.verbs.includes(event.verb)}
                  onChange={(change) => tick([event.verb], change.target.checked)}
                />
                <span className={styles.eventText}>
                  <span className={styles.eventName}>{event.label}</span>
                  {event.hint ? <span className={styles.eventHint}>{event.hint}</span> : null}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}
    </fieldset>
  )
}
