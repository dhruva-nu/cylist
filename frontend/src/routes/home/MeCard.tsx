import { useState } from 'react'
import type { Person } from '../../api/client'
import { queryKeys, useIdentity, useInvalidate } from '../../api/queries'
import { PersonDialog } from '../../components/PersonDialog'
import { Avatar, Button, cardStyles } from '../../components/ui'
import styles from './Home.module.css'
import { ChangePasswordDialog, FirstAccountDialog } from './PasswordDialogs'

/**
 * Who you are, on the screen where you start projects.
 *
 * You are whoever signed in, and you are put on every project you create — so
 * this belongs next to the button that creates them, not buried on a project's
 * People tab where you can only reach it once a project exists.
 *
 * It has one other job, and only ever once per deployment. A session with no
 * person behind it is the bootstrap session: somebody signed in with
 * `CYLIST_PASSWORD_HASH` on a deployment that has no accounts yet. The only
 * useful thing to do from there is open the first account, and this card is
 * where that happens — the empty state of "who are you?" turning out to be
 * exactly the right question.
 */
export function MeCard({ onSaved }: { onSaved: (message: string) => void }) {
  const invalidate = useInvalidate()
  const [editing, setEditing] = useState(false)
  const [changingPassword, setChangingPassword] = useState(false)
  const identity = useIdentity()
  const me: Person | null = identity.data?.person ?? null

  async function refresh() {
    await invalidate(queryKeys.identity, queryKeys.people)
  }

  return (
    <>
      {me ? (
        <div className={`${cardStyles.card} ${styles.me}`}>
          <Avatar name={me.name} colour={me.colour} large />
          <div className={styles.meText}>
            <div className={styles.meName}>
              <b>{me.name}</b>
              <span className={styles.youTag}>you</span>
            </div>
            <span className={styles.meRole}>{me.title}</span>
            <span className={styles.meNote}>
              On every project you create, and pickable as an assignee from the moment it exists.
            </span>
          </div>
          <Button onClick={() => setChangingPassword(true)}>Password</Button>
          <Button onClick={() => setEditing(true)}>Edit</Button>
        </div>
      ) : (
        <button
          className={`${cardStyles.card} ${styles.me} ${styles.meEmpty}`}
          onClick={() => setEditing(true)}
        >
          <span className={styles.meText}>
            <b>Who are you?</b>
            <span className={styles.meNote}>
              You are signed in with this deployment's password, which belongs to nobody. Open an
              account and it stops working.
            </span>
          </span>
          <span className={styles.meAdd}>+ Open my account</span>
        </button>
      )}

      {editing && me ? (
        <PersonDialog
          title="This is me"
          person={me}
          onSaved={(name) => onSaved(`${name} saved.`)}
          onDone={refresh}
          onClose={() => setEditing(false)}
        />
      ) : null}

      {editing && !me ? (
        <FirstAccountDialog onDone={refresh} onClose={() => setEditing(false)} />
      ) : null}

      {changingPassword ? (
        <ChangePasswordDialog
          onSaved={() => onSaved('Password changed. Any other session you had is signed out.')}
          onClose={() => setChangingPassword(false)}
        />
      ) : null}
    </>
  )
}
