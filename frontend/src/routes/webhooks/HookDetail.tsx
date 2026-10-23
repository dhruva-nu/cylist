/** One hook: what it fires on, where it sends, and the log of what it sent. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, type Hook, type HookDelivery, type HookEvent } from '../../api/client'
import { Button, ErrorNotice, cardStyles } from '../../components/ui'
import styles from './ProjectHooks.module.css'
import { STATE_WORDS, attemptTime, deliveryOutcome, describeRule, isDangling } from './projectHooks'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { queryKeys } from '../../api/queries'

/** How long after a redelivery is queued the log looks again: the courier sends it within moments. */
const REDELIVERY_SETTLES_MS = 2_000

export function HookDetail({
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
  const [message, setMessage] = useState('')

  const deliveries = useQuery({
    queryKey: queryKeys.hookDeliveries(hook.id),
    queryFn: ({ signal }) => api.listHookDeliveries(projectKey, hook.id, signal),
    // Retries happen in the background; a log left open should show them land.
    refetchInterval: 15_000,
  })

  async function refreshLog() {
    await queryClient.invalidateQueries({ queryKey: queryKeys.hookDeliveries(hook.id) })
  }

  // The second look after a redelivery, cancelled if the pane goes first —
  // it is this hook's log it would refresh, and this hook may be gone.
  const lookAgain = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(lookAgain.current), [])

  const toggle = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (enabled: boolean) => api.updateHook(projectKey, hook.id, { enabled }),
    onSuccess: (saved) => onChanged(`${saved.name} is ${saved.enabled ? 'on' : 'off'}.`),
  })
  const test = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.testHook(projectKey, hook.id),
    onSuccess: async (sent) => {
      await refreshLog()
      await onChanged(`Test sent to ${hook.name}: ${deliveryOutcome(sent)}.`)
    },
  })
  const fire = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.fireHook(projectKey, hook.id, message),
    onSuccess: async (sent) => {
      setMessage('')
      await refreshLog()
      await onChanged(`${hook.name} fired: ${deliveryOutcome(sent)}.`)
    },
  })
  const rotate = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.rotateHookSecret(projectKey, hook.id),
    // The new secret is shown once, by the dialog it is handed to; it is not
    // kept in react-query's cache for the minutes after.
    gcTime: 0,
    onSuccess: async (rotated) => {
      onSecret(rotated.secret)
      await onChanged(`${hook.name} has a new signing secret.`)
    },
  })
  const remove = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.deleteHook(projectKey, hook.id),
    onSuccess: () => onChanged(`${hook.name} deleted.`),
  })
  const redeliver = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (delivery: HookDelivery) => api.redeliverHook(projectKey, hook.id, delivery.id),
    onSuccess: async () => {
      await refreshLog()
      window.clearTimeout(lookAgain.current)
      lookAgain.current = window.setTimeout(() => void refreshLog(), REDELIVERY_SETTLES_MS)
    },
  })

  const failure =
    toggle.error ?? test.error ?? fire.error ?? rotate.error ?? remove.error ?? redeliver.error

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
      <ErrorNotice error={failure} />

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

      {hook.manual ? (
        <form
          className={styles.fire}
          onSubmit={(event) => {
            event.preventDefault()
            fire.mutate()
          }}
        >
          <input
            value={message}
            maxLength={2000}
            placeholder="A message for the receiver (optional)"
            aria-label="Message"
            disabled={!hook.enabled}
            onChange={(event) => setMessage(event.target.value)}
          />
          <Button
            type="submit"
            variant="go"
            disabled={!hook.enabled || fire.isPending}
            title={hook.enabled ? undefined : 'Switch it on to fire it.'}
          >
            {fire.isPending ? 'Firing…' : 'Fire now'}
          </Button>
        </form>
      ) : null}

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
        <ErrorNotice error={deliveries.error} />
      ) : deliveries.data.length === 0 ? (
        <p className={styles.quiet}>
          {hook.manual
            ? 'Nothing sent yet. Fire it, or send a test.'
            : 'Nothing sent yet. Send a test, or make a change this hook matches.'}
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
