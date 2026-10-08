/**
 * The screen a project's admin tells the outside world about its board from.
 *
 * Every change on a board is an event — a card created, moved, commented on.
 * A hook picks the ones it wants ("a Hotfix card moved into In staging") and
 * Cylist POSTs each as signed JSON to the hook's URL, retrying while the
 * receiver is down. See `app/services/hooks.py` for the rules and
 * `app/services/hook_delivery.py` for the sending.
 *
 * **Hooks down the left, one hook's detail and its delivery log on the
 * right** — the Roles page's shape. The log is what this page is opened for
 * after the first day: "did the deploy hook fire, and what did CI say?".
 *
 * Only the project's admin can see this page's contents. A hook's URL is often
 * a capability in its own right — a CI trigger, a chat webhook — so the server
 * refuses everyone else, and this says why rather than showing an empty list.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import {
  ApiError,
  api,
  type BoardColumn,
  type Hook,
  type HookDelivery,
  type HookEvent,
  type TaskType,
  type Template,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import {
  Button,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import styles from './ProjectHooks.module.css'
import {
  EMPTY_DRAFT,
  STATE_WORDS,
  attemptTime,
  type HookDraft,
  deliveryOutcome,
  describeRule,
  draftOf,
  groupEvents,
  inputOf,
  isDangling,
  pickedIn,
  whyIncomplete,
  withVerbs,
} from './projectHooks'

export function ProjectHooks() {
  const { projectKey } = useParams({ from: '/p/$projectKey/hooks' })
  const queryClient = useQueryClient()
  const { message, announce } = useAnnouncer()
  const [chosenId, setChosenId] = useState<string | null>(null)
  const [editing, setEditing] = useState<Hook | 'new' | null>(null)
  const [shownSecret, setShownSecret] = useState<{ name: string; secret: string } | null>(null)

  const hooks = useQuery({
    queryKey: ['hooks', projectKey],
    queryFn: () => api.listHooks(projectKey),
    retry: (count, error) =>
      !(error instanceof ApiError && error.code === 'forbidden') && count < 2,
  })
  const events = useQuery({ queryKey: ['hook-events'], queryFn: api.listHookEvents })
  const board = useQuery({
    queryKey: ['board', projectKey],
    queryFn: () => api.listColumns(projectKey),
  })
  const templates = useQuery({
    queryKey: ['templates', projectKey],
    queryFn: () => api.listTemplates(projectKey),
  })

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['hooks', projectKey] })
  }

  const head = (
    <PageHead
      title="Hooks"
      actions={
        hooks.data ? (
          <Button variant="go" onClick={() => setEditing('new')}>
            Add hook
          </Button>
        ) : null
      }
    >
      Send this board&rsquo;s changes to a URL — a deploy, a chat, a script of your own. Each one is
      POSTed as signed JSON, and tried again for hours if nobody answers.
    </PageHead>
  )

  if (hooks.isPending) return <EmptyState>Loading hooks…</EmptyState>
  if (hooks.error) {
    const forbidden = hooks.error instanceof ApiError && hooks.error.code === 'forbidden'
    return (
      <>
        {head}
        {forbidden ? (
          <p className={styles.note}>
            Only an admin of {projectKey} can see or change its hooks. Ask one to add you to the
            Admin role on the Roles page.
          </p>
        ) : (
          <ErrorBanner>{hooks.error.message}</ErrorBanner>
        )}
      </>
    )
  }

  const catalogue = events.data ?? []
  const selected = hooks.data.find((hook) => hook.id === chosenId) ?? hooks.data[0]

  return (
    <>
      {head}
      <LiveRegion message={message} />
      {hooks.data.length === 0 || selected === undefined ? (
        <EmptyState>
          No hooks yet. Add one to have a change on this board — a card moved into a column, say —
          POSTed to a URL you choose.
        </EmptyState>
      ) : (
        <div className={styles.split}>
          <nav className={styles.list} aria-label="Hooks">
            {hooks.data.map((hook) => (
              <button
                key={hook.id}
                type="button"
                className={`${styles.listRow} ${hook.id === selected.id ? styles.selected : ''}`}
                aria-current={hook.id === selected.id ? 'true' : undefined}
                onClick={() => setChosenId(hook.id)}
              >
                <span
                  className={`${styles.dot} ${styles[hook.last_delivery?.state ?? 'none']} ${hook.enabled ? '' : styles.off}`}
                  aria-hidden="true"
                />
                <span className={styles.listName}>{hook.name}</span>
                {hook.enabled ? null : <span className={styles.listOff}>Off</span>}
              </button>
            ))}
          </nav>
          <HookDetail
            key={selected.id}
            projectKey={projectKey}
            hook={selected}
            events={catalogue}
            onEdit={() => setEditing(selected)}
            onSecret={(secret) => setShownSecret({ name: selected.name, secret })}
            onChanged={async (said) => {
              await refresh()
              announce(said)
            }}
          />
        </div>
      )}

      {editing !== null ? (
        <HookDialog
          projectKey={projectKey}
          hook={editing === 'new' ? null : editing}
          hooks={hooks.data}
          events={catalogue}
          columns={board.data?.columns ?? []}
          templates={templates.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={async (saved, secret) => {
            await refresh()
            setChosenId(saved.id)
            setEditing(null)
            if (secret) setShownSecret({ name: saved.name, secret })
            announce(`${saved.name} saved.`)
          }}
        />
      ) : null}

      {shownSecret !== null ? (
        <SecretDialog {...shownSecret} onClose={() => setShownSecret(null)} />
      ) : null}
    </>
  )
}

function HookDetail({
  projectKey,
  hook,
  events,
  onEdit,
  onSecret,
  onChanged,
}: {
  projectKey: string
  hook: Hook
  events: HookEvent[]
  onEdit: () => void
  onSecret: (secret: string) => void
  onChanged: (said: string) => Promise<void>
}) {
  const queryClient = useQueryClient()
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const deliveries = useQuery({
    queryKey: ['hook-deliveries', hook.id],
    queryFn: () => api.listHookDeliveries(projectKey, hook.id),
    // Retries happen in the background; a log left open should show them land.
    refetchInterval: 15_000,
  })

  async function refreshLog() {
    await queryClient.invalidateQueries({ queryKey: ['hook-deliveries', hook.id] })
  }

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.updateHook(projectKey, hook.id, { enabled }),
    onSuccess: (saved) => onChanged(`${saved.name} is ${saved.enabled ? 'on' : 'off'}.`),
  })
  const test = useMutation({
    mutationFn: () => api.testHook(projectKey, hook.id),
    onSuccess: async (sent) => {
      await refreshLog()
      await onChanged(`Test sent to ${hook.name}: ${deliveryOutcome(sent)}.`)
    },
  })
  const rotate = useMutation({
    mutationFn: () => api.rotateHookSecret(projectKey, hook.id),
    onSuccess: async (rotated) => {
      onSecret(rotated.secret)
      await onChanged(`${hook.name} has a new signing secret.`)
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteHook(projectKey, hook.id),
    onSuccess: () => onChanged(`${hook.name} deleted.`),
  })
  const redeliver = useMutation({
    mutationFn: (delivery: HookDelivery) => api.redeliverHook(projectKey, hook.id, delivery.id),
    onSuccess: async () => {
      await refreshLog()
      // The courier sends it within moments; look again once it has.
      window.setTimeout(() => void refreshLog(), 2_000)
    },
  })

  const failure = toggle.error ?? test.error ?? rotate.error ?? remove.error ?? redeliver.error

  return (
    <section className={`${cardStyles.card} ${styles.pane}`} aria-label={hook.name}>
      <div className={styles.detailHead}>
        <h2>{hook.name}</h2>
        <label className={styles.switch}>
          <input
            type="checkbox"
            checked={hook.enabled}
            disabled={toggle.isPending}
            onChange={(event) => toggle.mutate(event.target.checked)}
          />
          {hook.enabled ? 'On' : 'Off'}
        </label>
      </div>
      {failure ? <ErrorBanner>{failure.message}</ErrorBanner> : null}

      <dl className={styles.facts}>
        <dt>Fires on</dt>
        <dd>
          {describeRule(hook, events)}
          {isDangling(hook) ? (
            <span className={styles.warn}>
              {' '}
              — a filter names something deleted, so this matches nothing.
            </span>
          ) : null}
        </dd>
        <dt>Sends to</dt>
        <dd className={styles.mono}>{hook.url}</dd>
        <dt>Signed with</dt>
        <dd>
          <span className={styles.mono}>••••{hook.secret_hint}</span>{' '}
          <Button small variant="ghost" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
            {rotate.isPending ? 'Rotating…' : 'New secret'}
          </Button>
        </dd>
      </dl>

      <div className={styles.actions}>
        <Button onClick={onEdit}>Edit</Button>
        <Button disabled={test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? 'Sending…' : 'Send test'}
        </Button>
        {confirmingDelete ? (
          <>
            <Button danger disabled={remove.isPending} onClick={() => remove.mutate()}>
              Delete {hook.name} and its log
            </Button>
            <Button variant="ghost" onClick={() => setConfirmingDelete(false)}>
              Keep it
            </Button>
          </>
        ) : (
          <Button variant="ghost" danger onClick={() => setConfirmingDelete(true)}>
            Delete
          </Button>
        )}
      </div>

      <h3 className={styles.logTitle}>Recent deliveries</h3>
      {deliveries.isPending ? (
        <p className={styles.quiet}>Loading…</p>
      ) : deliveries.error ? (
        <ErrorBanner>{deliveries.error.message}</ErrorBanner>
      ) : deliveries.data.length === 0 ? (
        <p className={styles.quiet}>
          Nothing sent yet. Send a test, or make a change this hook matches.
        </p>
      ) : (
        <ul className={styles.log}>
          {deliveries.data.map((delivery) => (
            <li key={delivery.id} className={styles.delivery}>
              <details>
                <summary>
                  <span className={`${styles.state} ${styles[delivery.state]}`}>
                    {STATE_WORDS[delivery.state]}
                  </span>
                  <span className={styles.event}>{delivery.event}</span>
                  <span className={styles.when}>{attemptTime(delivery.created_at)}</span>
                  <span className={styles.outcome}>{deliveryOutcome(delivery)}</span>
                </summary>
                <div className={styles.more}>
                  {delivery.attempts.length > 0 ? (
                    <ol className={styles.attempts}>
                      {delivery.attempts.map((attempt) => (
                        <li key={attempt.at}>
                          <span className={styles.when}>{attemptTime(attempt.at)}</span>{' '}
                          {attempt.status_code ?? 'no answer'} · {attempt.duration_ms} ms
                          {attempt.error ? ` · ${attempt.error}` : ''}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  <pre className={styles.payload}>{JSON.stringify(delivery.payload, null, 2)}</pre>
                  {delivery.event !== 'hook.test' && delivery.state !== 'pending' ? (
                    <Button
                      small
                      disabled={redeliver.isPending}
                      onClick={() => redeliver.mutate(delivery)}
                    >
                      Send again
                    </Button>
                  ) : null}
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const TYPES: { value: TaskType; label: string }[] = [
  { value: 'feature', label: 'Feature' },
  { value: 'bug', label: 'Bug' },
  { value: 'chore', label: 'Chore' },
]

function HookDialog({
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
        <>
          {blocked && draft.name !== '' ? <span className={styles.blocked}>{blocked}</span> : null}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={blocked !== null || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Saving…' : hook ? 'Save' : 'Add hook'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
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
                  onClick={() =>
                    tick(
                      active.events.map((event) => event.verb),
                      true,
                    )
                  }
                >
                  All
                </button>
                <button
                  type="button"
                  className={styles.linkish}
                  onClick={() =>
                    tick(
                      active.events.map((event) => event.verb),
                      false,
                    )
                  }
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

function SecretDialog({
  name,
  secret,
  onClose,
}: {
  name: string
  secret: string
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Modal
      title={`${name}'s signing secret`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</Button>
          <Button variant="go" onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      <ModalBody>
        <p>
          This is the only time it is shown. Give it to whatever receives this hook, so it can check
          each request&rsquo;s <code>X-Cylist-Signature</code> header.
        </p>
        <pre className={styles.secret}>{secret}</pre>
        <p className={styles.quiet}>
          The header is <code>t=&lt;unix time&gt;,v1=&lt;hex&gt;</code>, where the hex is
          HMAC-SHA256 of <code>&lt;t&gt;.&lt;body&gt;</code> under this secret.
        </p>
      </ModalBody>
    </Modal>
  )
}
