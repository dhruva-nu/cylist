/** The people on a project, a card each, grouped by whether they are team or client. */

import { isMe, type Identity, type Member, type Person } from '../../api/client'
import {
  Avatar,
  Button,
  EmptyState,
  KindTag,
  MailIcon,
  RoleTag,
  cardStyles,
} from '../../components/ui'
import styles from './ProjectPeople.module.css'

/** One kind of person on the project — the team, or the clients — under a heading. */
export function PeopleGroup({
  title,
  people,
  identity,
  mayRemove,
  onEdit,
  onInvite,
  onOpenAccount,
  onRemove,
}: {
  title: string
  people: Member[]
  identity: Identity | undefined
  mayRemove: boolean
  onEdit: (person: Person) => void
  onInvite: (person: Person) => void
  onOpenAccount: (person: Person) => void
  onRemove: (person: Member) => void
}) {
  return (
    <>
      <div className={styles.section}>
        <h2>{title}</h2>
        <span>{people.length}</span>
      </div>
      {people.length === 0 ? (
        <EmptyState>Nobody here yet.</EmptyState>
      ) : (
        <div className={styles.grid}>
          {people.map((person) => (
            <PersonCard
              key={person.id}
              person={person}
              isYou={isMe(person, identity)}
              mayRemove={mayRemove}
              onEdit={() => onEdit(person)}
              onInvite={() => onInvite(person)}
              onOpenAccount={() => onOpenAccount(person)}
              onRemove={() => onRemove(person)}
            />
          ))}
        </div>
      )}
    </>
  )
}

/**
 * Whether an invitation would do anything for this person. Clients are named
 * on the work, not signed in to it, the agent is a machine and does not sign
 * in at all, and somebody who already has an account has nothing to accept.
 */
function mayBeInvited(person: Person): boolean {
  return person.kind === 'team' && !person.is_agent && !person.has_account && !person.archived_at
}

/** Whether a password can be set for this person by hand. */
function mayHavePasswordSet(person: Person): boolean {
  return person.kind === 'team' && !person.is_agent && !person.archived_at
}

/** One person on the project: who they are, how to reach them, and what can be done about them. */
function PersonCard({
  person,
  isYou,
  mayRemove,
  onEdit,
  onInvite,
  onOpenAccount,
  onRemove,
}: {
  person: Member
  isYou: boolean
  mayRemove: boolean
  onEdit: () => void
  onInvite: () => void
  onOpenAccount: () => void
  onRemove: () => void
}) {
  return (
    <div className={`${cardStyles.card} ${styles.person}`}>
      <Avatar name={person.name} colour={person.colour} large />
      <div className={styles.details}>
        <div className={styles.nameRow}>
          <b>{person.name}</b>
          <KindTag kind={person.kind} />
          {isYou ? <span className={styles.you}>you</span> : null}
          {person.is_agent ? (
            <span
              className={styles.agent}
              title="Cylist's own machine. It is given work like anybody else, and never signs in."
            >
              agent
            </span>
          ) : null}
          {person.role ? <RoleTag role={person.role} /> : null}
          <AccountTag person={person} />
        </div>
        <span className={styles.title}>{person.title}</span>
        <div className={styles.responsibilities}>{person.responsibilities}</div>
        <div className={styles.contact}>
          {person.email ? (
            <span className={styles.email}>
              <MailIcon />
              <span>{person.email}</span>
            </span>
          ) : null}
          <Button variant="ghost" small onClick={onEdit}>
            Edit
          </Button>
          {/* Only offered where it would do something — see `mayBeInvited`. */}
          {mayBeInvited(person) ? (
            <Button variant="ghost" small onClick={onInvite}>
              {person.invite_is_pending ? 'Re-invite' : 'Invite'}
            </Button>
          ) : null}
          {/* The other door, offered on the same row and second: an
              invitation is the one that leaves the password with
              nobody but them, so it reads first. This one is still
              here for somebody with no mailbox you can reach, and is
              the only way back in for somebody who has lost the
              password they already set. */}
          {mayHavePasswordSet(person) ? (
            <Button variant="ghost" small onClick={onOpenAccount}>
              {person.has_account ? 'Reset password' : 'Set a password'}
            </Button>
          ) : null}
          {mayRemove ? (
            <Button variant="ghost" small danger onClick={onRemove}>
              Remove
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/** Whether this person can sign in, said in a word next to their name. */
function AccountTag({ person }: { person: Person }) {
  if (person.has_account) {
    return (
      <span className={styles.account} title="Can sign in as themselves.">
        account
      </span>
    )
  }
  if (person.invite_is_pending) {
    return (
      <span className={styles.invited} title="Invited; has not set a password yet.">
        invited
      </span>
    )
  }
  return null
}
