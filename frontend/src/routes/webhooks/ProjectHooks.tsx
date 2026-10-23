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

import { useQuery } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { ApiError, api, type Hook } from '../../api/client'
import { queryKeys, useBoard, useInvalidate, useMembers, useTemplates } from '../../api/queries'
import { PageHead } from '../../components/Shell'
import { Button, EmptyState, ErrorNotice, LiveRegion, useAnnouncer } from '../../components/ui'
import { HookDetail } from './HookDetail'
import { HookDialog } from './HookDialog'
import styles from './ProjectHooks.module.css'
import { SecretDialog } from './SecretDialog'

/** Whether the server refused because the reader is not this project's admin. */
function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'forbidden'
}

export function ProjectHooks() {
  const { projectKey } = useParams({ from: '/p/$projectKey/hooks' })
  const invalidate = useInvalidate()
  const { message, announce } = useAnnouncer()
  const [chosenId, setChosenId] = useState<string | null>(null)
  const [editing, setEditing] = useState<Hook | 'new' | null>(null)
  const [shownSecret, setShownSecret] = useState<{ name: string; secret: string } | null>(null)

  const hooks = useQuery({
    queryKey: ['hooks', projectKey],
    queryFn: ({ signal }) => api.listHooks(projectKey, signal),
    // A refusal is an answer about who is asking; asking again changes nothing.
    retry: (count, error) => !isForbidden(error) && count < 2,
  })
  const events = useQuery({
    queryKey: queryKeys.hookEvents,
    queryFn: ({ signal }) => api.listHookEvents(signal),
  })
  const board = useBoard(projectKey)
  const templates = useTemplates(projectKey)
  const members = useMembers(projectKey)

  async function refresh() {
    await invalidate(['hooks', projectKey])
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
      POSTed as signed JSON, and tried again for hours if nobody answers. A manual hook sends
      nothing on its own: it fires when you press its button.
    </PageHead>
  )

  if (hooks.isPending) return <EmptyState>Loading hooks…</EmptyState>
  if (hooks.error) {
    return (
      <>
        {head}
        {isForbidden(hooks.error) ? (
          <p className={styles.note}>
            Only an admin of {projectKey} can see or change its hooks. Ask one to add you to the
            Admin role on the Roles page.
          </p>
        ) : (
          <ErrorNotice error={hooks.error} />
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
      {selected === undefined ? (
        <EmptyState>
          No hooks yet. Add one to have a change on this board — a card moved into a column, say —
          POSTed to a URL you choose.
        </EmptyState>
      ) : (
        <div className={styles.split}>
          <HookList hooks={hooks.data} selectedId={selected.id} onSelect={setChosenId} />
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
          members={members.data?.members ?? []}
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

/** The hooks down the left, each with a dot for how its last delivery went. */
function HookList({
  hooks,
  selectedId,
  onSelect,
}: {
  hooks: Hook[]
  selectedId: string
  onSelect: (hookId: string) => void
}) {
  return (
    <nav className={styles.list} aria-label="Hooks">
      {hooks.map((hook) => (
        <button
          key={hook.id}
          type="button"
          className={`${styles.listRow} ${hook.id === selectedId ? styles.selected : ''}`}
          aria-current={hook.id === selectedId ? 'true' : undefined}
          onClick={() => onSelect(hook.id)}
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
  )
}
